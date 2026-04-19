"""
Referral system for provider growth.

Providers can generate referral codes and earn 20% of platform commission
from usage by referred providers.
"""
from __future__ import annotations

import secrets
import string
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from .db import Database

# Referral payout rate: 20% of platform commission from referred provider usage
REFERRAL_PAYOUT_RATE = Decimal("0.20")

# Referral code length
CODE_LENGTH = 8

# Allowed characters for referral codes (alphanumeric, no ambiguous chars)
_CODE_ALPHABET = string.ascii_uppercase + string.digits


REFERRAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS referrals (
    id TEXT PRIMARY KEY,
    referrer_provider_id TEXT NOT NULL,
    referred_provider_id TEXT,
    referral_code TEXT NOT NULL UNIQUE,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    activated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_referrals_referrer
    ON referrals(referrer_provider_id);
CREATE INDEX IF NOT EXISTS idx_referrals_code
    ON referrals(referral_code);
CREATE INDEX IF NOT EXISTS idx_referrals_referred
    ON referrals(referred_provider_id);

CREATE TABLE IF NOT EXISTS referral_payouts (
    id TEXT PRIMARY KEY,
    referral_id TEXT NOT NULL,
    period TEXT NOT NULL,
    platform_revenue REAL NOT NULL DEFAULT 0,
    payout_amount REAL NOT NULL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payouts_referral
    ON referral_payouts(referral_id);
CREATE INDEX IF NOT EXISTS idx_payouts_period
    ON referral_payouts(period);
"""


def _generate_code() -> str:
    """Generate a unique 8-character alphanumeric referral code."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


@dataclass(frozen=True)
class ReferralStats:
    """Summary statistics for a provider's referral activity."""

    total_referred: int
    active: int
    pending: int
    total_earned: Decimal


class ReferralManager:
    """Manages referral codes, linking, and payout calculations."""

    def __init__(self, db: Database, commission_engine=None) -> None:
        self.db = db
        self._commission_engine = commission_engine
        self._init_schema()

    def _init_schema(self) -> None:
        """Create referral tables if they don't exist."""
        with self.db.connect() as conn:
            conn.executescript(REFERRAL_SCHEMA_SQL)

    def generate_code(self, provider_id: str) -> dict:
        """Generate a unique referral code for a provider.

        Each call creates a new code. A provider can have multiple codes.
        Returns the referral record dict.
        """
        # Generate a unique code with collision retry
        max_attempts = 10
        for _ in range(max_attempts):
            code = _generate_code()
            referral_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            try:
                with self.db.connect() as conn:
                    conn.execute(
                        """INSERT INTO referrals
                           (id, referrer_provider_id, referred_provider_id,
                            referral_code, status, created_at, activated_at)
                           VALUES (?, ?, NULL, ?, 'pending', ?, NULL)""",
                        (referral_id, provider_id, code, now),
                    )
                return {
                    "id": referral_id,
                    "referrer_provider_id": provider_id,
                    "referral_code": code,
                    "status": "pending",
                    "created_at": now,
                }
            except Exception:
                # Code collision — retry with a new code
                continue

        raise RuntimeError("Failed to generate unique referral code after max attempts")

    def apply_code(self, new_provider_id: str, code: str) -> dict:
        """Apply a referral code for a new provider.

        Links the referred provider to the referrer. The code must exist
        and be in 'pending' status. A provider cannot refer themselves.

        Returns the updated referral record.
        Raises ValueError on invalid input.
        """
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM referrals WHERE referral_code = ?",
                (code,),
            ).fetchone()

            if not row:
                raise ValueError("Invalid referral code")

            referral = dict(row)

            if referral["status"] != "pending":
                raise ValueError("Referral code already used")

            if referral["referrer_provider_id"] == new_provider_id:
                raise ValueError("Cannot use your own referral code")

            # Check if this provider is already referred by any code
            existing = conn.execute(
                "SELECT id FROM referrals WHERE referred_provider_id = ?",
                (new_provider_id,),
            ).fetchone()
            if existing:
                raise ValueError("Provider already referred")

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """UPDATE referrals
                   SET referred_provider_id = ?, status = 'active',
                       activated_at = ?
                   WHERE id = ?""",
                (new_provider_id, now, referral["id"]),
            )

            return {
                "id": referral["id"],
                "referrer_provider_id": referral["referrer_provider_id"],
                "referred_provider_id": new_provider_id,
                "referral_code": referral["referral_code"],
                "status": "active",
                "created_at": referral["created_at"],
                "activated_at": now,
            }

    def get_referrals(self, provider_id: str) -> list[dict]:
        """List all referrals where provider_id is the referrer."""
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM referrals
                   WHERE referrer_provider_id = ?
                   ORDER BY created_at DESC""",
                (provider_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def calculate_payout(
        self, referrer_id: str, period: str
    ) -> list[dict]:
        """Calculate referral payouts for a referrer in a given period.

        Payout = 20% of platform commission earned from each referred
        provider's usage during the period.

        The period string is used to filter usage_records timestamps
        (expects ISO format date prefix, e.g. '2026-03' for monthly).

        Returns list of payout records (one per active referral with usage).
        """
        payouts: list[dict] = []

        with self.db.connect() as conn:
            # Get all active referrals for this referrer
            referrals = conn.execute(
                """SELECT * FROM referrals
                   WHERE referrer_provider_id = ? AND status = 'active'""",
                (referrer_id,),
            ).fetchall()

            for ref_row in referrals:
                ref = dict(ref_row)
                referred_id = ref["referred_provider_id"]

                # Sum platform revenue from referred provider's usage in period
                usage_row = conn.execute(
                    """SELECT COALESCE(SUM(amount_usd), 0) as total_revenue
                       FROM usage_records
                       WHERE provider_id = ? AND timestamp LIKE ?""",
                    (referred_id, f"{period}%"),
                ).fetchone()

                total_revenue = Decimal(str(usage_row["total_revenue"]))
                if total_revenue <= 0:
                    continue

                # Get actual commission rate from CommissionEngine (dynamic, not hardcoded)
                if self._commission_engine is not None:
                    commission_rate = self._commission_engine.get_effective_rate(
                        referred_id, transaction_amount=total_revenue,
                    )
                else:
                    commission_rate = Decimal("0.10")  # fallback to standard
                # Referral payout is 20% of that platform commission
                platform_commission = total_revenue * commission_rate
                payout_amount = platform_commission * REFERRAL_PAYOUT_RATE

                payout_id = str(uuid.uuid4())
                now = datetime.now(timezone.utc).isoformat()

                # Check if payout already exists for this referral+period
                existing = conn.execute(
                    """SELECT id FROM referral_payouts
                       WHERE referral_id = ? AND period = ?""",
                    (ref["id"], period),
                ).fetchone()

                if existing:
                    # Update existing payout
                    conn.execute(
                        """UPDATE referral_payouts
                           SET platform_revenue = ?, payout_amount = ?
                           WHERE referral_id = ? AND period = ?""",
                        (
                            float(str(total_revenue)),
                            float(str(payout_amount)),
                            ref["id"],
                            period,
                        ),
                    )
                    payout_id = existing["id"]
                else:
                    conn.execute(
                        """INSERT INTO referral_payouts
                           (id, referral_id, period, platform_revenue,
                            payout_amount, status, created_at)
                           VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                        (
                            payout_id,
                            ref["id"],
                            period,
                            float(str(total_revenue)),
                            float(str(payout_amount)),
                            now,
                        ),
                    )

                payouts.append({
                    "id": payout_id,
                    "referral_id": ref["id"],
                    "referred_provider_id": referred_id,
                    "period": period,
                    "platform_revenue": total_revenue,
                    "payout_amount": payout_amount,
                    "status": "pending",
                })

        return payouts

    def get_stats(self, provider_id: str) -> dict:
        """Get summary referral stats for a provider.

        Returns dict with total_referred, active, pending, total_earned.
        """
        with self.db.connect() as conn:
            # Count referrals by status
            total_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM referrals WHERE referrer_provider_id = ?",
                (provider_id,),
            ).fetchone()
            total_referred = total_row["cnt"] if total_row else 0

            active_row = conn.execute(
                """SELECT COUNT(*) as cnt FROM referrals
                   WHERE referrer_provider_id = ? AND status = 'active'""",
                (provider_id,),
            ).fetchone()
            active = active_row["cnt"] if active_row else 0

            pending = total_referred - active

            # Total earned from payouts
            earned_row = conn.execute(
                """SELECT COALESCE(SUM(rp.payout_amount), 0) as total
                   FROM referral_payouts rp
                   JOIN referrals r ON rp.referral_id = r.id
                   WHERE r.referrer_provider_id = ?""",
                (provider_id,),
            ).fetchone()
            total_earned = Decimal(str(earned_row["total"])) if earned_row else Decimal("0")

        return {
            "provider_id": provider_id,
            "total_referred": total_referred,
            "active": active,
            "pending": pending,
            "total_earned": total_earned,
        }
