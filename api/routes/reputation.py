"""
Reputation API routes.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(tags=["reputation"])


ALLOWED_PERIOD_PATTERN = {"all-time"}  # Also allows YYYY-MM format


def _validate_period(period: str) -> str:
    """Validate period parameter: 'all-time' or 'YYYY-MM' format."""
    if period == "all-time":
        return period
    import re
    if re.match(r"^\d{4}-(0[1-9]|1[0-2])$", period):
        return period
    raise ValueError("period must be 'all-time' or 'YYYY-MM' format")


@router.get("/agents/{agent_id}/reputation")
async def get_agent_reputation(
    agent_id: str,
    request: Request,
    period: str = "all-time",
    compute: bool = False,
):
    """
    Get reputation for an agent.

    - period: "all-time" or "YYYY-MM"
    - compute: if True, recompute from usage data before returning
    """
    try:
        period = _validate_period(period)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    rep_engine = request.app.state.reputation

    if compute:
        # Recompute from live data and persist (updates leaderboard)
        scores = rep_engine.save_reputation(
            provider_id=agent_id, period_label=period
        )
        return scores

    # Return persisted records
    records = rep_engine.get_agent_reputation(agent_id, period)
    if not records:
        # Compute on-demand
        scores = rep_engine.compute_reputation(
            provider_id=agent_id, period_label=period
        )
        return scores

    return {
        "agent_id": agent_id,
        "period": period,
        "records": records,
    }


@router.get("/services/{service_id}/reputation")
async def get_service_reputation(
    service_id: str,
    request: Request,
    period: str = "all-time",
):
    """Get reputation records for a specific service."""
    try:
        period = _validate_period(period)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    rep_engine = request.app.state.reputation
    records = rep_engine.get_service_reputation(service_id, period)

    return {
        "service_id": service_id,
        "period": period,
        "records": records,
    }


@router.get("/reputation/leaderboard")
async def reputation_leaderboard(request: Request, limit: int = 20):
    """Get top agents by reputation score."""
    limit = min(max(limit, 1), 100)
    rep_engine = request.app.state.reputation
    leaderboard = rep_engine.get_leaderboard(limit=limit)
    return {
        "leaderboard": leaderboard,
        "count": len(leaderboard),
    }
