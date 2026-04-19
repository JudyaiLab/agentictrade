"""
Agent Provider API routes.

Registration, activation, suspension, and management of AI agents
acting as service providers on the marketplace.

Security: All endpoints enforce ownership — only the provider's owner
or an admin can access sensitive data.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.deps import extract_owner, require_admin

router = APIRouter(prefix="/agent-providers", tags=["agent-providers"])


# --- Request models ---

class RegisterProviderRequest(BaseModel):
    agent_id: str
    owner_email: str
    wallet_address: str
    did: str
    declaration: str = ""


class SuspendProviderRequest(BaseModel):
    reason: str


class AppealRequest(BaseModel):
    reason: str


# --- Ownership helpers ---

def _require_owner_or_admin(
    provider: dict, owner_id: str, key_record: dict, db,
) -> None:
    """Raise 403 if caller is neither the owner nor admin."""
    if key_record["role"] == "admin":
        return
    # Check if the agent identity belongs to this caller
    agent_id = provider.get("agent_id", "")
    if agent_id:
        agent = db.get_agent(agent_id)
        if agent and agent.get("owner_id") == owner_id:
            return
    raise HTTPException(status_code=403, detail="Access denied — not your provider")


def _provider_response(provider: dict, is_owner: bool = False) -> dict:
    """Serialize agent provider record for API response.

    PII (owner_email, wallet_address) is only included for the owner or admin.
    """
    resp = {
        "id": provider.get("id", ""),
        "agent_id": provider.get("agent_id", ""),
        "did": provider.get("did", ""),
        "declaration": provider.get("declaration", ""),
        "status": provider.get("status", ""),
        "reputation_score": provider.get("reputation_score", 0.0),
        "fast_track_eligible": bool(provider.get("fast_track_eligible", False)),
        "daily_tx_cap": provider.get("daily_tx_cap", 500.0),
        "probation_ends_at": provider.get("probation_ends_at", ""),
        "total_reports": provider.get("total_reports", 0),
        "created_at": provider.get("created_at", ""),
        "updated_at": provider.get("updated_at", ""),
    }
    if is_owner:
        resp["owner_email"] = provider.get("owner_email", "")
        resp["wallet_address"] = provider.get("wallet_address", "")
    return resp


def _is_owner_or_admin(provider: dict, owner_id: str, key_record: dict, db) -> bool:
    """Check ownership without raising."""
    if key_record["role"] == "admin":
        return True
    agent_id = provider.get("agent_id", "")
    if agent_id:
        agent = db.get_agent(agent_id)
        if agent and agent.get("owner_id") == owner_id:
            return True
    return False


# --- Routes ---

@router.post("", status_code=201)
async def register_agent_provider(req: RegisterProviderRequest, request: Request):
    """Register an AI agent as a service provider.

    Requires a valid agent identity that is active. The agent will
    start in 'pending_review' status with a 30-day probation period
    and $500/day transaction cap.
    """
    from marketplace.agent_provider import AgentProviderError

    owner_id, key_record = extract_owner(request)
    db = request.app.state.db
    mgr = _get_manager(request)

    # Verify the caller owns the agent they're registering
    agent = db.get_agent(req.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.get("owner_id") != owner_id and key_record["role"] != "admin":
        raise HTTPException(status_code=403, detail="You do not own this agent")

    try:
        provider = mgr.register(
            agent_id=req.agent_id,
            owner_email=req.owner_email,
            wallet_address=req.wallet_address,
            did=req.did,
            declaration=req.declaration,
        )
        return _provider_response(provider, is_owner=True)
    except AgentProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
async def list_agent_providers(
    request: Request,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """List agent providers. Admin only."""
    require_admin(request)
    db = request.app.state.db

    limit = min(max(limit, 1), 100)
    providers = db.list_agent_providers(status=status, limit=limit, offset=offset)
    return {
        "providers": [_provider_response(p, is_owner=True) for p in providers],
        "count": len(providers),
    }


@router.get("/{provider_id}")
async def get_agent_provider(provider_id: str, request: Request):
    """Get agent provider details. PII only visible to owner/admin."""
    owner_id, key_record = extract_owner(request)
    db = request.app.state.db

    provider = db.get_agent_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Agent provider not found")

    is_owner = _is_owner_or_admin(provider, owner_id, key_record, db)
    return _provider_response(provider, is_owner=is_owner)


@router.get("/by-agent/{agent_id}")
async def get_provider_by_agent(agent_id: str, request: Request):
    """Look up agent provider by agent identity ID. PII only visible to owner/admin."""
    owner_id, key_record = extract_owner(request)
    db = request.app.state.db
    mgr = _get_manager(request)

    provider = mgr.get_by_agent_id(agent_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Agent provider not found for this agent")

    is_owner = _is_owner_or_admin(provider, owner_id, key_record, db)
    return _provider_response(provider, is_owner=is_owner)


# --- Admin routes ---

@router.post("/{provider_id}/activate")
async def activate_provider(provider_id: str, request: Request):
    """Activate an agent provider after review. Admin only."""
    from marketplace.agent_provider import AgentProviderError

    require_admin(request)
    mgr = _get_manager(request)

    try:
        provider = mgr.activate(provider_id)
        await _dispatch_event(request, "provider.activated", {
            "provider_id": provider.get("id", ""),
            "agent_id": provider.get("agent_id", ""),
        })
        return _provider_response(provider, is_owner=True)
    except AgentProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{provider_id}/suspend")
async def suspend_provider(
    provider_id: str, req: SuspendProviderRequest, request: Request,
):
    """Suspend an agent provider. Admin only."""
    from marketplace.agent_provider import AgentProviderError

    require_admin(request)
    mgr = _get_manager(request)

    try:
        provider = mgr.suspend(provider_id, req.reason)
        await _dispatch_event(request, "provider.suspended", {
            "provider_id": provider.get("id", ""),
            "agent_id": provider.get("agent_id", ""),
            "reason": req.reason,
        })
        return _provider_response(provider, is_owner=True)
    except AgentProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{provider_id}/appeal")
async def appeal_suspension(
    provider_id: str, req: AppealRequest, request: Request,
):
    """Appeal a provider suspension. Owner only.

    Changes status from 'suspended' to 'pending_review' for admin re-evaluation.
    """
    from marketplace.agent_provider import AgentProviderError

    owner_id, key_record = extract_owner(request)
    db = request.app.state.db

    provider = db.get_agent_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Agent provider not found")
    _require_owner_or_admin(provider, owner_id, key_record, db)

    mgr = _get_manager(request)
    try:
        updated = mgr.appeal_suspension(provider_id, req.reason)
        return _provider_response(updated, is_owner=True)
    except AgentProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{provider_id}/fast-track")
async def check_fast_track(provider_id: str, request: Request):
    """Check if a provider is eligible for fast-track review. Owner or admin only."""
    from marketplace.agent_provider import AgentProviderError

    owner_id, key_record = extract_owner(request)
    db = request.app.state.db

    provider = db.get_agent_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Agent provider not found")
    _require_owner_or_admin(provider, owner_id, key_record, db)

    mgr = _get_manager(request)
    try:
        eligible = mgr.is_fast_track_eligible(provider_id)
        return {"provider_id": provider_id, "fast_track_eligible": eligible}
    except AgentProviderError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{provider_id}/tx-limit")
async def check_tx_limit(
    provider_id: str, request: Request, amount: float = 0.0,
):
    """Check if a transaction amount is within the provider's daily limit. Owner or admin only."""
    from marketplace.agent_provider import AgentProviderError

    owner_id, key_record = extract_owner(request)
    db = request.app.state.db

    provider = db.get_agent_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Agent provider not found")
    _require_owner_or_admin(provider, owner_id, key_record, db)

    mgr = _get_manager(request)
    try:
        allowed = mgr.check_daily_tx_limit(provider_id, amount)
        return {
            "provider_id": provider_id,
            "amount": amount,
            "allowed": allowed,
        }
    except AgentProviderError as e:
        raise HTTPException(status_code=404, detail=str(e))


# --- Helpers ---

async def _dispatch_event(request: Request, event: str, payload: dict) -> None:
    """Fire webhook event if webhook manager is available."""
    webhook_mgr = getattr(request.app.state, "webhooks", None)
    if webhook_mgr:
        try:
            await webhook_mgr.dispatch(event, payload)
        except Exception:
            pass  # Webhook delivery is best-effort


def _get_manager(request: Request):
    return request.app.state.agent_provider_mgr
