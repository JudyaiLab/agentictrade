"""
Agent Identity API routes.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.deps import extract_owner

router = APIRouter(tags=["agents"])


# --- Request models ---

class RegisterAgentRequest(BaseModel):
    display_name: str
    identity_type: str = "api_key_only"
    capabilities: list[str] = []
    wallet_address: Optional[str] = None
    metadata: dict = {}


class UpdateAgentRequest(BaseModel):
    display_name: Optional[str] = None
    capabilities: Optional[list[str]] = None
    wallet_address: Optional[str] = None
    status: Optional[str] = None
    metadata: Optional[dict] = None


# --- Routes ---

@router.post("/agents", status_code=201)
async def register_agent(req: RegisterAgentRequest, request: Request):
    """Register a new agent identity. Requires API key."""
    from marketplace.identity import IdentityError

    owner_id, _ = extract_owner(request)
    identity_mgr = request.app.state.identity

    try:
        agent = identity_mgr.register(
            display_name=req.display_name,
            owner_id=owner_id,
            identity_type=req.identity_type,
            capabilities=req.capabilities,
            wallet_address=req.wallet_address,
            metadata=req.metadata,
        )
        return _agent_response(agent, include_owner=True)
    except IdentityError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/agents")
async def list_agents(
    request: Request,
    status: str = "active",
    limit: int = 50,
    offset: int = 0,
):
    """List agents. No auth required."""
    limit = min(max(limit, 1), 100)
    identity_mgr = request.app.state.identity
    agents = identity_mgr.list_agents(status=status, limit=limit, offset=offset)
    return {
        "agents": [_agent_response(a) for a in agents],
        "count": len(agents),
    }


@router.get("/agents/search")
async def search_agents(request: Request, q: str = "", limit: int = 20):
    """Search agents by name or ID."""
    limit = min(max(limit, 1), 100)
    identity_mgr = request.app.state.identity
    agents = identity_mgr.search(query=q, limit=limit)
    return {
        "agents": [_agent_response(a) for a in agents],
        "count": len(agents),
    }


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    """Get agent details. No auth required."""
    identity_mgr = request.app.state.identity
    agent = identity_mgr.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _agent_response(agent)


@router.patch("/agents/{agent_id}")
async def update_agent(
    agent_id: str, req: UpdateAgentRequest, request: Request
):
    """Update an agent (owner only). Requires API key."""
    from marketplace.identity import IdentityError

    owner_id, _ = extract_owner(request)
    identity_mgr = request.app.state.identity
    updates = req.model_dump(exclude_none=True)

    try:
        agent = identity_mgr.update(agent_id, owner_id, **updates)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return _agent_response(agent, include_owner=True)
    except IdentityError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/agents/{agent_id}")
async def deactivate_agent(agent_id: str, request: Request):
    """Deactivate an agent (soft delete, owner only). Requires API key."""
    from marketplace.identity import IdentityError

    owner_id, _ = extract_owner(request)
    identity_mgr = request.app.state.identity
    try:
        removed = identity_mgr.deactivate(agent_id, owner_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"status": "deactivated", "agent_id": agent_id}
    except IdentityError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/agents/{agent_id}/verify")
async def verify_agent(agent_id: str, request: Request):
    """Mark an agent as verified (admin only). Requires admin API key."""
    _, key_record = extract_owner(request)
    if key_record["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    identity_mgr = request.app.state.identity
    agent = identity_mgr.verify(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _agent_response(agent)


# --- Helpers ---

def _agent_response(agent, include_owner: bool = False) -> dict:
    resp = {
        "agent_id": agent.agent_id,
        "display_name": agent.display_name,
        "identity_type": agent.identity_type,
        "capabilities": list(agent.capabilities),
        "wallet_address": agent.wallet_address,
        "verified": agent.verified,
        "reputation_score": agent.reputation_score,
        "status": agent.status,
        "created_at": agent.created_at.isoformat(),
        "updated_at": agent.updated_at.isoformat(),
    }
    if include_owner:
        resp["owner_id"] = agent.owner_id
    return resp
