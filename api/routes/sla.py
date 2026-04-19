"""
SLA (Service Level Agreement) API routes.

Manage SLA tiers, check compliance, and view breaches.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import extract_owner, require_admin

router = APIRouter(prefix="/sla", tags=["sla"])


# --- Request models ---

class SetSLARequest(BaseModel):
    sla_tier: str = Field(
        default="basic",
        description="SLA tier: basic (95% uptime), standard (99%), or premium (99.9%)",
    )


# --- Routes ---

@router.post("/services/{service_id}")
async def set_service_sla(
    service_id: str,
    req: SetSLARequest,
    request: Request,
):
    """Set or update the SLA tier for a service.

    Tiers: basic (95% uptime, 5s latency), standard (99%, 2s),
    premium (99.9%, 500ms). Provider or admin only.
    """
    owner_id, key_record = extract_owner(request)

    # Verify ownership
    db = request.app.state.db
    service = db.get_service(service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    if service["provider_id"] != owner_id and key_record["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not your service")

    mgr = _get_manager(request)
    try:
        result = mgr.set_service_sla(service_id, req.sla_tier)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/services/{service_id}")
async def get_service_sla(service_id: str, request: Request):
    """Get the SLA configuration and current compliance status."""
    owner_id, key_record = extract_owner(request)

    mgr = _get_manager(request)
    config = mgr.get_service_sla(service_id)

    status = mgr.check_compliance(service_id)
    return {
        "config": config or {"sla_tier": "basic", "note": "Default tier (not explicitly set)"},
        "status": _status_to_dict(status) if status else None,
    }


@router.get("/services/{service_id}/compliance")
async def check_sla_compliance(
    service_id: str,
    request: Request,
    lookback_days: int = 30,
):
    """Check SLA compliance for a service over a lookback period.

    Returns detailed compliance status including which metrics
    are met/breached.
    """
    extract_owner(request)  # require auth

    mgr = _get_manager(request)
    status = mgr.check_compliance(service_id, lookback_days)
    if not status:
        raise HTTPException(
            status_code=404,
            detail="No health check data available for this service",
        )
    return _status_to_dict(status)


@router.get("/services/{service_id}/breaches")
async def get_sla_breaches(
    service_id: str,
    request: Request,
    limit: int = 50,
):
    """Get recent SLA breaches for a service."""
    owner_id, key_record = extract_owner(request)

    db = request.app.state.db
    service = db.get_service(service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    if service["provider_id"] != owner_id and key_record["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not your service")

    mgr = _get_manager(request)
    breaches = mgr.get_breaches(service_id, limit)
    return {"breaches": breaches, "count": len(breaches)}


@router.get("/providers/{provider_id}/summary")
async def get_provider_sla_summary(provider_id: str, request: Request):
    """Get SLA compliance summary across all of a provider's services."""
    owner_id, key_record = extract_owner(request)

    if provider_id != owner_id and key_record["role"] != "admin":
        raise HTTPException(status_code=403, detail="Access denied")

    mgr = _get_manager(request)
    return mgr.get_provider_sla_summary(provider_id)


@router.get("/tiers")
async def list_sla_tiers(request: Request):
    """List all available SLA tiers and their requirements."""
    from marketplace.sla import SLA_TIERS, _tier_to_dict
    return {
        "tiers": {
            name: _tier_to_dict(tier)
            for name, tier in SLA_TIERS.items()
        },
    }


# --- Helpers ---

def _get_manager(request: Request):
    from marketplace.sla import SLAManager
    # Lazily init SLA manager (reuses app db)
    if not hasattr(request.app.state, "sla_mgr"):
        request.app.state.sla_mgr = SLAManager(request.app.state.db)
    return request.app.state.sla_mgr


def _status_to_dict(status) -> dict:
    return {
        "service_id": status.service_id,
        "sla_tier": status.sla_tier,
        "compliant": status.compliant,
        "metrics": {
            "uptime": {
                "actual": status.uptime_pct,
                "target": status.uptime_target,
                "met": status.uptime_met,
            },
            "latency_ms": {
                "actual": status.avg_latency_ms,
                "target": status.latency_target,
                "met": status.latency_met,
            },
            "error_rate": {
                "actual": status.error_rate_pct,
                "target": status.error_rate_target,
                "met": status.error_rate_met,
            },
        },
        "breach_count": status.breach_count,
        "last_checked": status.last_checked,
    }
