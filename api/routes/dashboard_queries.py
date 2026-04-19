"""
Read-only database query functions for the admin dashboard.
All functions accept a database connection and return immutable data.
No side effects — pure query + transform logic.
Uses Decimal for all financial calculations to avoid float precision issues.
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

_MAX_SLA_LATENCY = 5000  # default SLA latency threshold in ms
_MAX_SLA_ERROR_RATE = 5.0  # default SLA error rate threshold %


# ---------------------------------------------------------------------------
# Formatting / math helpers (pure functions)
# ---------------------------------------------------------------------------

def fmt_number(value: int | float, decimals: int = 0) -> str:
    """Format a number with comma separators and optional decimal places."""
    if decimals > 0:
        return f"{value:,.{decimals}f}"
    return f"{int(value):,}"


def safe_pct(numerator: float, denominator: float) -> float:
    """Calculate percentage safely, returning 0.0 on zero denominator."""
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * 100, 1)


def clamp_page(page: int, total_pages: int) -> int:
    """Clamp page number to valid range."""
    return max(1, min(page, max(1, total_pages)))


def build_page_range(current: int, total: int, window: int = 5) -> list[int]:
    """Build a list of page numbers around the current page."""
    half = window // 2
    start = max(1, current - half)
    end = min(total, start + window - 1)
    start = max(1, end - window + 1)
    return list(range(start, end + 1))


def percentile(sorted_values: list[float], pct: float) -> float:
    """Calculate the given percentile from a sorted list of values."""
    if not sorted_values:
        return 0.0
    k = (pct / 100) * (len(sorted_values) - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_values[int(k)])
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


def _to_decimal(value, places: int = 2) -> float:
    """Convert a DB numeric value to a rounded float via Decimal.

    Uses Decimal for intermediate arithmetic to avoid float precision issues
    (e.g. 0.1 + 0.2 != 0.3 in float). Returns float for JSON serialization.
    """
    if value is None:
        return 0.0
    quantize_str = "0." + "0" * places
    return float(Decimal(str(value)).quantize(Decimal(quantize_str)))


def sla_status(
    error_rate: float,
    p95_latency: float,
    total_calls: int,
) -> str:
    """Determine SLA compliance status for a service."""
    if total_calls == 0:
        return "no_data"
    if error_rate > _MAX_SLA_ERROR_RATE or p95_latency > _MAX_SLA_LATENCY:
        return "violation"
    if error_rate > _MAX_SLA_ERROR_RATE * 0.6 or p95_latency > _MAX_SLA_LATENCY * 0.8:
        return "warning"
    return "compliant"


# ---------------------------------------------------------------------------
# Query functions (read-only DB access, return immutable data)
# ---------------------------------------------------------------------------

_PER_PAGE = 25


def query_overview_stats(conn) -> list[dict[str, Any]]:
    """Gather platform-level summary statistics."""
    svc_cnt = conn.execute(
        "SELECT COUNT(*) AS cnt FROM services WHERE status = 'active'"
    ).fetchone()["cnt"]

    agent_cnt = conn.execute(
        "SELECT COUNT(*) AS cnt FROM agent_identities WHERE status = 'active'"
    ).fetchone()["cnt"]

    usage_row = conn.execute(
        "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount_usd), 0) AS revenue "
        "FROM usage_records"
    ).fetchone()

    team_cnt = conn.execute(
        "SELECT COUNT(*) AS cnt FROM teams WHERE status = 'active'"
    ).fetchone()["cnt"]

    settle_cnt = conn.execute(
        "SELECT COUNT(*) AS cnt FROM settlements"
    ).fetchone()["cnt"]

    revenue = _to_decimal(usage_row["revenue"])
    return [
        {"label": "Services", "value": fmt_number(svc_cnt), "is_revenue": False},
        {"label": "Agents", "value": fmt_number(agent_cnt), "is_revenue": False},
        {"label": "Transactions", "value": fmt_number(usage_row["cnt"]), "is_revenue": False},
        {"label": "Revenue (USD)", "value": "$" + fmt_number(revenue, 2), "is_revenue": True},
        {"label": "Settlements", "value": fmt_number(settle_cnt), "is_revenue": False},
    ]


def query_payment_methods(conn) -> list[dict[str, Any]]:
    """Revenue breakdown by payment provider."""
    rows = conn.execute(
        """SELECT payment_method,
                  COUNT(*) AS count,
                  COALESCE(SUM(amount_usd), 0) AS total_usd
           FROM usage_records
           GROUP BY payment_method
           ORDER BY total_usd DESC"""
    ).fetchall()

    total_rev = sum(Decimal(str(r["total_usd"])) for r in rows) or Decimal("1")
    return [
        {
            "method": row["payment_method"] or "unknown",
            "count": row["count"],
            "total_usd": _to_decimal(row["total_usd"]),
            "pct": safe_pct(float(Decimal(str(row["total_usd"]))), float(total_rev)),
        }
        for row in rows
    ]


def query_recent_transactions(conn, limit: int = 20) -> list[dict[str, Any]]:
    """Fetch the most recent usage records."""
    rows = conn.execute(
        "SELECT * FROM usage_records ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def query_services_with_stats(conn) -> list[dict[str, Any]]:
    """List all services (any status) with aggregated call stats."""
    rows = conn.execute(
        """SELECT
               s.*,
               COALESCE(u.call_count, 0) AS call_count,
               COALESCE(u.avg_latency, 0) AS avg_latency_ms
           FROM services s
           LEFT JOIN (
               SELECT service_id,
                      COUNT(*) AS call_count,
                      AVG(latency_ms) AS avg_latency
               FROM usage_records
               GROUP BY service_id
           ) u ON u.service_id = s.id
           ORDER BY s.created_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def query_agents_with_relations(conn) -> list[dict[str, Any]]:
    """List all active agents with their provided/consumed service lists."""
    agents = conn.execute(
        "SELECT * FROM agent_identities WHERE status = 'active' "
        "ORDER BY reputation_score DESC, created_at DESC"
    ).fetchall()

    result = []
    for row in agents:
        agent = dict(row)
        provided = conn.execute(
            "SELECT id, name FROM services WHERE provider_id = ? AND status = 'active'",
            (agent["owner_id"],),
        ).fetchall()
        agent["provided_services"] = [dict(s) for s in provided]

        consumed = conn.execute(
            """SELECT DISTINCT s.id, s.name
               FROM usage_records u
               JOIN services s ON s.id = u.service_id
               WHERE u.buyer_id = ?""",
            (agent["owner_id"],),
        ).fetchall()
        agent["consumed_services"] = [dict(s) for s in consumed]
        result.append(agent)
    return result


