"""
Multi-page admin dashboard — overview, services, agents, transactions, quality.
Serves Jinja2-rendered HTML pages under /dashboard/.
Authentication via cookie or Authorization header (initial login via query param
sets a session cookie for subsequent requests).

Backward-compatible: still serves the legacy /admin/dashboard route.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import os
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from marketplace.auth import AuthError
from .dashboard_queries import (
    fmt_number,
    clamp_page,
    build_page_range,
    query_overview_stats,
    query_payment_methods,
    query_recent_transactions,
    query_services_with_stats,
    query_agents_with_relations,
    query_transactions_filtered,
    query_available_payment_methods,
    query_quality_metrics,
    query_provider_stats,
    query_provider_quality_tiers,
    query_provider_growth_trend,
    query_provider_summary,
    _PER_PAGE,
)

router = APIRouter(tags=["dashboard"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Session cookie signing key — derived from the admin secret using HMAC
# prefix derivation so that session keys and CSRF keys never share the
# same raw secret value.
_RAW_ADMIN_SECRET = os.environ.get("ACF_ADMIN_SECRET", "") or os.urandom(32).hex()
_SESSION_SECRET = hmac.new(
    _RAW_ADMIN_SECRET.encode(), b"dashboard_session", hashlib.sha256,
).hexdigest()
_SESSION_COOKIE = "acf_dash_session"
_SESSION_MAX_AGE = 3600 * 8  # 8 hours


def _sign_session(key_id: str) -> str:
    """Create a signed session token: key_id|timestamp|signature."""
    ts = str(int(time.time()))
    payload = f"{key_id}|{ts}"
    sig = hmac.new(_SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}|{sig}"


def _verify_session(token: str) -> str | None:
    """Verify a session token. Returns key_id or None."""
    parts = token.split("|")
    if len(parts) != 3:
        return None
    key_id, ts_str, sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return None
    if time.time() - ts > _SESSION_MAX_AGE:
        return None
    expected_sig = hmac.new(
        _SESSION_SECRET.encode(), f"{key_id}|{ts_str}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected_sig):
        return None
    return key_id


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _auth_admin_via_query(request: Request, key: str) -> str | None:
    """Validate admin auth from query param, cookie, or Authorization header.

    Returns key_id on success (for session cookie creation).
    """
    # 1. Try session cookie first (avoids key in URL)
    session_cookie = request.cookies.get(_SESSION_COOKIE, "")
    if session_cookie:
        session_key_id = _verify_session(session_cookie)
        if session_key_id:
            # Validate key still exists and is admin
            key_mgr = request.app.state.auth
            try:
                record = key_mgr.validate_key_id(session_key_id)
                if record and record["role"] == "admin":
                    return session_key_id
            except Exception:
                pass  # Cookie invalid, fall through to other auth methods

    # 2. Try Authorization header
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        key = auth_header[7:]

    # 3. Try query param (initial login only)
    if not key:
        raise HTTPException(status_code=401, detail="API key required")
    parts = key.split(":", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=401,
            detail="Invalid key format. Use key_id:secret",
        )
    key_id, secret = parts
    key_mgr = request.app.state.auth
    try:
        record = key_mgr.validate(key_id, secret)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    if record["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return key_id


# ---------------------------------------------------------------------------
# Dashboard routes
# ---------------------------------------------------------------------------

def _set_session_cookie(response, key_id: str | None):
    """Set session cookie if key_id was obtained from query/header (not cookie)."""
    if key_id:
        response.set_cookie(
            _SESSION_COOKIE,
            _sign_session(key_id),
            max_age=_SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=True,
        )
    return response


@router.get("/dashboard/", response_class=HTMLResponse)
async def dashboard_overview(
    request: Request,
    key: str = Query("", description="Admin API key in key_id:secret format"),
):
    """Dashboard home -- overview stats, revenue summary, recent transactions."""
    key_id = _auth_admin_via_query(request, key)
    db = request.app.state.db

    with db.connect() as conn:
        stats = query_overview_stats(conn)
        payment_methods = query_payment_methods(conn)
        recent_transactions = query_recent_transactions(conn, limit=20)

    response = templates.TemplateResponse(
        "dashboard/overview.html",
        {
            "request": request,
            "key": "",
            "active_page": "Overview",
            "stats": stats,
            "payment_methods": payment_methods,
            "recent_transactions": recent_transactions,
        },
    )
    return _set_session_cookie(response, key_id)


@router.get("/dashboard/services", response_class=HTMLResponse)
async def dashboard_services(
    request: Request,
    key: str = Query("", description="Admin API key in key_id:secret format"),
):
    """Services view -- all registered services with call stats."""
    key_id = _auth_admin_via_query(request, key)
    db = request.app.state.db

    with db.connect() as conn:
        services = query_services_with_stats(conn)

    response = templates.TemplateResponse(
        "dashboard/services.html",
        {
            "request": request,
            "key": "",
            "active_page": "Services",
            "services": services,
        },
    )
    return _set_session_cookie(response, key_id)


@router.get("/dashboard/agents", response_class=HTMLResponse)
async def dashboard_agents(
    request: Request,
    key: str = Query("", description="Admin API key in key_id:secret format"),
):
    """Agents view -- all agents with reputation and service relations."""
    key_id = _auth_admin_via_query(request, key)
    db = request.app.state.db

    with db.connect() as conn:
        agents = query_agents_with_relations(conn)

    response = templates.TemplateResponse(
        "dashboard/agents.html",
        {
            "request": request,
            "key": "",
            "active_page": "Agents",
            "agents": agents,
        },
    )
    return _set_session_cookie(response, key_id)


@router.get("/dashboard/transactions", response_class=HTMLResponse)
async def dashboard_transactions(
    request: Request,
    key: str = Query("", description="Admin API key in key_id:secret format"),
    page: int = Query(1, ge=1, le=1000),
    service_id: str = Query("", description="Filter by service ID"),
    buyer_id: str = Query("", description="Filter by buyer ID"),
    payment_method: str = Query("", description="Filter by payment method"),
    date_from: str = Query("", description="Filter from date (YYYY-MM-DD)"),
    date_to: str = Query("", description="Filter to date (YYYY-MM-DD)"),
):
    """Transactions view -- paginated, filterable transaction log."""
    key_id = _auth_admin_via_query(request, key)
    db = request.app.state.db

    with db.connect() as conn:
        transactions, total_count = query_transactions_filtered(
            conn,
            service_id=service_id,
            buyer_id=buyer_id,
            payment_method=payment_method,
            date_from=date_from,
            date_to=date_to,
            page=page,
        )
        available_methods = query_available_payment_methods(conn)

    total_pages = max(1, math.ceil(total_count / _PER_PAGE))
    current_page = clamp_page(page, total_pages)

    # Build filter query string for pagination links (URL-encode values)
    from urllib.parse import quote
    filter_parts = []
    if service_id:
        filter_parts.append(f"&service_id={quote(service_id)}")
    if buyer_id:
        filter_parts.append(f"&buyer_id={quote(buyer_id)}")
    if payment_method:
        filter_parts.append(f"&payment_method={quote(payment_method)}")
    if date_from:
        filter_parts.append(f"&date_from={quote(date_from)}")
    if date_to:
        filter_parts.append(f"&date_to={quote(date_to)}")
    filter_qs = "".join(filter_parts)

    response = templates.TemplateResponse(
        "dashboard/transactions.html",
        {
            "request": request,
            "key": "",
            "active_page": "Transactions",
            "transactions": transactions,
            "total_count": total_count,
            "total_pages": total_pages,
            "current_page": current_page,
            "page_range": build_page_range(current_page, total_pages),
            "filter_qs": filter_qs,
            "filters": {
                "service_id": service_id,
                "buyer_id": buyer_id,
                "payment_method": payment_method,
                "date_from": date_from,
                "date_to": date_to,
            },
            "available_methods": available_methods,
        },
    )
    return _set_session_cookie(response, key_id)


@router.get("/dashboard/quality", response_class=HTMLResponse)
async def dashboard_quality(
    request: Request,
    key: str = Query("", description="Admin API key in key_id:secret format"),
):
    """Quality monitor -- SLA compliance, error rates, latency percentiles."""
    key_id = _auth_admin_via_query(request, key)
    db = request.app.state.db

    with db.connect() as conn:
        services, summary = query_quality_metrics(conn)

    response = templates.TemplateResponse(
        "dashboard/quality.html",
        {
            "request": request,
            "key": "",
            "active_page": "Quality",
            "services": services,
            "summary": summary,
        },
    )
    return _set_session_cookie(response, key_id)


@router.get("/dashboard/providers", response_class=HTMLResponse)
async def dashboard_providers(
    request: Request,
    key: str = Query("", description="Admin API key in key_id:secret format"),
):
    """Provider Growth Dashboard -- per-provider revenue, quality tiers, growth trends."""
    key_id = _auth_admin_via_query(request, key)
    db = request.app.state.db

    with db.connect() as conn:
        providers = query_provider_stats(conn)
        quality_tiers = query_provider_quality_tiers(conn)
        growth_trend = query_provider_growth_trend(conn)
        summary = query_provider_summary(conn)

    # Merge quality tier into each provider dict (immutable — create new dicts)
    providers_with_tiers = [
        {**p, "quality_tier": quality_tiers.get(p["provider_id"], "Standard")}
        for p in providers
    ]

    response = templates.TemplateResponse(
        "dashboard/providers.html",
        {
            "request": request,
            "key": "",
            "active_page": "Providers",
            "providers": providers_with_tiers,
            "growth_trend": growth_trend,
            "summary": summary,
        },
    )
    return _set_session_cookie(response, key_id)


# ---------------------------------------------------------------------------
# Legacy route (backward compatible)
# ---------------------------------------------------------------------------

@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard_legacy(
    request: Request,
    key: str = Query(..., description="Admin API key in key_id:secret format"),
):
    """Legacy single-page dashboard -- kept for backward compatibility."""
    key_id = _auth_admin_via_query(request, key)
    db = request.app.state.db

    with db.connect() as conn:
        svc_cnt = conn.execute(
            "SELECT COUNT(*) AS cnt FROM services WHERE status = 'active'"
        ).fetchone()["cnt"]
        agent_cnt = conn.execute(
            "SELECT COUNT(*) AS cnt FROM agent_identities WHERE status = 'active'"
        ).fetchone()["cnt"]
        team_cnt = conn.execute(
            "SELECT COUNT(*) AS cnt FROM teams WHERE status = 'active'"
        ).fetchone()["cnt"]
        usage_row = conn.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount_usd), 0) AS revenue "
            "FROM usage_records"
        ).fetchone()
        total_revenue = round(usage_row["revenue"], 2)
        settle_cnt = conn.execute(
            "SELECT COUNT(*) AS cnt FROM settlements"
        ).fetchone()["cnt"]

        daily_rows = conn.execute(
            """SELECT DATE(timestamp) AS date, COUNT(*) AS call_count,
                      COALESCE(SUM(amount_usd), 0) AS revenue_usd
               FROM usage_records
               WHERE DATE(timestamp) >= DATE('now', '-7 days')
               GROUP BY DATE(timestamp) ORDER BY date ASC"""
        ).fetchall()
        daily_usage = [
            {"date": r["date"], "call_count": r["call_count"],
             "revenue_usd": round(r["revenue_usd"], 2)}
            for r in daily_rows
        ]

        payment_rows = conn.execute(
            """SELECT payment_method, COUNT(*) AS count,
                      COALESCE(SUM(amount_usd), 0) AS total_usd
               FROM usage_records GROUP BY payment_method
               ORDER BY total_usd DESC"""
        ).fetchall()
        payment_methods = [
            {"method": r["payment_method"] or "unknown",
             "count": r["count"], "total_usd": round(r["total_usd"], 2)}
            for r in payment_rows
        ]

        provider_rows = conn.execute(
            """SELECT u.provider_id,
                      COALESCE(a.display_name, u.provider_id) AS display_name,
                      COUNT(*) AS total_calls,
                      COALESCE(SUM(u.amount_usd), 0) AS total_revenue,
                      COALESCE(AVG(u.latency_ms), 0) AS avg_latency_ms,
                      CASE WHEN COUNT(*) = 0 THEN 0.0
                           ELSE ROUND(
                               CAST(SUM(CASE WHEN u.status_code < 400 THEN 1
                                    ELSE 0 END) AS REAL)
                               / COUNT(*) * 100, 2)
                      END AS success_rate
               FROM usage_records u
               LEFT JOIN agent_identities a ON a.owner_id = u.provider_id
               GROUP BY u.provider_id
               ORDER BY total_revenue DESC LIMIT 10"""
        ).fetchall()
        top_providers = [
            {"rank": i + 1, "display_name": r["display_name"],
             "total_calls": r["total_calls"],
             "total_revenue": round(r["total_revenue"], 2),
             "avg_latency_ms": round(r["avg_latency_ms"], 1),
             "success_rate": r["success_rate"]}
            for i, r in enumerate(provider_rows)
        ]

        health_rows = conn.execute(
            """SELECT s.id AS service_id, s.name, s.status,
                      COALESCE(AVG(u.latency_ms), 0) AS avg_latency_ms,
                      CASE WHEN COUNT(u.id) = 0 THEN 0.0
                           ELSE ROUND(
                               CAST(SUM(CASE WHEN u.status_code >= 500 THEN 1
                                    ELSE 0 END) AS REAL)
                               / COUNT(u.id) * 100, 2)
                      END AS error_rate,
                      MAX(u.timestamp) AS last_called
               FROM services s
               LEFT JOIN usage_records u ON u.service_id = s.id
               WHERE s.status = 'active'
               GROUP BY s.id ORDER BY s.name"""
        ).fetchall()
        service_health = [
            {"name": r["name"], "status": r["status"],
             "avg_latency_ms": round(r["avg_latency_ms"], 1),
             "error_rate": r["error_rate"],
             "last_called": r["last_called"] or "Never"}
            for r in health_rows
        ]

    max_daily = max((d["call_count"] for d in daily_usage), default=1) or 1
    max_pay = max((p["total_usd"] for p in payment_methods), default=1) or 1

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "total_services": fmt_number(svc_cnt),
            "total_agents": fmt_number(agent_cnt),
            "total_teams": fmt_number(team_cnt),
            "total_revenue": fmt_number(total_revenue, 2),
            "total_settlements": fmt_number(settle_cnt),
            "daily_usage": daily_usage,
            "max_daily_calls": max_daily,
            "payment_methods": payment_methods,
            "max_payment_usd": max_pay,
            "top_providers": top_providers,
            "service_health": service_health,
        },
    )
