"""
Admin dashboard API routes — platform monitoring and analytics.
All endpoints require admin authentication via require_admin.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from api.deps import require_admin

router = APIRouter(tags=["admin"])


@router.get("/admin/stats")
async def platform_stats(request: Request):
    """Platform overview statistics."""
    require_admin(request)
    db = request.app.state.db

    with db.connect() as conn:
        svc_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM services WHERE status = 'active'"
        ).fetchone()
        total_services = svc_row["cnt"]

        agent_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM agent_identities WHERE status = 'active'"
        ).fetchone()
        total_agents = agent_row["cnt"]

        team_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM teams WHERE status = 'active'"
        ).fetchone()
        total_teams = team_row["cnt"]

        usage_row = conn.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount_usd), 0) AS revenue "
            "FROM usage_records"
        ).fetchone()
        total_usage_records = usage_row["cnt"]
        total_revenue_usd = round(usage_row["revenue"], 6)

        settle_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM settlements"
        ).fetchone()
        total_settlements = settle_row["cnt"]

        wh_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM webhooks WHERE active = 1"
        ).fetchone()
        active_webhooks = wh_row["cnt"]

    return {
        "total_services": total_services,
        "total_agents": total_agents,
        "total_teams": total_teams,
        "total_usage_records": total_usage_records,
        "total_revenue_usd": total_revenue_usd,
        "total_settlements": total_settlements,
        "active_webhooks": active_webhooks,
    }


@router.get("/admin/usage/daily")
async def daily_usage(
    request: Request,
    days: int = Query(default=30, ge=1, le=90),
):
    """Daily usage aggregation for the last N days."""
    require_admin(request)
    db = request.app.state.db

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                DATE(timestamp) AS date,
                COUNT(*) AS call_count,
                COALESCE(SUM(amount_usd), 0) AS revenue_usd,
                COUNT(DISTINCT buyer_id) AS unique_buyers,
                COUNT(DISTINCT service_id) AS unique_services
            FROM usage_records
            WHERE DATE(timestamp) >= DATE('now', ? || ' days')
            GROUP BY DATE(timestamp)
            ORDER BY date DESC
            """,
            (str(-days),),
        ).fetchall()

    return {
        "days": days,
        "data": [
            {
                "date": row["date"],
                "call_count": row["call_count"],
                "revenue_usd": round(row["revenue_usd"], 6),
                "unique_buyers": row["unique_buyers"],
                "unique_services": row["unique_services"],
            }
            for row in rows
        ],
    }


@router.get("/admin/providers/ranking")
async def provider_ranking(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    period: str = Query(
        default="all-time",
        pattern="^(all-time|7|14|30|60|90|180|365)$",
    ),
):
    """Rank providers by usage volume and revenue."""
    require_admin(request)
    db = request.app.state.db

    period_filter = ""
    params: list = []

    if period != "all-time":
        period_filter = "WHERE DATE(u.timestamp) >= DATE('now', ? || ' days')"
        params.append(str(-int(period)))

    params.append(limit)

    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                u.provider_id,
                COALESCE(a.display_name, u.provider_id) AS display_name,
                COUNT(*) AS total_calls,
                COALESCE(SUM(u.amount_usd), 0) AS total_revenue,
                COALESCE(AVG(u.latency_ms), 0) AS avg_latency_ms,
                CASE
                    WHEN COUNT(*) = 0 THEN 0.0
                    ELSE ROUND(
                        CAST(SUM(CASE WHEN u.status_code < 400 THEN 1 ELSE 0 END) AS REAL)
                        / COUNT(*) * 100, 2
                    )
                END AS success_rate
            FROM usage_records u
            LEFT JOIN agent_identities a ON a.owner_id = u.provider_id
            {period_filter}
            GROUP BY u.provider_id
            ORDER BY total_revenue DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    return {
        "period": period,
        "providers": [
            {
                "provider_id": row["provider_id"],
                "display_name": row["display_name"],
                "total_calls": row["total_calls"],
                "total_revenue": round(row["total_revenue"], 6),
                "avg_latency_ms": round(row["avg_latency_ms"], 1),
                "success_rate": row["success_rate"],
            }
            for row in rows
        ],
    }