def query_transactions_filtered(
    conn,
    *,
    service_id: str = "",
    buyer_id: str = "",
    payment_method: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    per_page: int = _PER_PAGE,
) -> tuple[list[dict], int]:
    """Fetch paginated, filtered transactions. Returns (rows, total_count)."""
    conditions: list[str] = []
    params: list[str | int] = []

    if service_id:
        conditions.append("service_id = ?")
        params.append(service_id)
    if buyer_id:
        conditions.append("buyer_id = ?")
        params.append(buyer_id)
    if payment_method:
        conditions.append("payment_method = ?")
        params.append(payment_method)
    if date_from:
        conditions.append("DATE(timestamp) >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("DATE(timestamp) <= ?")
        params.append(date_to)

    where = " AND ".join(conditions) if conditions else "1=1"

    count_row = conn.execute(
        f"SELECT COUNT(*) AS cnt FROM usage_records WHERE {where}", params
    ).fetchone()
    total = count_row["cnt"]

    offset = (page - 1) * per_page
    data_params = list(params) + [per_page, offset]
    rows = conn.execute(
        f"SELECT * FROM usage_records WHERE {where} "
        f"ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        data_params,
    ).fetchall()

    return [dict(r) for r in rows], total


def query_available_payment_methods(conn) -> list[str]:
    """Get distinct payment methods used in transactions."""
    rows = conn.execute(
        "SELECT DISTINCT payment_method FROM usage_records ORDER BY payment_method"
    ).fetchall()
    return [r["payment_method"] for r in rows if r["payment_method"]]


def query_provider_stats(conn) -> list[dict[str, Any]]:
    """Per-provider revenue, commission, call counts, latency, and success rate."""
    rows = conn.execute(
        """SELECT
               u.provider_id,
               COUNT(*) AS total_calls,
               COALESCE(SUM(u.amount_usd), 0) AS total_revenue,
               COALESCE(AVG(u.latency_ms), 0) AS avg_latency,
               COALESCE(
                   CAST(SUM(CASE WHEN u.status_code < 400 THEN 1 ELSE 0 END) AS REAL)
                   / NULLIF(COUNT(*), 0) * 100, 0
               ) AS success_rate,
               MIN(u.timestamp) AS first_call,
               COUNT(DISTINCT u.service_id) AS service_count
           FROM usage_records u
           GROUP BY u.provider_id
           ORDER BY total_revenue DESC"""
    ).fetchall()

    providers = []
    for row in rows:
        pid = row["provider_id"]

        # 7-day calls
        calls_7d = conn.execute(
            "SELECT COUNT(*) AS cnt FROM usage_records "
            "WHERE provider_id = ? AND timestamp >= datetime('now', '-7 days')",
            (pid,),
        ).fetchone()["cnt"]

        # 30-day calls
        calls_30d = conn.execute(
            "SELECT COUNT(*) AS cnt FROM usage_records "
            "WHERE provider_id = ? AND timestamp >= datetime('now', '-30 days')",
            (pid,),
        ).fetchone()["cnt"]

        # Commission paid (platform_fee from settlements)
        commission_row = conn.execute(
            "SELECT COALESCE(SUM(platform_fee), 0) AS total_commission "
            "FROM settlements WHERE provider_id = ?",
            (pid,),
        ).fetchone()
        commission_paid = _to_decimal(commission_row["total_commission"])

        # Net payout (net_amount from settlements)
        payout_row = conn.execute(
            "SELECT COALESCE(SUM(net_amount), 0) AS total_payout "
            "FROM settlements WHERE provider_id = ? AND status = 'completed'",
            (pid,),
        ).fetchone()
        net_payout = _to_decimal(payout_row["total_payout"])

        total_rev = _to_decimal(row["total_revenue"])

        providers.append({
            "provider_id": pid,
            "total_revenue": total_rev,
            "commission_paid": commission_paid,
            "net_payout": net_payout,
            "total_calls": row["total_calls"],
            "calls_7d": calls_7d,
            "calls_30d": calls_30d,
            "avg_latency": round(row["avg_latency"], 1),
            "success_rate": round(row["success_rate"], 1),
            "service_count": row["service_count"],
            "first_call": row["first_call"],
        })

    return providers


def query_provider_quality_tiers(conn) -> dict[str, str]:
    """Determine quality tier per provider based on success rate and latency.

    Returns a mapping of provider_id to tier name (Standard/Verified/Premium).
    Tier thresholds mirror marketplace.commission.QUALITY_TIERS logic:
      Premium:  success_rate >= 99% AND avg_latency <= 200ms
      Verified: success_rate >= 95% AND avg_latency <= 500ms
      Standard: everything else
    """
    rows = conn.execute(
        """SELECT
               provider_id,
               COALESCE(
                   CAST(SUM(CASE WHEN status_code < 400 THEN 1 ELSE 0 END) AS REAL)
                   / NULLIF(COUNT(*), 0) * 100, 0
               ) AS success_rate,
               COALESCE(AVG(latency_ms), 0) AS avg_latency
           FROM usage_records
           GROUP BY provider_id"""
    ).fetchall()

    tiers: dict[str, str] = {}
    for row in rows:
        sr = row["success_rate"]
        lat = row["avg_latency"]
        if sr >= 99.0 and lat <= 200:
            tiers[row["provider_id"]] = "Premium"
        elif sr >= 95.0 and lat <= 500:
            tiers[row["provider_id"]] = "Verified"
        else:
            tiers[row["provider_id"]] = "Standard"
    return tiers


def query_provider_growth_trend(conn) -> list[dict[str, Any]]:
    """Weekly count of new provider registrations (from services table).

    Returns a list of dicts with 'week' (ISO date of Monday) and 'new_providers'.
    """
    rows = conn.execute(
        """SELECT
               DATE(created_at, 'weekday 0', '-6 days') AS week_start,
               COUNT(DISTINCT provider_id) AS new_providers
           FROM services
           GROUP BY week_start
           ORDER BY week_start DESC
           LIMIT 12"""
    ).fetchall()

    return [
        {"week": row["week_start"], "new_providers": row["new_providers"]}
        for row in rows
    ]


def query_provider_summary(conn) -> dict[str, Any]:
    """Aggregate summary stats across all providers."""
    total_providers = conn.execute(
        "SELECT COUNT(DISTINCT provider_id) AS cnt FROM usage_records"
    ).fetchone()["cnt"]

    total_revenue = conn.execute(
        "SELECT COALESCE(SUM(amount_usd), 0) AS rev FROM usage_records"
    ).fetchone()["rev"]

    total_commission = conn.execute(
        "SELECT COALESCE(SUM(platform_fee), 0) AS fee FROM settlements"
    ).fetchone()["fee"]

    return {
        "total_providers": total_providers,
        "total_revenue": _to_decimal(total_revenue),
        "total_commission": _to_decimal(total_commission),
    }


def query_quality_metrics(conn) -> tuple[list[dict], dict]:
    """
    Compute per-service quality metrics and overall summary.
    Returns (service_metrics, summary_dict).
    """
    rows = conn.execute(
        """SELECT
               s.id AS service_id,
               s.name,
               COUNT(u.id) AS total_calls,
               COALESCE(AVG(u.latency_ms), 0) AS avg_latency,
               COALESCE(
                   CAST(SUM(CASE WHEN u.status_code < 400 THEN 1 ELSE 0 END) AS REAL)
                   / NULLIF(COUNT(u.id), 0) * 100, 0
               ) AS success_rate,
               COALESCE(
                   CAST(SUM(CASE WHEN u.status_code >= 500 THEN 1 ELSE 0 END) AS REAL)
                   / NULLIF(COUNT(u.id), 0) * 100, 0
               ) AS error_rate
           FROM services s
           LEFT JOIN usage_records u ON u.service_id = s.id
           WHERE s.status = 'active'
           GROUP BY s.id
           ORDER BY s.name"""
    ).fetchall()

    services_data = []
    all_latencies: list[float] = []

    for row in rows:
        sid = row["service_id"]
        latency_rows = conn.execute(
            "SELECT latency_ms FROM usage_records WHERE service_id = ? "
            "ORDER BY latency_ms",
            (sid,),
        ).fetchall()
        latencies = [r["latency_ms"] for r in latency_rows]
        all_latencies.extend(latencies)

        p95 = percentile(latencies, 95) if latencies else 0.0
        error_rate_val = round(row["error_rate"], 1)
        sla = sla_status(error_rate_val, p95, row["total_calls"])

        services_data.append({
            "service_id": sid,
            "name": row["name"],
            "total_calls": row["total_calls"],
            "success_rate": round(row["success_rate"], 1),
            "error_rate": error_rate_val,
            "avg_latency": round(row["avg_latency"], 0),
            "p95_latency": round(p95, 0),
            "sla_status": sla,
        })

    compliant_count = sum(1 for s in services_data if s["sla_status"] == "compliant")
    total_with_data = sum(1 for s in services_data if s["sla_status"] != "no_data")

    summary = {
        "avg_sla": safe_pct(compliant_count, total_with_data) if total_with_data else 100.0,
        "avg_error_rate": (
            round(sum(s["error_rate"] for s in services_data) / len(services_data), 1)
            if services_data else 0.0
        ),
        "p50_latency": percentile(sorted(all_latencies), 50) if all_latencies else 0.0,
        "p95_latency": percentile(sorted(all_latencies), 95) if all_latencies else 0.0,
    }

    return services_data, summary
