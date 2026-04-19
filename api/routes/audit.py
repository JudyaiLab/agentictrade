"""
Audit log API routes — view security audit events.
All endpoints require admin authentication via require_admin.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request

from api.deps import require_admin

router = APIRouter(tags=["admin"])

# Default time window when no explicit range is provided (30 days).
_DEFAULT_DAYS = 30


@router.get("/admin/audit")
async def list_audit_events(
    request: Request,
    event_type: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    since: str | None = Query(
        default=None,
        description="ISO-8601 lower bound for timestamp (defaults to 30 days ago)",
    ),
    until: str | None = Query(
        default=None,
        description="ISO-8601 upper bound for timestamp",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """List audit log events with optional filters (admin only).

    When no ``since``/``until`` range is provided, defaults to the last
    30 days to prevent full-table scans.
    """
    require_admin(request)
    audit = request.app.state.audit

    # Apply default time window when caller omits both bounds
    effective_since = since
    if effective_since is None and until is None:
        effective_since = (
            datetime.now(timezone.utc) - timedelta(days=_DEFAULT_DAYS)
        ).isoformat()

    events = audit.get_events(
        event_type=event_type,
        actor=actor,
        since=effective_since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return {"events": events, "count": len(events)}


@router.get("/admin/audit/summary")
async def audit_summary(
    request: Request,
    hours: int = Query(default=24, ge=1, le=720),
):
    """Event counts by type for the last N hours (admin only)."""
    require_admin(request)
    audit = request.app.state.audit

    summary = audit.get_summary(hours=hours)
    return {"hours": hours, "summary": summary}
