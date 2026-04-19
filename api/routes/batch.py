"""
Batch operations API for multi-agent fleet management.

Provides bulk key creation, bulk balance deposits, and aggregate usage
reporting across all keys belonging to an owner. All endpoints require
admin authentication.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import List

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from marketplace.auth import AuthError, hash_secret, generate_api_key

router = APIRouter(tags=["batch"])

# --- Constants ---

BATCH_KEYS_LIMIT = 10     # Max keys per single bulk-create request (capped to reduce DB round-trips)
BATCH_DEPOSITS_LIMIT = 100  # Max deposits per single bulk-deposit request

ALLOWED_ROLES = {"buyer", "provider", "admin"}
MAX_RATE_LIMIT = 300


# --- Auth helper (admin only) ---

def _require_admin(request: Request) -> str:
    """Validate admin Bearer token. Returns key_id. Raises HTTPException otherwise."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Admin Bearer token required")

    token = auth_header[7:]
    parts = token.split(":", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=401, detail="Invalid key format. Use key_id:secret")

    key_id, secret = parts
    auth_mgr = request.app.state.auth
    try:
        record = auth_mgr.validate(key_id, secret)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    if record["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    return key_id


# --- Request / Response models ---

class BatchCreateKeysRequest(BaseModel):
    count: int
    owner_id: str
    role: str = "buyer"
    rate_limit: int = 60

    @field_validator("count")
    @classmethod
    def _check_count(cls, v: int) -> int:
        if v < 1:
            raise ValueError("count must be at least 1")
        if v > BATCH_KEYS_LIMIT:
            raise ValueError(f"count cannot exceed {BATCH_KEYS_LIMIT}")
        return v

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str) -> str:
        if v not in ALLOWED_ROLES:
            raise ValueError(f"role must be one of {ALLOWED_ROLES}")
        return v

    @field_validator("rate_limit")
    @classmethod
    def _check_rate_limit(cls, v: int) -> int:
        if v < 1 or v > MAX_RATE_LIMIT:
            raise ValueError(f"rate_limit must be between 1 and {MAX_RATE_LIMIT}")
        return v


class DepositItem(BaseModel):
    buyer_id: str
    amount: str  # String to preserve decimal precision

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, v: str) -> str:
        try:
            d = Decimal(v)
        except InvalidOperation:
            raise ValueError("amount must be a valid decimal number")
        if d <= Decimal("0"):
            raise ValueError("amount must be positive")
        return v


class BatchDepositsRequest(BaseModel):
    deposits: List[DepositItem]

    @field_validator("deposits")
    @classmethod
    def _check_deposits(cls, v: List[DepositItem]) -> List[DepositItem]:
        if len(v) < 1:
            raise ValueError("deposits list must not be empty")
        if len(v) > BATCH_DEPOSITS_LIMIT:
            raise ValueError(f"deposits list cannot exceed {BATCH_DEPOSITS_LIMIT} items")
        return v


# --- Endpoints ---

@router.post("/batch/keys", status_code=201)
async def batch_create_keys(req: BatchCreateKeysRequest, request: Request):
    """Bulk-create API keys for an owner.

    Requires admin authentication. Cap: 10 keys per request (each requires a
    separate scrypt hash; larger batches are split across multiple requests).
    All keys share the same owner_id, role, and rate_limit.
    The raw secret for each key is returned once and cannot be retrieved again.

    Scrypt hashing is offloaded to a thread pool via ``run_in_executor``
    so the event loop is not blocked (~100ms per key).
    """
    _require_admin(request)

    auth_mgr = request.app.state.auth
    loop = asyncio.get_event_loop()

    def _create_one() -> tuple[str, str]:
        return auth_mgr.create_key(
            owner_id=req.owner_id,
            role=req.role,
            rate_limit=req.rate_limit,
        )

    # Run all scrypt-heavy key creations in parallel threads
    try:
        futures = [loop.run_in_executor(None, _create_one) for _ in range(req.count)]
        results = await asyncio.gather(*futures)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    created = [
        {"key_id": key_id, "api_key": f"{key_id}:{raw_secret}"}
        for key_id, raw_secret in results
    ]

    return {"keys": created, "count": len(created)}


