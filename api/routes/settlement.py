"""
Settlement API routes — provider payout management.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from marketplace.settlement import SettlementError
from api.deps import extract_owner as _extract_owner

router = APIRouter(tags=["settlement"])


class CreateSettlementRequest(BaseModel):
    provider_id: str
    period_start: str  # ISO 8601 UTC (e.g. "2026-03-01T00:00:00+00:00")
    period_end: str    # ISO 8601 UTC


class MarkPaidRequest(BaseModel):
    payment_tx: str


@router.post("/settlements", status_code=201)
async def create_settlement(req: CreateSettlementRequest, request: Request):
    """Create a settlement for a provider (admin only)."""
    owner_id, record = _extract_owner(request)
    if record["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    # Normalize period boundaries to UTC ISO format for consistent comparisons
    from datetime import datetime as _dt, timezone as _tz
    try:
        ps = _dt.fromisoformat(req.period_start)
        pe = _dt.fromisoformat(req.period_end)
        # Convert timezone-aware datetimes to UTC; assume UTC if naive
        if ps.tzinfo is not None:
            ps = ps.astimezone(_tz.utc)
        if pe.tzinfo is not None:
            pe = pe.astimezone(_tz.utc)
        period_start_utc = ps.isoformat()
        period_end_utc = pe.isoformat()
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid ISO 8601 date format for period_start/period_end")

    engine = request.app.state.settlement
    try:
        result = engine.create_settlement(
            provider_id=req.provider_id,
            period_start=period_start_utc,
            period_end=period_end_utc,
        )

        # Check milestones after settlement
        milestones_awarded = []
        try:
            from marketplace.milestones import MilestoneTracker
            tracker = MilestoneTracker(request.app.state.db)
            newly = tracker.check_and_award(req.provider_id)
            if any(m["milestone_type"] == "cashback" for m in newly):
                tracker.apply_cashback(req.provider_id)
            milestones_awarded = [m["milestone_type"] for m in newly]
        except Exception:
            pass  # Milestone check is best-effort

        return {
            "id": result["id"],
            "provider_id": result["provider_id"],
            "total_amount": str(result["total_amount"]),
            "platform_fee": str(result["platform_fee"]),
            "net_amount": str(result["net_amount"]),
            "call_count": result["call_count"],
            "status": result["status"],
            "milestones_awarded": milestones_awarded,
        }
    except SettlementError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/settlements")
async def list_settlements(
    request: Request,
    provider_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
):
    """List settlements. Providers see only their own; admins see all."""
    owner_id, record = _extract_owner(request)
    limit = min(max(limit, 1), 100)

    engine = request.app.state.settlement
    # Non-admin can only see their own settlements
    if record["role"] != "admin":
        provider_id = owner_id

    results = engine.list_settlements(
        provider_id=provider_id,
        status=status,
        limit=limit,
    )
    return {
        "settlements": [
            {
                "id": s["id"],
                "provider_id": s["provider_id"],
                "period_start": s["period_start"],
                "period_end": s["period_end"],
                "total_amount": str(s["total_amount"]),
                "platform_fee": str(s["platform_fee"]),
                "net_amount": str(s["net_amount"]),
                "status": s["status"],
                "payment_tx": s["payment_tx"],
            }
            for s in results
        ],
        "count": len(results),
    }


@router.patch("/settlements/{settlement_id}/pay")
async def mark_settlement_paid(
    settlement_id: str,
    req: MarkPaidRequest,
    request: Request,
):
    """Mark a settlement as paid (admin only)."""
    _, record = _extract_owner(request)
    if record["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    engine = request.app.state.settlement
    success = engine.mark_paid(settlement_id, req.payment_tx)
    if not success:
        raise HTTPException(
            status_code=404,
            detail="Settlement not found or already paid",
        )
    return {"status": "completed", "payment_tx": req.payment_tx}


@router.post("/admin/settlements/recover")
async def recover_stuck_settlements(request: Request, timeout_hours: int = 24):
    """Recover settlements stuck in 'processing' state (admin only).

    Finds settlements that have been in 'processing' longer than timeout_hours
    and moves them to 'failed' with an auto-recovery note.
    """
    _, record = _extract_owner(request)
    if record["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    engine = request.app.state.settlement
    recovered = engine.recover_stuck_settlements(timeout_hours=timeout_hours)
    return {
        "recovered_count": len(recovered),
        "recovered": recovered,
        "timeout_hours": timeout_hours,
    }


@router.post("/admin/settlements/retry-failed")
async def retry_failed_settlements(request: Request, max_attempts: int = 3):
    """Re-queue failed settlements for automatic payout retry (admin only).

    Moves eligible 'failed' settlements back to 'pending' state,
    up to max_attempts per settlement.
    """
    _, record = _extract_owner(request)
    if record["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    engine = request.app.state.settlement
    retried = engine.retry_failed_settlements(max_attempts=max_attempts)
    return {
        "retried_count": len(retried),
        "retried": retried,
        "max_attempts": max_attempts,
    }