@router.get("/admin/services/health")
async def services_health(request: Request):
    """Health overview of all active services."""
    require_admin(request)
    db = request.app.state.db

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                s.id AS service_id,
                s.name,
                s.provider_id,
                s.status,
                COALESCE(AVG(u.latency_ms), 0) AS avg_latency_ms,
                CASE
                    WHEN COUNT(u.id) = 0 THEN 0.0
                    ELSE ROUND(
                        CAST(SUM(CASE WHEN u.status_code >= 500 THEN 1 ELSE 0 END) AS REAL)
                        / COUNT(u.id) * 100, 2
                    )
                END AS error_rate,
                MAX(u.timestamp) AS last_called
            FROM services s
            LEFT JOIN usage_records u ON u.service_id = s.id
            WHERE s.status = 'active'
            GROUP BY s.id
            ORDER BY s.name
            """
        ).fetchall()

    return {
        "services": [
            {
                "service_id": row["service_id"],
                "name": row["name"],
                "provider_id": row["provider_id"],
                "status": row["status"],
                "avg_latency_ms": round(row["avg_latency_ms"], 1),
                "error_rate": row["error_rate"],
                "last_called": row["last_called"],
            }
            for row in rows
        ],
    }


@router.get("/admin/payments/summary")
async def payments_summary(request: Request):
    """Breakdown of usage by payment method."""
    require_admin(request)
    db = request.app.state.db

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                payment_method,
                COUNT(*) AS count,
                COALESCE(SUM(amount_usd), 0) AS total_usd
            FROM usage_records
            GROUP BY payment_method
            ORDER BY total_usd DESC
            """
        ).fetchall()

    methods: dict = {}
    for row in rows:
        methods[row["payment_method"]] = {
            "count": row["count"],
            "total_usd": round(row["total_usd"], 6),
        }

    return {"methods": methods}


@router.get("/admin/analytics/trends")
async def analytics_trends(
    request: Request,
    granularity: str = Query(default="weekly", pattern="^(daily|weekly|monthly)$"),
    periods: int = Query(default=12, ge=1, le=52),
):
    """Revenue and call trends by day/week/month."""
    require_admin(request)
    db = request.app.state.db

    _TREND_BODY = (
        " COUNT(*) AS calls,"
        " COALESCE(SUM(amount_usd), 0) AS revenue,"
        " COUNT(DISTINCT buyer_id) AS unique_buyers,"
        " COUNT(DISTINCT service_id) AS active_services,"
        " COALESCE(AVG(latency_ms), 0) AS avg_latency,"
        " CASE WHEN COUNT(*) = 0 THEN 100.0"
        "      ELSE ROUND(CAST(SUM(CASE WHEN status_code < 400 THEN 1 ELSE 0 END) AS REAL)"
        "                 / COUNT(*) * 100, 2) END AS success_rate"
        " FROM usage_records"
    )
    _TREND_QUERIES = {
        "daily": (
            "SELECT DATE(timestamp) AS period," + _TREND_BODY
            + " GROUP BY DATE(timestamp) ORDER BY period DESC LIMIT ?"
        ),
        "weekly": (
            "SELECT strftime('%Y-W%W', timestamp) AS period," + _TREND_BODY
            + " GROUP BY strftime('%Y-W%W', timestamp) ORDER BY period DESC LIMIT ?"
        ),
        "monthly": (
            "SELECT strftime('%Y-%m', timestamp) AS period," + _TREND_BODY
            + " GROUP BY strftime('%Y-%m', timestamp) ORDER BY period DESC LIMIT ?"
        ),
    }

    with db.connect() as conn:
        rows = conn.execute(_TREND_QUERIES[granularity], (periods,)).fetchall()

    return {
        "granularity": granularity,
        "data": [
            {
                "period": r["period"],
                "calls": r["calls"],
                "revenue": round(r["revenue"], 2),
                "unique_buyers": r["unique_buyers"],
                "active_services": r["active_services"],
                "avg_latency_ms": round(r["avg_latency"], 1),
                "success_rate": r["success_rate"],
            }
            for r in rows
        ],
    }


