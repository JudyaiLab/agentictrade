"""
Escrow API routes.

Manage payment holds for Agent Provider transactions.
Tiered hold periods with structured dispute evidence,
provider counter-responses, and admin arbitration.

Security: Holds are scoped — buyers see their own holds, providers see
holds for their services, admins see everything.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import extract_owner, require_admin

router = APIRouter(prefix="/escrow", tags=["escrow"])


# --- Request models ---

class CreateHoldRequest(BaseModel):
    provider_id: str
    service_id: str
    amount: float
    usage_record_id: str
    currency: str = "USDC"


class RefundHoldRequest(BaseModel):
    reason: str = ""


class DisputeRequest(BaseModel):
    """Structured dispute submission with evidence."""
    reason: str = Field(default="", description="Description of the issue")
    category: str = Field(
        default="other",
        description="Dispute category: service_not_delivered, quality_issue, "
        "unauthorized_charge, wrong_output, timeout_or_error, other",
    )
    evidence_urls: list[str] = Field(
        default_factory=list,
        description="URLs to supporting evidence (screenshots, logs, etc.)",
    )


class DisputeResponseRequest(BaseModel):
    """Provider counter-response to a dispute."""
    description: str = Field(..., description="Provider's response/explanation")
    evidence_urls: list[str] = Field(
        default_factory=list,
        description="URLs to counter-evidence",
    )


class ResolveDisputeRequest(BaseModel):
    """Admin dispute resolution."""
    outcome: str = Field(
        ...,
        description="Resolution: refund_buyer, release_to_provider, or partial_refund",
    )
    note: str = Field(default="", description="Admin note explaining the resolution")
    refund_amount: Optional[float] = Field(
        default=None,
        description="Amount to refund for partial_refund outcome (required when outcome=partial_refund)",
    )


# --- Ownership helpers ---

def _can_access_hold(hold: dict, owner_id: str, role: str) -> bool:
    """Check if caller can access this escrow hold."""
    if role == "admin":
        return True
    # Buyer can see their own holds
    if hold.get("buyer_id") == owner_id:
        return True
    # Provider can see holds on their services
    if hold.get("provider_id") == owner_id:
        return True
    return False


def _audit_escrow(request: Request, event_type: str, actor: str, target: str, details: str) -> None:
    """Log an escrow event to the audit trail. Best-effort (never raises)."""
    try:
        from marketplace.audit import AuditLogger
        db_path = getattr(request.app.state, "db", None)
        if db_path and hasattr(db_path, "db_path"):
            logger = AuditLogger(db_path.db_path)
        else:
            logger = AuditLogger()
        ip = request.client.host if request.client else ""
        logger.log_event(event_type, actor=actor, target=target, details=details, ip_address=ip)
    except Exception:
        pass  # Audit is best-effort; don't break escrow operations


# --- Routes ---

@router.post("/holds", status_code=201)
async def create_escrow_hold(req: CreateHoldRequest, request: Request):
    """Create an escrow hold for an agent provider transaction.

    Payment is held for 7 days before automatic release.
    buyer_id is automatically set to the authenticated caller.
    """
    from marketplace.escrow import EscrowError

    owner_id, _ = extract_owner(request)
    mgr = _get_manager(request)

    try:
        hold = mgr.create_hold(
            provider_id=req.provider_id,
            service_id=req.service_id,
            buyer_id=owner_id,  # Always the authenticated caller
            amount=req.amount,
            usage_record_id=req.usage_record_id,
            currency=req.currency,
        )
        _audit_escrow(
            request, "escrow_created", actor=owner_id,
            target=hold["id"],
            details=f"amount={req.amount} {req.currency}, provider={req.provider_id}",
        )
        return _hold_response(hold)
    except EscrowError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/holds/{hold_id}")
async def get_escrow_hold(hold_id: str, request: Request):
    """Get details of a specific escrow hold. Only buyer, provider, or admin."""
    owner_id, key_record = extract_owner(request)
    db = request.app.state.db

    hold = db.get_escrow_hold(hold_id)
    if not hold:
        raise HTTPException(status_code=404, detail="Escrow hold not found")

    if not _can_access_hold(hold, owner_id, key_record["role"]):
        raise HTTPException(status_code=403, detail="Access denied")
    return _hold_response(hold)


@router.get("/holds")
async def list_escrow_holds(
    request: Request,
    provider_id: Optional[str] = None,
    status: Optional[str] = None,
):
    """List escrow holds. Non-admins can only see their own holds."""
    owner_id, key_record = extract_owner(request)
    db = request.app.state.db

    if key_record["role"] == "admin":
        holds = db.list_escrow_holds(provider_id=provider_id, status=status)
    else:
        # Non-admin: only return holds where caller is buyer or provider
        all_holds = db.list_escrow_holds(provider_id=provider_id, status=status)
        holds = [
            h for h in all_holds
            if h.get("buyer_id") == owner_id or h.get("provider_id") == owner_id
        ]

    return {
        "holds": [_hold_response(h) for h in holds],
        "count": len(holds),
    }


@router.post("/holds/{hold_id}/release")
async def release_escrow(hold_id: str, request: Request):
    """Manually release an escrow hold. Admin only."""
    from marketplace.escrow import EscrowError

    require_admin(request)
    mgr = _get_manager(request)

    try:
        hold = mgr.release_hold(hold_id)
        _audit_escrow(
            request, "escrow_released", actor="admin",
            target=hold_id,
            details=f"amount={hold.get('amount', 0)} {hold.get('currency', 'USDC')}, provider={hold.get('provider_id', '')}",
        )
        await _dispatch_event(request, "escrow.released", {
            "hold_id": hold["id"],
            "provider_id": hold.get("provider_id", ""),
            "amount": hold.get("amount", 0),
            "currency": hold.get("currency", "USDC"),
        })
        return _hold_response(hold)
    except EscrowError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/holds/{hold_id}/refund")
async def refund_escrow(
    hold_id: str, req: RefundHoldRequest, request: Request,
):
    """Refund an escrow hold back to the buyer. Admin only."""
    from marketplace.escrow import EscrowError

    require_admin(request)
    mgr = _get_manager(request)

    try:
        hold = mgr.refund_hold(hold_id, reason=req.reason)
        _audit_escrow(
            request, "escrow_released", actor="admin",
            target=hold_id,
            details=f"refund, reason={req.reason}, amount={hold.get('amount', 0)}",
        )
        return _hold_response(hold)
    except EscrowError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/holds/{hold_id}/dispute")
async def dispute_escrow(
    hold_id: str,
    req: DisputeRequest,
    request: Request,
):
    """Open a structured dispute on an escrow hold.

    Only the buyer of this hold (or admin) can dispute.
    Accepts a reason, category, and evidence URLs.
    Dispute timeout scales with transaction amount:
    <$1 = 24h, $1-$100 = 72h, $100+ = 7 days.
    """
    from marketplace.escrow import EscrowError

    owner_id, key_record = extract_owner(request)
    db = request.app.state.db

    hold = db.get_escrow_hold(hold_id)
    if not hold:
        raise HTTPException(status_code=404, detail="Escrow hold not found")

    # Only the buyer or admin can dispute
    if hold.get("buyer_id") != owner_id and key_record["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only the buyer can dispute this hold")

    mgr = _get_manager(request)
    try:
        updated = mgr.dispute_hold(
            hold_id,
            reason=req.reason,
            category=req.category,
            evidence_urls=req.evidence_urls,
            submitted_by=owner_id,
        )
        _audit_escrow(
            request, "escrow_disputed", actor=owner_id,
            target=hold_id,
            details=f"category={req.category}, amount={updated.get('amount', 0)}",
        )
        await _dispatch_event(request, "escrow.dispute_opened", {
            "hold_id": updated["id"],
            "provider_id": updated.get("provider_id", ""),
            "buyer_id": updated.get("buyer_id", ""),
            "amount": updated.get("amount", 0),
            "category": req.category,
        })
        return _hold_response(updated)
    except EscrowError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/holds/{hold_id}/dispute/respond")
async def respond_to_dispute(
    hold_id: str,
    req: DisputeResponseRequest,
    request: Request,
):
    """Provider submits a counter-response to a dispute.

    Only the provider of the disputed hold (or admin) can respond.
    """
    from marketplace.escrow import EscrowError

    owner_id, key_record = extract_owner(request)
    db = request.app.state.db

    hold = db.get_escrow_hold(hold_id)
    if not hold:
        raise HTTPException(status_code=404, detail="Escrow hold not found")

    if hold.get("provider_id") != owner_id and key_record["role"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only the provider can respond to this dispute",
        )

    mgr = _get_manager(request)
    try:
        updated = mgr.respond_to_dispute(
            hold_id,
            responder_id=owner_id,
            description=req.description,
            evidence_urls=req.evidence_urls,
        )
        return _hold_response(updated)
    except EscrowError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/holds/{hold_id}/dispute/resolve")
async def resolve_dispute(
    hold_id: str,
    req: ResolveDisputeRequest,
    request: Request,
):
    """Admin resolves a dispute with a binding outcome.

    Outcomes: refund_buyer, release_to_provider, or partial_refund.
    """
    from marketplace.escrow import EscrowError

    require_admin(request)
    mgr = _get_manager(request)

    try:
        updated = mgr.resolve_dispute(
            hold_id,
            outcome=req.outcome,
            note=req.note,
            refund_amount=req.refund_amount,
        )
        refund_detail = f", refund_amount={req.refund_amount}" if req.refund_amount else ""
        _audit_escrow(
            request, "escrow_resolved", actor="admin",
            target=hold_id,
            details=f"outcome={req.outcome}{refund_detail}, amount={updated.get('amount', 0)}",
        )
        event_name = (
            "escrow.released" if req.outcome == "release_to_provider"
            else "escrow.refunded"
        )
        await _dispatch_event(request, event_name, {
            "hold_id": updated["id"],
            "provider_id": updated.get("provider_id", ""),
            "amount": updated.get("amount", 0),
            "resolution": req.outcome,
        })
        return _hold_response(updated)
    except EscrowError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/holds/{hold_id}/dispute/evidence")
async def get_dispute_evidence(hold_id: str, request: Request):
    """View all evidence submissions for a disputed hold.

    Buyer, provider, or admin can view evidence.
    """
    owner_id, key_record = extract_owner(request)
    db = request.app.state.db

    hold = db.get_escrow_hold(hold_id)
    if not hold:
        raise HTTPException(status_code=404, detail="Escrow hold not found")

    if not _can_access_hold(hold, owner_id, key_record["role"]):
        raise HTTPException(status_code=403, detail="Access denied")

    mgr = _get_manager(request)
    evidence = mgr.get_dispute_evidence(hold_id)
    return {"hold_id": hold_id, "evidence": evidence, "count": len(evidence)}


@router.get("/disputes")
async def list_disputes(request: Request):
    """List all disputed escrow holds. Admin only."""
    require_admin(request)
    db = request.app.state.db

    disputes = db.list_escrow_holds(status="disputed")
    return {
        "disputes": [_hold_response(h) for h in disputes],
        "count": len(disputes),
    }


@router.get("/providers/{provider_id}/summary")
async def provider_escrow_summary(provider_id: str, request: Request):
    """Get escrow summary for a specific provider. Owner or admin only."""
    owner_id, key_record = extract_owner(request)

    # Only the provider themselves or admin
    if provider_id != owner_id and key_record["role"] != "admin":
        raise HTTPException(status_code=403, detail="Access denied — not your provider")

    mgr = _get_manager(request)
    return mgr.get_provider_escrow_summary(provider_id)


@router.post("/process-releasable")
async def process_releasable(request: Request):
    """Process all escrow holds that are past their release date. Admin/cron only."""
    require_admin(request)
    mgr = _get_manager(request)
    released = mgr.process_releasable()
    for hold in released:
        await _dispatch_event(request, "escrow.released", {
            "hold_id": hold["id"],
            "provider_id": hold.get("provider_id", ""),
            "amount": hold.get("amount", 0),
            "currency": hold.get("currency", "USDC"),
        })
    return {
        "released_count": len(released),
        "released": [_hold_response(h) for h in released],
    }


# --- Helpers ---

def _get_manager(request: Request):
    return request.app.state.escrow_mgr


async def _dispatch_event(request: Request, event: str, payload: dict) -> None:
    """Fire webhook event if webhook manager is available."""
    webhook_mgr = getattr(request.app.state, "webhooks", None)
    if webhook_mgr:
        try:
            await webhook_mgr.dispatch(event, payload)
        except Exception:
            pass  # Webhook delivery is best-effort


def _hold_response(hold: dict) -> dict:
    """Serialize escrow hold record for API response."""
    resp = {
        "id": hold.get("id", ""),
        "provider_id": hold.get("provider_id", ""),
        "service_id": hold.get("service_id", ""),
        "buyer_id": hold.get("buyer_id", ""),
        "amount": hold.get("amount", 0),
        "currency": hold.get("currency", "USDC"),
        "status": hold.get("status", ""),
        "usage_record_id": hold.get("usage_record_id", ""),
        "held_at": hold.get("held_at", ""),
        "release_at": hold.get("release_at", ""),
        "released_at": hold.get("released_at"),
        "created_at": hold.get("created_at", ""),
    }
    # Include dispute fields when present
    if hold.get("dispute_reason") is not None:
        resp["dispute_reason"] = hold.get("dispute_reason", "")
    if hold.get("dispute_category") is not None:
        resp["dispute_category"] = hold.get("dispute_category", "")
    if hold.get("dispute_timeout_at") is not None:
        resp["dispute_timeout_at"] = hold.get("dispute_timeout_at", "")
    if hold.get("resolved_at") is not None:
        resp["resolved_at"] = hold.get("resolved_at", "")
    if hold.get("resolution_outcome") is not None:
        resp["resolution_outcome"] = hold.get("resolution_outcome", "")
    if hold.get("resolution_note") is not None:
        resp["resolution_note"] = hold.get("resolution_note", "")
    if hold.get("refund_amount") is not None:
        resp["refund_amount"] = hold.get("refund_amount")
    if hold.get("provider_payout") is not None:
        resp["provider_payout"] = hold.get("provider_payout")
    return resp
