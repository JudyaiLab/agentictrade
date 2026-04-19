"""
Provider self-service API routes.
Endpoints for providers to manage their services, view earnings,
manage API keys, test endpoints, and track onboarding progress.
"""
from __future__ import annotations

import ipaddress
import time
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request

from api.deps import require_provider

logger = logging.getLogger("acf.provider")

router = APIRouter(prefix="/provider", tags=["provider"])


_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "169.254.169.254"}


def _validate_external_url(url: str) -> None:
    """Reject private/loopback/link-local addresses and disallowed schemes."""
    try:
        parsed = urlparse(url)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid endpoint URL")

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise HTTPException(status_code=400, detail="Only http/https endpoints allowed")

    hostname = (parsed.hostname or "").lower()
    if hostname in _BLOCKED_HOSTS:
        raise HTTPException(status_code=400, detail="Endpoint target not allowed")

    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise HTTPException(status_code=400, detail="Endpoint must be a public address")
    except ValueError:
        pass  # hostname, not IP — allowed


# --- Dashboard ---

@router.get("/dashboard")
async def provider_dashboard(request: Request):
    """Provider overview: service count, total calls, revenue, settlements."""
    owner_id, _ = require_provider(request)
    db = request.app.state.db

    with db.connect() as conn:
        # Count services
        svc_count = conn.execute(
            "SELECT COUNT(*) FROM services WHERE provider_id = ?",
            (owner_id,),
        ).fetchone()[0]

        # Total calls and revenue
        usage_row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount_usd), 0) "
            "FROM usage_records WHERE provider_id = ?",
            (owner_id,),
        ).fetchone()
        total_calls = usage_row[0]
        total_revenue = round(float(usage_row[1]), 2)

        # Settled amount
        settled_row = conn.execute(
            "SELECT COALESCE(SUM(net_amount), 0) "
            "FROM settlements WHERE provider_id = ? AND status = 'completed'",
            (owner_id,),
        ).fetchone()
        total_settled = round(float(settled_row[0]), 2)

        # Pending settlement
        pending_row = conn.execute(
            "SELECT COALESCE(SUM(net_amount), 0) "
            "FROM settlements WHERE provider_id = ? AND status = 'pending'",
            (owner_id,),
        ).fetchone()
        pending_settlement = round(float(pending_row[0]), 2)

    return {
        "provider_id": owner_id,
        "total_services": svc_count,
        "total_calls": total_calls,
        "total_revenue": total_revenue,
        "total_settled": total_settled,
        "pending_settlement": pending_settlement,
    }


# --- My Services ---

@router.get("/services")
async def provider_services(request: Request):
    """List provider's own services with usage stats."""
    owner_id, _ = require_provider(request)
    db = request.app.state.db

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, name, description, endpoint, price_per_call, status, "
            "category, created_at "
            "FROM services WHERE provider_id = ? ORDER BY created_at DESC",
            (owner_id,),
        ).fetchall()

        services = []
        for row in rows:
            svc_id = row["id"]
            stats = conn.execute(
                "SELECT COUNT(*) as cnt, "
                "COALESCE(SUM(amount_usd), 0) as rev, "
                "COALESCE(AVG(latency_ms), 0) as avg_lat "
                "FROM usage_records WHERE service_id = ?",
                (svc_id,),
            ).fetchone()

            services.append({
                "id": svc_id,
                "name": row["name"],
                "description": row["description"],
                "endpoint": row["endpoint"],
                "price_per_call": row["price_per_call"],
                "status": row["status"],
                "category": row["category"],
                "total_calls": stats["cnt"],
                "total_revenue": round(float(stats["rev"]), 2),
                "avg_latency_ms": round(float(stats["avg_lat"]), 1),
                "created_at": row["created_at"],
            })

    return {"services": services}


# --- Service Analytics ---