@router.get("/admin/analytics/top-services")
async def top_services(
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
    sort_by: str = Query(default="revenue", pattern="^(revenue|calls|latency)$"),
    days: int = Query(default=30, ge=1, le=365),
):
    """Top services ranked by revenue, calls, or latency."""
    require_admin(request)
    db = request.app.state.db

    _TOP_SVC_BASE = (
        "SELECT u.service_id, s.name AS service_name, s.provider_id, s.category,"
        " COUNT(*) AS total_calls,"
        " COALESCE(SUM(u.amount_usd), 0) AS total_revenue,"
        " COALESCE(AVG(u.latency_ms), 0) AS avg_latency,"
        " COUNT(DISTINCT u.buyer_id) AS unique_buyers,"
        " CASE WHEN COUNT(*) = 0 THEN 100.0"
        "      ELSE ROUND(CAST(SUM(CASE WHEN u.status_code < 400 THEN 1 ELSE 0 END) AS REAL)"
        "                 / COUNT(*) * 100, 2) END AS success_rate"
        " FROM usage_records u JOIN services s ON s.id = u.service_id"
        " WHERE DATE(u.timestamp) >= DATE('now', ? || ' days')"
        " GROUP BY u.service_id"
    )
    _TOP_SVC_QUERIES = {
        "revenue": _TOP_SVC_BASE + " ORDER BY total_revenue DESC LIMIT ?",
        "calls": _TOP_SVC_BASE + " ORDER BY total_calls DESC LIMIT ?",
        "latency": _TOP_SVC_BASE + " ORDER BY avg_latency ASC LIMIT ?",
    }

    with db.connect() as conn:
        rows = conn.execute(
            _TOP_SVC_QUERIES[sort_by],
            (str(-days), limit),
        ).fetchall()

    return {
        "sort_by": sort_by,
        "days": days,
        "services": [
            {
                "service_id": r["service_id"],
                "service_name": r["service_name"],
                "provider_id": r["provider_id"],
                "category": r["category"],
                "total_calls": r["total_calls"],
                "total_revenue": round(r["total_revenue"], 2),
                "avg_latency_ms": round(r["avg_latency"], 1),
                "unique_buyers": r["unique_buyers"],
                "success_rate": r["success_rate"],
            }
            for r in rows
        ],
    }


@router.get("/admin/analytics/buyers")
async def buyer_metrics(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
):
    """Buyer engagement metrics: new, active, repeat, top spenders."""
    require_admin(request)
    db = request.app.state.db

    with db.connect() as conn:
        # Total unique buyers all time
        total_buyers = conn.execute(
            "SELECT COUNT(DISTINCT buyer_id) FROM usage_records"
        ).fetchone()[0]

        # Active buyers in period
        active_buyers = conn.execute(
            "SELECT COUNT(DISTINCT buyer_id) FROM usage_records "
            "WHERE DATE(timestamp) >= DATE('now', ? || ' days')",
            (str(-days),),
        ).fetchone()[0]

        # Repeat buyers (>1 call in period)
        repeat_buyers = conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT buyer_id FROM usage_records "
            "  WHERE DATE(timestamp) >= DATE('now', ? || ' days') "
            "  GROUP BY buyer_id HAVING COUNT(*) > 1"
            ")",
            (str(-days),),
        ).fetchone()[0]

        # Top spenders
        top_spenders = conn.execute(
            """
            SELECT buyer_id, COUNT(*) AS calls,
                   COALESCE(SUM(amount_usd), 0) AS total_spent,
                   COUNT(DISTINCT service_id) AS services_used
            FROM usage_records
            WHERE DATE(timestamp) >= DATE('now', ? || ' days')
            GROUP BY buyer_id
            ORDER BY total_spent DESC
            LIMIT 10
            """,
            (str(-days),),
        ).fetchall()

        # Average calls per buyer
        avg_calls = conn.execute(
            "SELECT COALESCE(AVG(cnt), 0) FROM ("
            "  SELECT COUNT(*) AS cnt FROM usage_records "
            "  WHERE DATE(timestamp) >= DATE('now', ? || ' days') "
            "  GROUP BY buyer_id"
            ")",
            (str(-days),),
        ).fetchone()[0]

    return {
        "days": days,
        "total_buyers_all_time": total_buyers,
        "active_buyers": active_buyers,
        "repeat_buyers": repeat_buyers,
        "repeat_rate": round(repeat_buyers / active_buyers * 100, 1) if active_buyers > 0 else 0,
        "avg_calls_per_buyer": round(float(avg_calls), 1),
        "top_spenders": [
            {
                "buyer_id": r["buyer_id"],
                "calls": r["calls"],
                "total_spent": round(r["total_spent"], 2),
                "services_used": r["services_used"],
            }
            for r in top_spenders
        ],
    }


