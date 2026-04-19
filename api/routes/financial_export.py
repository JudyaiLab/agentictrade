"""
Financial data export API for reconciliation.

Admin-only endpoint that returns settlements, deposits, and usage records
as a JSON export with date range filters.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request

from api.deps import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


def _to_decimal(value) -> str:
    """Convert DB value to Decimal string for precise financial output."""
    if value is None:
        return "0.00"
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


@router.get("/financial-export")
async def financial_export(
    request: Request,
    date_from: str = Query("", description="Start date (YYYY-MM-DD)"),
    date_to: str = Query("", description="End date (YYYY-MM-DD)"),
    limit: int = Query(1000, ge=1, le=10000, description="Max records per category"),
):
    """Export financial data for reconciliation.

    Returns settlements, deposits (escrow), and usage records as JSON.
    Admin-only. Supports date range filtering.
    """
    require_admin(request)

    db = request.app.state.db

    # Validate date format if provided
    for label, val in [("date_from", date_from), ("date_to", date_to)]:
        if val:
            try:
                datetime.strptime(val, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid {label} format. Use YYYY-MM-DD",
                )

    # Convert YYYY-MM-DD boundaries to ISO timestamp strings for portable
    # comparison (works with both SQLite and PostgreSQL; no DATE() needed).
    ts_from = f"{date_from}T00:00:00" if date_from else ""
    ts_to = f"{date_to}T23:59:59" if date_to else ""

    with db.connect() as conn:
        # --- Settlements ---
        settle_conditions: list[str] = []
        settle_params: list = []
        if date_from:
            settle_conditions.append("period_start >= ?")
            settle_params.append(ts_from)
        if date_to:
            settle_conditions.append("period_end <= ?")
            settle_params.append(ts_to)
        settle_where = " AND ".join(settle_conditions) if settle_conditions else "1=1"
        settle_params.append(limit)

        settlement_rows = conn.execute(
            f"SELECT * FROM settlements WHERE {settle_where} "
            f"ORDER BY period_start DESC LIMIT ?",
            settle_params,
        ).fetchall()
        settlements = []
        for r in settlement_rows:
            row = dict(r)
            for field in ("total_amount", "platform_fee", "net_amount"):
                if field in row:
                    row[field] = _to_decimal(row[field])
            settlements.append(row)

        settlement_count = conn.execute(
            f"SELECT COUNT(*) as cnt FROM settlements WHERE {settle_where}",
            settle_params[:-1],  # exclude limit
        ).fetchone()["cnt"]

        # --- Usage records ---
        usage_conditions: list[str] = []
        usage_params: list = []
        if date_from:
            usage_conditions.append("timestamp >= ?")
            usage_params.append(ts_from)
        if date_to:
            usage_conditions.append("timestamp <= ?")
            usage_params.append(ts_to)
        usage_where = " AND ".join(usage_conditions) if usage_conditions else "1=1"
        usage_params.append(limit)

        usage_rows = conn.execute(
            f"SELECT * FROM usage_records WHERE {usage_where} "
            f"ORDER BY timestamp DESC LIMIT ?",
            usage_params,
        ).fetchall()
        usage_records = []
        for r in usage_rows:
            row = dict(r)
            if "amount_usd" in row:
                row["amount_usd"] = _to_decimal(row["amount_usd"])
            usage_records.append(row)

        usage_count = conn.execute(
            f"SELECT COUNT(*) as cnt FROM usage_records WHERE {usage_where}",
            usage_params[:-1],
        ).fetchone()["cnt"]

        # --- Revenue summary ---
        rev_params = []
        rev_conditions = []
        if date_from:
            rev_conditions.append("timestamp >= ?")
            rev_params.append(ts_from)
        if date_to:
            rev_conditions.append("timestamp <= ?")
            rev_params.append(ts_to)
        rev_where = " AND ".join(rev_conditions) if rev_conditions else "1=1"

        rev_row = conn.execute(
            f"SELECT COUNT(*) as cnt, COALESCE(SUM(amount_usd), 0) as total "
            f"FROM usage_records WHERE {rev_where}",
            rev_params,
        ).fetchone()

        # --- Escrow deposits (if table exists) ---
        deposits = []
        deposit_count = 0
        try:
            dep_conditions: list[str] = []
            dep_params: list = []
            if date_from:
                dep_conditions.append("created_at >= ?")
                dep_params.append(ts_from)
            if date_to:
                dep_conditions.append("created_at <= ?")
                dep_params.append(ts_to)
            dep_where = " AND ".join(dep_conditions) if dep_conditions else "1=1"
            dep_params.append(limit)

            dep_rows = conn.execute(
                f"SELECT * FROM escrow_holds WHERE {dep_where} "
                f"ORDER BY created_at DESC LIMIT ?",
                dep_params,
            ).fetchall()
            for r in dep_rows:
                row = dict(r)
                if "amount" in row:
                    row["amount"] = _to_decimal(row["amount"])
                deposits.append(row)

            deposit_count = conn.execute(
                f"SELECT COUNT(*) as cnt FROM escrow_holds WHERE {dep_where}",
                dep_params[:-1],
            ).fetchone()["cnt"]
        except Exception:
            # escrow_holds table may not exist
            pass

    now = datetime.now(timezone.utc).isoformat()

    return {
        "exported_at": now,
        "filters": {
            "date_from": date_from or None,
            "date_to": date_to or None,
            "limit": limit,
        },
        "summary": {
            "total_transactions": usage_count,
            "total_revenue_usd": _to_decimal(rev_row["total"]),
            "total_settlements": settlement_count,
            "total_deposits": deposit_count,
        },
        "settlements": settlements,
        "usage_records": usage_records,
        "deposits": deposits,
    }