@router.get("/services/{service_id}/analytics")
async def service_analytics(service_id: str, request: Request):
    """Detailed analytics for a specific service (owner only)."""
    owner_id, _ = require_provider(request)
    db = request.app.state.db

    svc = db.get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    if svc["provider_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Not your service")

    with db.connect() as conn:
        # Overall stats
        stats = conn.execute(
            "SELECT COUNT(*) as cnt, "
            "COALESCE(SUM(amount_usd), 0) as rev, "
            "COALESCE(AVG(latency_ms), 0) as avg_lat, "
            "MIN(timestamp) as first_call, "
            "MAX(timestamp) as last_call "
            "FROM usage_records WHERE service_id = ?",
            (service_id,),
        ).fetchone()

        # Success rate
        success = conn.execute(
            "SELECT COUNT(*) FROM usage_records "
            "WHERE service_id = ? AND status_code < 400",
            (service_id,),
        ).fetchone()[0]

        total = stats["cnt"]
        success_rate = round(success / total * 100, 1) if total > 0 else 100.0

        # Unique buyers
        buyers = conn.execute(
            "SELECT COUNT(DISTINCT buyer_id) FROM usage_records WHERE service_id = ?",
            (service_id,),
        ).fetchone()[0]

        # Daily breakdown (last 30 days)
        daily = conn.execute(
            "SELECT DATE(timestamp) as day, COUNT(*) as calls, "
            "SUM(amount_usd) as revenue "
            "FROM usage_records WHERE service_id = ? "
            "GROUP BY DATE(timestamp) ORDER BY day DESC LIMIT 30",
            (service_id,),
        ).fetchall()

    return {
        "service_id": service_id,
        "service_name": svc["name"],
        "total_calls": total,
        "total_revenue": round(float(stats["rev"]), 2),
        "avg_latency_ms": round(float(stats["avg_lat"]), 1),
        "success_rate": success_rate,
        "unique_buyers": buyers,
        "first_call": stats["first_call"],
        "last_call": stats["last_call"],
        "daily": [
            {"date": r["day"], "calls": r["calls"], "revenue": round(float(r["revenue"]), 2)}
            for r in daily
        ],
    }


# --- Earnings ---

@router.get("/earnings")
async def provider_earnings(request: Request):
    """Earnings summary: total earned, settled, pending, settlement history."""
    owner_id, _ = require_provider(request)
    db = request.app.state.db

    with db.connect() as conn:
        # Total revenue from usage
        rev_row = conn.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) FROM usage_records WHERE provider_id = ?",
            (owner_id,),
        ).fetchone()
        total_earned = round(float(rev_row[0]), 2)

        # Settled (completed)
        settled_row = conn.execute(
            "SELECT COALESCE(SUM(net_amount), 0) FROM settlements "
            "WHERE provider_id = ? AND status = 'completed'",
            (owner_id,),
        ).fetchone()
        total_settled = round(float(settled_row[0]), 2)

        # Pending
        pending_row = conn.execute(
            "SELECT COALESCE(SUM(net_amount), 0) FROM settlements "
            "WHERE provider_id = ? AND status = 'pending'",
            (owner_id,),
        ).fetchone()
        pending_settlement = round(float(pending_row[0]), 2)

        # Settlement history
        settlements = conn.execute(
            "SELECT id, total_amount, platform_fee, net_amount, status, "
            "period_start, period_end "
            "FROM settlements WHERE provider_id = ? ORDER BY period_end DESC",
            (owner_id,),
        ).fetchall()

    return {
        "total_earned": total_earned,
        "total_settled": total_settled,
        "pending_settlement": pending_settlement,
        "settlements": [
            {
                "id": s["id"],
                "total_amount": round(float(s["total_amount"]), 2),
                "platform_fee": round(float(s["platform_fee"]), 2),
                "net_amount": round(float(s["net_amount"]), 2),
                "status": s["status"],
                "period_start": s["period_start"],
                "period_end": s["period_end"],
            }
            for s in settlements
        ],
    }


# --- API Keys ---

@router.get("/keys")
async def provider_keys(request: Request):
    """List provider's own API keys (secrets not returned)."""
    owner_id, _ = require_provider(request)
    db = request.app.state.db

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT key_id, owner_id, role, rate_limit, wallet_address, "
            "created_at, expires_at FROM api_keys WHERE owner_id = ?",
            (owner_id,),
        ).fetchall()

    return {
        "keys": [
            {
                "key_id": r["key_id"],
                "role": r["role"],
                "rate_limit": r["rate_limit"],
                "wallet_address": r["wallet_address"],
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
            }
            for r in rows
        ],
    }