# --- Health Monitor ---

@router.post("/admin/health-check/run")
async def run_health_checks(request: Request):
    """Trigger health checks for all active services (admin only)."""
    require_admin(request)
    from marketplace.health_monitor import HealthMonitor

    db = request.app.state.db
    monitor = HealthMonitor(db)
    results = await monitor.check_all_services()

    return {
        "checked": len(results),
        "reachable": sum(1 for r in results if r.reachable),
        "unreachable": sum(1 for r in results if not r.reachable),
        "results": [
            {
                "service_id": r.service_id,
                "reachable": r.reachable,
                "latency_ms": r.latency_ms,
                "status_code": r.status_code,
                "error": r.error,
            }
            for r in results
        ],
    }


@router.get("/admin/health-check/scores")
async def health_scores(
    request: Request,
    days: int = Query(default=30, ge=1, le=90),
):
    """Get health quality scores for all services (admin only)."""
    require_admin(request)
    from marketplace.health_monitor import HealthMonitor

    db = request.app.state.db
    monitor = HealthMonitor(db)
    scores = monitor.get_all_health_scores(lookback_days=days)

    return {
        "lookback_days": days,
        "services": [
            {
                "service_id": s.service_id,
                "provider_id": s.provider_id,
                "quality_score": s.quality_score,
                "uptime_pct": s.uptime_pct,
                "avg_latency_ms": s.avg_latency_ms,
                "error_rate_pct": s.error_rate_pct,
                "check_count": s.check_count,
                "rank": s.rank,
                "last_checked": s.last_checked,
            }
            for s in scores
        ],
    }


@router.get("/admin/health-check/provider/{provider_id}")
async def provider_health(
    request: Request,
    provider_id: str,
    days: int = Query(default=30, ge=1, le=90),
):
    """Get health summary for a specific provider (admin only)."""
    require_admin(request)
    from marketplace.health_monitor import HealthMonitor

    db = request.app.state.db
    monitor = HealthMonitor(db)
    summary = monitor.get_provider_health_summary(provider_id, lookback_days=days)
    return summary


@router.get("/admin/providers/{provider_id}/commission")
async def provider_commission(request: Request, provider_id: str):
    """Get commission info for a specific provider (Growth Program status)."""
    require_admin(request)
    commission_engine = getattr(request.app.state, "commission_engine", None)
    if commission_engine is None:
        return {
            "provider_id": provider_id,
            "error": "Commission engine not configured",
            "default_rate": "0.10",
        }

    info = commission_engine.get_provider_commission_info(provider_id)
    return {
        "provider_id": info["provider_id"],
        "registered": info["registered"],
        "current_rate": str(info["current_rate"]),
        "current_tier": info["current_tier"],
        "registration_date": info["registration_date"],
        "month_number": info["month_number"],
        "next_tier_date": info["next_tier_date"],
        "next_tier_rate": str(info["next_tier_rate"]) if info["next_tier_rate"] is not None else None,
    }


@router.get("/admin/providers/{provider_id}/milestones")
async def provider_milestones(request: Request, provider_id: str):
    """Get milestone progress for a specific provider (admin view)."""
    require_admin(request)
    from marketplace.milestones import MilestoneTracker

    db = request.app.state.db
    tracker = MilestoneTracker(db)
    return tracker.get_progress(provider_id)


@router.get("/admin/platform-consumption")
async def platform_consumption_stats(
    request: Request,
    days: int = Query(default=30, ge=1, le=90),
):
    """Get platform agent consumption statistics (admin only)."""
    require_admin(request)
    from marketplace.platform_consumer import PlatformConsumer

    db = request.app.state.db
    consumer = PlatformConsumer(db)
    return consumer.get_consumption_stats(days=days)