@router.post("/batch/deposits")
async def batch_deposits(req: BatchDepositsRequest, request: Request):
    """Bulk-deposit funds into multiple buyer balances.

    Requires admin authentication. Cap: 100 deposits per request.
    Each deposit is processed independently; failures for one buyer do not
    block the remaining deposits. Failed items are included in the response
    with an ``error`` field instead of ``new_balance``.
    """
    _require_admin(request)

    db = request.app.state.db
    results: list[dict] = []
    total_deposited = Decimal("0")

    for item in req.deposits:
        amount = Decimal(item.amount)
        try:
            new_balance = db.credit_balance(item.buyer_id, amount)

            # Create a deposit record for audit trail
            now_iso = datetime.now(timezone.utc).isoformat()
            deposit_id = str(uuid.uuid4())
            db.insert_deposit({
                "id": deposit_id,
                "buyer_id": item.buyer_id,
                "amount": str(amount),
                "currency": "USDC",
                "payment_provider": "admin_batch",
                "payment_id": deposit_id,
                "payment_status": "confirmed",
                "confirmed_at": now_iso,
                "created_at": now_iso,
            })

            results.append({
                "buyer_id": item.buyer_id,
                "new_balance": str(new_balance.quantize(Decimal("0.01"))),
                "deposit_id": deposit_id,
            })
            total_deposited += amount
        except Exception as exc:
            results.append({
                "buyer_id": item.buyer_id,
                "error": str(exc),
            })

    return {
        "results": results,
        "total_deposited": str(total_deposited),
    }


@router.get("/batch/usage")
async def batch_usage(
    request: Request,
    owner_id: str = Query(..., description="Owner ID to aggregate usage for"),
    period_start: str | None = Query(None, description="Start date (ISO 8601, e.g. 2026-01-01). Defaults to first day of current month."),
    period_end: str | None = Query(None, description="End date (ISO 8601, e.g. 2026-01-31). Defaults to current timestamp."),
):
    """Aggregate API usage across all keys belonging to an owner for a specified date range.

    Requires admin authentication.

    If period_start and period_end are not provided, defaults to the current calendar month.
    Returns total call count, total spend, key count, and the billing period.
    """
    _require_admin(request)

    db = request.app.state.db

    # Collect all key_ids for this owner
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT key_id FROM api_keys WHERE owner_id = ?",
            (owner_id,),
        ).fetchall()

    key_ids = [row["key_id"] for row in rows]
    keys_count = len(key_ids)

    # Parse and validate date range
    now = datetime.now(timezone.utc)

    if period_start is None or period_end is None:
        # Default to current month (backward compatible)
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        period_label = now.strftime("%Y-%m")
    else:
        # Parse ISO 8601 date strings
        try:
            # Support both date-only and datetime formats
            if len(period_start) == 10:  # "YYYY-MM-DD"
                start_dt = datetime.fromisoformat(f"{period_start}T00:00:00").replace(tzinfo=timezone.utc)
            else:
                start_dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
                start_dt = start_dt.replace(tzinfo=timezone.utc)

            if len(period_end) == 10:  # "YYYY-MM-DD"
                end_dt = datetime.fromisoformat(f"{period_end}T23:59:59").replace(tzinfo=timezone.utc)
            else:
                end_dt = datetime.fromisoformat(period_end.replace("Z", "+00:00"))
                end_dt = end_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid date format: {str(exc)}")

        if start_dt > end_dt:
            raise HTTPException(status_code=400, detail="period_start must be before or equal to period_end")

        period_label = f"{start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')}"

    period_start_iso = start_dt.isoformat()
    period_end_iso = end_dt.isoformat()

    if not key_ids:
        return {
            "owner_id": owner_id,
            "total_calls": 0,
            "total_spend": "0.00",
            "keys_count": 0,
            "period": period_label,
        }

    # Aggregate usage for all buyer_ids (key_ids) owned by this owner
    placeholders = ",".join("?" * len(key_ids))
    with db.connect() as conn:
        row = conn.execute(
            f"""SELECT COUNT(*) AS total_calls,
                       COALESCE(SUM(amount_usd), 0) AS total_spend
                FROM usage_records
               WHERE buyer_id IN ({placeholders})
                 AND timestamp >= ?
                 AND timestamp <= ?""",
            (*key_ids, period_start_iso, period_end_iso),
        ).fetchone()

    total_calls = row["total_calls"] or 0
    total_spend = Decimal(str(row["total_spend"] or 0))

    return {
        "owner_id": owner_id,
        "total_calls": total_calls,
        "total_spend": str(total_spend.quantize(Decimal("0.01"))),
        "keys_count": keys_count,
        "period": period_label,
    }