@router.delete("/keys/{key_id}")
async def revoke_key(key_id: str, request: Request):
    """Revoke (delete) one of provider's own API keys."""
    owner_id, _ = require_provider(request)
    db = request.app.state.db

    with db.connect() as conn:
        row = conn.execute(
            "SELECT owner_id FROM api_keys WHERE key_id = ?", (key_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Key not found")
        if row["owner_id"] != owner_id:
            raise HTTPException(status_code=403, detail="Not your key")
        conn.execute("DELETE FROM api_keys WHERE key_id = ?", (key_id,))

    return {"status": "revoked", "key_id": key_id}


# --- Service Health Test ---

@router.post("/services/{service_id}/test")
async def test_service_endpoint(service_id: str, request: Request):
    """Test a service endpoint's connectivity (owner only)."""
    owner_id, _ = require_provider(request)
    db = request.app.state.db

    svc = db.get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    if svc["provider_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Not your service")

    endpoint = svc["endpoint"]
    _validate_external_url(endpoint)

    reachable = False
    latency_ms = 0
    status_code = 0
    error_msg = ""

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as http:
            resp = await http.get(endpoint)
            latency_ms = round((time.monotonic() - start) * 1000)
            status_code = resp.status_code
            reachable = status_code < 500
    except httpx.TimeoutException:
        latency_ms = 10000
        error_msg = "Connection timed out"
    except httpx.ConnectError:
        error_msg = "Connection refused or unreachable"
    except httpx.InvalidURL:
        error_msg = "Invalid endpoint URL"
    except Exception:
        logger.warning("Service test error for %s", service_id, exc_info=True)
        error_msg = "Unexpected error during connectivity test"

    return {
        "service_id": service_id,
        "endpoint": endpoint,
        "reachable": reachable,
        "latency_ms": latency_ms,
        "status_code": status_code,
        "error": error_msg,
    }


# --- Health Score (provider view) ---

@router.get("/health")
async def my_health_scores(request: Request):
    """Get health scores for all your services."""
    owner_id, _ = require_provider(request)
    from marketplace.health_monitor import HealthMonitor

    db = request.app.state.db
    monitor = HealthMonitor(db)
    summary = monitor.get_provider_health_summary(owner_id)
    return summary


# --- Reputation (provider view) ---

@router.get("/reputation")
async def my_reputation(request: Request):
    """Get reputation scores for all your services."""
    owner_id, _ = require_provider(request)
    db = request.app.state.db

    # Get all services for this provider
    services = db.list_services(status="active")
    my_services = [s for s in services if s["provider_id"] == owner_id]

    results = []
    for svc in my_services:
        reputation = db.get_service_reputation(svc["id"], "all-time")
        usage_stats = db.get_usage_for_reputation(owner_id, svc["id"])
        results.append({
            "service_id": svc["id"],
            "service_name": svc["name"],
            "reputation": reputation,
            "usage_stats": usage_stats,
        })

    return {
        "provider_id": owner_id,
        "service_count": len(results),
        "services": results,
    }


# --- Onboarding ---

@router.get("/onboarding")
async def onboarding_status(request: Request):
    """Track provider onboarding progress."""
    owner_id, _ = require_provider(request)
    db = request.app.state.db

    with db.connect() as conn:
        # Step 1: Has API key (always true if they authenticated)
        has_key = True

        # Step 2: Has registered at least one service
        svc_count = conn.execute(
            "SELECT COUNT(*) FROM services WHERE provider_id = ?", (owner_id,)
        ).fetchone()[0]
        has_service = svc_count > 0

        # Step 3: Has active service
        active_count = conn.execute(
            "SELECT COUNT(*) FROM services WHERE provider_id = ? AND status = 'active'",
            (owner_id,),
        ).fetchone()[0]
        has_active = active_count > 0

        # Step 4: Has received at least one call
        call_count = conn.execute(
            "SELECT COUNT(*) FROM usage_records WHERE provider_id = ?", (owner_id,)
        ).fetchone()[0]
        has_traffic = call_count > 0

        # Step 5: Has a completed or pending settlement
        settle_count = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE provider_id = ? "
            "AND status IN ('completed', 'pending')", (owner_id,)
        ).fetchone()[0]
        has_settlement = settle_count > 0

    steps = {
        "create_api_key": {"completed": has_key, "label": "Create API key"},
        "register_service": {"completed": has_service, "label": "Register your first service"},
        "activate_service": {"completed": has_active, "label": "Activate a service"},
        "first_traffic": {"completed": has_traffic, "label": "Receive first API call"},
        "first_settlement": {"completed": has_settlement, "label": "Complete first settlement"},
    }

    completed = sum(1 for s in steps.values() if s["completed"])
    total = len(steps)

    return {
        "provider_id": owner_id,
        "steps": steps,
        "completed_steps": completed,
        "total_steps": total,
        "completion_pct": round(completed / total * 100, 1),
    }


# --- Milestones ---

@router.get("/milestones")
async def my_milestones(request: Request):
    """Get milestone progress for the authenticated provider."""
    owner_id, _ = require_provider(request)
    from marketplace.milestones import MilestoneTracker

    db = request.app.state.db
    tracker = MilestoneTracker(db)

    # Check for newly achieved milestones
    newly_awarded = tracker.check_and_award(owner_id)

    # Auto-apply cashback if eligible
    if any(m["milestone_type"] == "cashback" for m in newly_awarded):
        tracker.apply_cashback(owner_id)

    progress = tracker.get_progress(owner_id)
    progress["newly_awarded"] = newly_awarded
    return progress
