"""
Referral system API routes.

Endpoints for providers to generate referral codes, apply codes,
view referral lists, and check referral stats.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.deps import extract_owner
from marketplace.referral import ReferralManager

router = APIRouter(prefix="/referrals", tags=["referrals"])


class ApplyCodeRequest(BaseModel):
    code: str


def _get_referral_manager(request: Request) -> ReferralManager:
    """Get or create the ReferralManager from app state."""
    if not hasattr(request.app.state, "referral_manager"):
        db = request.app.state.db
        commission_engine = getattr(request.app.state, "commission_engine", None)
        request.app.state.referral_manager = ReferralManager(
            db, commission_engine=commission_engine,
        )
    return request.app.state.referral_manager


@router.post("/code", status_code=201)
async def generate_referral_code(request: Request):
    """Generate a new referral code for the authenticated provider.

    Requires API key authentication (Bearer key_id:secret).
    Returns the new referral record with the code.
    """
    owner_id, _ = extract_owner(request)
    manager = _get_referral_manager(request)

    try:
        referral = manager.generate_code(owner_id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return referral


@router.post("/apply")
async def apply_referral_code(req: ApplyCodeRequest, request: Request):
    """Apply a referral code for the authenticated provider.

    Requires API key authentication (Bearer key_id:secret).
    Links the current provider as 'referred' by the code owner.
    """
    owner_id, _ = extract_owner(request)
    manager = _get_referral_manager(request)

    try:
        result = manager.apply_code(owner_id, req.code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result


@router.get("")
async def list_referrals(request: Request):
    """List all referrals for the authenticated provider (as referrer).

    Requires API key authentication (Bearer key_id:secret).
    """
    owner_id, _ = extract_owner(request)
    manager = _get_referral_manager(request)

    referrals = manager.get_referrals(owner_id)
    return {"referrals": referrals, "count": len(referrals)}


@router.get("/stats")
async def referral_stats(request: Request):
    """Get referral summary stats for the authenticated provider.

    Requires API key authentication (Bearer key_id:secret).
    Returns total_referred, active, pending, total_earned.
    """
    owner_id, _ = extract_owner(request)
    manager = _get_referral_manager(request)

    stats = manager.get_stats(owner_id)
    # Convert Decimal to string for JSON serialization
    stats["total_earned"] = str(stats["total_earned"])
    return stats
