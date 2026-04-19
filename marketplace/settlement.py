"""
Settlement engine — calculates and records provider payouts.
Aggregates usage records into settlement batches.
Supports USDC payouts via WalletManager.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from .db import Database

logger = logging.getLogger("settlement")


class SettlementError(Exception):
    """Settlement processing errors."""


class SettlementEngine:
    """
    Processes provider payouts by aggregating usage records.

    Flow:
    1. Query usage records for a period
    2. Calculate total revenue per provider
    3. Subtract platform fee (dynamic via CommissionEngine, or fixed fallback)
    4. Create settlement record
    """

    def __init__(
        self,
        db: Database,
        platform_fee_pct: Decimal = Decimal("0.10"),
        wallet_manager: Optional["WalletManager"] = None,
        commission_engine: Optional["CommissionEngine"] = None,
    ):
        self.db = db
        self.platform_fee_pct = platform_fee_pct
        self.wallet = wallet_manager
        self.commission_engine = commission_engine

    def calculate_settlement(
        self,
        provider_id: str,
        period_start: str,
        period_end: str,
    ) -> dict:
        """
        Calculate settlement for a provider in a given period.

        Uses per-record commission_rate snapshots when available (R16-M4 fix).
        Falls back to CommissionEngine live rate or fixed platform_fee_pct
        for records without a snapshot.

        Returns a settlement summary dict (not yet persisted).
        """
        if not provider_id:
            raise SettlementError("provider_id is required")

        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT amount_usd, commission_rate
                   FROM usage_records
                   WHERE provider_id = ?
                     AND timestamp >= ?
                     AND timestamp < ?
                     AND status_code < 500""",
                (provider_id, period_start, period_end),
            ).fetchall()

        # Determine the fallback rate for records without a snapshot
        if self.commission_engine is not None:
            fallback_rate = self.commission_engine.get_commission_rate(provider_id)
        else:
            fallback_rate = self.platform_fee_pct

        total = Decimal("0")
        platform_fee = Decimal("0")
        call_count = len(rows)

        for r in rows:
            amt = Decimal(str(r["amount_usd"]))
            total += amt

            snapshot = r["commission_rate"]
            if snapshot is not None:
                rate = Decimal(str(snapshot))
            else:
                rate = fallback_rate

            platform_fee += amt * rate

        net_amount = total - platform_fee

        return {
            "provider_id": provider_id,
            "period_start": period_start,
            "period_end": period_end,
            "call_count": call_count,
            "total_amount": total,
            "platform_fee": platform_fee,
            "net_amount": net_amount,
        }

    def create_settlement(
        self,
        provider_id: str,
        period_start: str,
        period_end: str,
    ) -> dict:
        """
        Calculate and persist a settlement record.

        Returns the created settlement with ID.
        """
        # Guard against duplicate settlements for the same provider+period.
        # Uses an exclusive transaction to serialize concurrent create calls.
        with self.db.connect() as conn:
            conn.execute("BEGIN EXCLUSIVE")
            try:
                existing = conn.execute(
                    """SELECT id FROM settlements
                       WHERE provider_id = ?
                         AND period_start = ?
                         AND period_end = ?
                         AND status IN ('pending', 'processing', 'completed')""",
                    (provider_id, period_start, period_end),
                ).fetchone()
                if existing:
                    conn.execute("COMMIT")
                    raise SettlementError(
                        f"Settlement already exists for provider {provider_id} "
                        f"period {period_start}..{period_end}: {existing['id']}"
                    )
                conn.execute("COMMIT")
            except SettlementError:
                raise
            except Exception:
                conn.execute("ROLLBACK")
                raise

        summary = self.calculate_settlement(
            provider_id, period_start, period_end
        )

        settlement_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO settlements
                   (id, provider_id, period_start, period_end,
                    total_amount, platform_fee, net_amount, status, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    settlement_id,
                    provider_id,
                    period_start,
                    period_end,
                    float(summary["total_amount"]),
                    float(summary["platform_fee"]),
                    float(summary["net_amount"]),
                    "pending",
                    now,
                ),
            )

        # Link usage records to this settlement for audit traceability.
        self.db.link_usage_to_settlement(
            settlement_id, provider_id, period_start, period_end,
        )

        return {
            "id": settlement_id,
            **summary,
            "status": "pending",
        }

    def list_settlements(
        self,
        provider_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List settlement records with optional filters."""
        conditions = []
        params: list = []

        if provider_id:
            conditions.append("provider_id = ?")
            params.append(provider_id)
        if status:
            conditions.append("status = ?")
            params.append(status)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        with self.db.connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM settlements
                    WHERE {where}
                    ORDER BY period_end DESC
                    LIMIT ?""",
                params,
            ).fetchall()

        return [
            {
                "id": r["id"],
                "provider_id": r["provider_id"],
                "period_start": r["period_start"],
                "period_end": r["period_end"],
                "total_amount": Decimal(str(r["total_amount"])),
                "platform_fee": Decimal(str(r["platform_fee"])),
                "net_amount": Decimal(str(r["net_amount"])),
                "status": r["status"],
                "payment_tx": r["payment_tx"],
            }
            for r in rows
        ]

    def mark_paid(self, settlement_id: str, payment_tx: str) -> bool:
        """Mark a settlement as paid with transaction hash.

        Accepts settlements in 'pending' or 'processing' state — the latter
        is the expected state during execute_payout() which transitions
        pending → processing → completed.
        """
        with self.db.connect() as conn:
            cursor = conn.execute(
                """UPDATE settlements
                   SET status = 'completed', payment_tx = ?,
                       updated_at = ?
                   WHERE id = ? AND status IN ('pending', 'processing')""",
                (payment_tx, datetime.now(timezone.utc).isoformat(), settlement_id),
            )
            return cursor.rowcount > 0

    def recover_stuck_settlements(self, timeout_hours: int = 24) -> list[dict]:
        """
        Find settlements stuck in 'processing' state and move them to 'failed'.

        A settlement is considered stuck if it has been in 'processing' state for
        longer than timeout_hours without completing. This can happen when the
        process crashes mid-payout or the wallet call hangs.

        Returns a list of settlement IDs that were recovered (moved to 'failed').
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=timeout_hours)
        ).isoformat()

        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT id FROM settlements
                   WHERE status = 'processing'
                     AND updated_at < ?""",
                (cutoff,),
            ).fetchall()

        recovered = []
        for row in rows:
            settlement_id = row["id"]
            with self.db.connect() as conn:
                conn.execute(
                    """UPDATE settlements
                       SET status = 'failed',
                           notes = COALESCE(notes || ' | ', '') ||
                                   'Auto-recovered: stuck in processing > ' ||
                                   ? || 'h',
                           updated_at = ?
                       WHERE id = ? AND status = 'processing'""",
                    (
                        str(timeout_hours),
                        datetime.now(timezone.utc).isoformat(),
                        settlement_id,
                    ),
                )
            recovered.append({"settlement_id": settlement_id, "status": "failed"})
            logger.warning(
                "Recovered stuck settlement %s (was processing > %dh)",
                settlement_id, timeout_hours,
            )

        return recovered

    def retry_failed_settlements(self, max_attempts: int = 3) -> list[dict]:
        """Move eligible 'failed' settlements back to 'pending' for retry.

        A settlement is eligible for retry if:
        - Status is 'failed'
        - The notes field indicates fewer than *max_attempts* retry cycles

        Returns a list of settlement IDs that were re-queued.
        """
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT id, notes FROM settlements WHERE status = 'failed'",
            ).fetchall()

        retried = []
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            notes = row["notes"] or ""
            retry_count = notes.count("retry→pending")
            if retry_count >= max_attempts:
                continue  # exhausted retries

            with self.db.connect() as conn:
                conn.execute(
                    """UPDATE settlements
                       SET status = 'pending',
                           notes = COALESCE(notes || ' | ', '') ||
                                   'retry→pending at ' || ?,
                           updated_at = ?
                       WHERE id = ? AND status = 'failed'""",
                    (now, now, row["id"]),
                )
            retried.append({"settlement_id": row["id"], "status": "pending"})
            logger.info("Retried failed settlement %s (attempt %d/%d)",
                        row["id"], retry_count + 1, max_attempts)

        return retried

    async def execute_payout(
        self,
        settlement_id: str,
        provider_wallet: str,
    ) -> dict:
        """
        Execute USDC payout for a pending settlement.

        Uses WalletManager to send USDC to the provider's wallet.
        Returns result dict with status and optional tx hash.
        """
        if not self.wallet or not self.wallet.is_ready:
            return {
                "settlement_id": settlement_id,
                "status": "skipped",
                "reason": "Wallet not configured",
            }

        # Get settlement details
        settlements = self.list_settlements(status="pending")
        target = None
        for s in settlements:
            if s["id"] == settlement_id:
                target = s
                break

        if not target:
            raise SettlementError(f"Settlement {settlement_id} not found or not pending")

        if target["net_amount"] <= 0:
            return {
                "settlement_id": settlement_id,
                "status": "skipped",
                "reason": "Zero amount",
            }

        # Verify wallet matches provider's registered address
        provider_record = self.db.get_agent(target["provider_id"])
        if provider_record:
            registered_wallet = provider_record.get("wallet_address")
            if registered_wallet and registered_wallet != provider_wallet:
                raise SettlementError(
                    "provider_wallet does not match registered wallet address"
                )

        # Mark processing atomically — only if still 'pending' to prevent double-payout
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            cur = conn.execute(
                "UPDATE settlements SET status = 'processing', updated_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (now, settlement_id),
            )
            if cur.rowcount == 0:
                raise SettlementError(
                    f"Settlement {settlement_id} is not in 'pending' state — "
                    f"cannot start payout (possible concurrent execution)"
                )

        # Execute transfer with provider-side failure recovery.
        # If the wallet call raises or returns falsy, we roll back to
        # 'failed' so recover_stuck_settlements() can retry later.
        tx_hash = None
        try:
            tx_hash = await self.wallet.transfer_usdc(
                to_address=provider_wallet,
                amount=target["net_amount"],
            )
        except Exception as exc:
            logger.error(
                "Payout exception for settlement %s: %s", settlement_id, exc,
            )

        if tx_hash:
            marked = self.mark_paid(settlement_id, tx_hash)
            if not marked:
                logger.error(
                    "mark_paid failed for settlement %s (tx: %s) — state may be inconsistent",
                    settlement_id, tx_hash,
                )
            logger.info(
                "Payout complete: %s USDC → %s (tx: %s)",
                target["net_amount"], provider_wallet, tx_hash,
            )
            return {
                "settlement_id": settlement_id,
                "status": "completed",
                "tx_hash": tx_hash,
                "amount": str(target["net_amount"]),
            }

        # Transfer failed — move to 'failed' so it can be retried via
        # recover_stuck_settlements() or manual re-trigger.
        fail_now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE settlements SET status = 'failed', "
                "notes = COALESCE(notes || ' | ', '') || 'Payout failed at ' || ?, "
                "updated_at = ? WHERE id = ?",
                (fail_now, fail_now, settlement_id),
            )
        logger.error("Payout failed for settlement %s", settlement_id)
        return {
            "settlement_id": settlement_id,
            "status": "failed",
            "reason": "USDC transfer failed or raised exception",
        }
