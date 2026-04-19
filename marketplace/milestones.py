"""
Provider Milestone Tracker — gamification for provider retention.

Tracks cumulative earnings and awards milestones at defined thresholds.

Milestones:
  $50  → Active Seller badge
  $200 → Commission reduction (10% → 8%)
  $500 → $25 cashback credit
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from .db import Database

logger = logging.getLogger("milestones")


@dataclass(frozen=True)
class MilestoneDef:
    """Definition of a milestone threshold."""

    milestone_type: str
    threshold_usd: Decimal
    label: str
    reward_description: str


MILESTONES: tuple[MilestoneDef, ...] = (
    MilestoneDef(
        milestone_type="active_seller",
        threshold_usd=Decimal("50"),
        label="Active Seller",
        reward_description="Active Seller badge unlocked",
    ),
    MilestoneDef(
        milestone_type="tier_upgrade",
        threshold_usd=Decimal("200"),
        label="Growth Partner",
        reward_description="Commission reduced from 10% to 8%",
    ),
    MilestoneDef(
        milestone_type="cashback",
        threshold_usd=Decimal("500"),
        label="Power Seller",
        reward_description="$25 credit added to balance",
    ),
)


class MilestoneTracker:
    """Track and award provider milestones based on cumulative earnings."""

    CASHBACK_AMOUNT = Decimal("25")

    def __init__(self, db: Database):
        self.db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create milestones table if not exists."""
        with self.db.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS milestones (
                    id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    milestone_type TEXT NOT NULL,
                    threshold_usd REAL NOT NULL,
                    achieved_at TEXT NOT NULL,
                    reward_applied INTEGER DEFAULT 0,
                    UNIQUE(provider_id, milestone_type)
                );

                CREATE INDEX IF NOT EXISTS idx_milestones_provider
                    ON milestones(provider_id);
            """)

    def get_cumulative_earnings(self, provider_id: str) -> Decimal:
        """Get total lifetime earnings for a provider from usage records."""
        with self.db.connect() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(amount_usd), 0) as total
                   FROM usage_records
                   WHERE provider_id = ?
                     AND status_code < 500""",
                (provider_id,),
            ).fetchone()
        return Decimal(str(row["total"]))

    def get_achieved_milestones(self, provider_id: str) -> list[dict]:
        """List all milestones already achieved by a provider."""
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM milestones
                   WHERE provider_id = ?
                   ORDER BY threshold_usd ASC""",
                (provider_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def check_and_award(self, provider_id: str) -> list[dict]:
        """
        Check if provider has crossed any new milestone thresholds.

        Returns list of newly awarded milestones.
        """
        earnings = self.get_cumulative_earnings(provider_id)
        achieved = {m["milestone_type"] for m in self.get_achieved_milestones(provider_id)}

        newly_awarded = []
        now = datetime.now(timezone.utc).isoformat()

        for mdef in MILESTONES:
            if mdef.milestone_type in achieved:
                continue
            if earnings < mdef.threshold_usd:
                continue

            # Award milestone
            milestone_id = str(uuid.uuid4())
            with self.db.connect() as conn:
                conn.execute(
                    """INSERT INTO milestones
                       (id, provider_id, milestone_type, threshold_usd, achieved_at, reward_applied)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT (provider_id, milestone_type) DO NOTHING""",
                    (
                        milestone_id,
                        provider_id,
                        mdef.milestone_type,
                        float(mdef.threshold_usd),
                        now,
                        0,
                    ),
                )

            newly_awarded.append({
                "id": milestone_id,
                "provider_id": provider_id,
                "milestone_type": mdef.milestone_type,
                "label": mdef.label,
                "reward_description": mdef.reward_description,
                "achieved_at": now,
            })

            logger.info(
                "Milestone awarded: %s → %s ($%s)",
                provider_id[:12], mdef.milestone_type, mdef.threshold_usd,
            )

        return newly_awarded

    def apply_cashback(self, provider_id: str) -> bool:
        """
        Apply $25 cashback for the cashback milestone.

        Adds credit to provider's balance. Only applies once.
        Returns True if applied, False if already applied or not eligible.
        """
        with self.db.connect() as conn:
            row = conn.execute(
                """SELECT id, reward_applied FROM milestones
                   WHERE provider_id = ? AND milestone_type = 'cashback'""",
                (provider_id,),
            ).fetchone()

        if not row or row["reward_applied"]:
            return False

        # Add credit to balance
        self.db.credit_balance(provider_id, self.CASHBACK_AMOUNT)

        # Mark reward as applied
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE milestones SET reward_applied = 1 WHERE id = ?",
                (row["id"],),
            )

        logger.info("Cashback applied: %s → $%s", provider_id[:12], self.CASHBACK_AMOUNT)
        return True

    def has_milestone(self, provider_id: str, milestone_type: str) -> bool:
        """Check if a provider has achieved a specific milestone."""
        with self.db.connect() as conn:
            row = conn.execute(
                """SELECT 1 FROM milestones
                   WHERE provider_id = ? AND milestone_type = ?""",
                (provider_id, milestone_type),
            ).fetchone()
        return row is not None

    def get_progress(self, provider_id: str) -> dict:
        """
        Get milestone progress summary for a provider.

        Returns current earnings, achieved milestones, and next milestone info.
        """
        earnings = self.get_cumulative_earnings(provider_id)
        achieved = self.get_achieved_milestones(provider_id)
        achieved_types = {m["milestone_type"] for m in achieved}

        milestones_detail = []
        next_milestone = None

        for mdef in MILESTONES:
            is_achieved = mdef.milestone_type in achieved_types
            progress_pct = min(100.0, float(earnings / mdef.threshold_usd * 100))

            detail = {
                "milestone_type": mdef.milestone_type,
                "label": mdef.label,
                "threshold_usd": str(mdef.threshold_usd),
                "reward_description": mdef.reward_description,
                "achieved": is_achieved,
                "progress_pct": round(progress_pct, 1),
            }

            if is_achieved:
                matched = [m for m in achieved if m["milestone_type"] == mdef.milestone_type]
                if matched:
                    detail["achieved_at"] = matched[0]["achieved_at"]

            milestones_detail.append(detail)

            if not is_achieved and next_milestone is None:
                next_milestone = {
                    "milestone_type": mdef.milestone_type,
                    "label": mdef.label,
                    "threshold_usd": str(mdef.threshold_usd),
                    "remaining_usd": str(mdef.threshold_usd - earnings),
                    "progress_pct": round(progress_pct, 1),
                }

        return {
            "provider_id": provider_id,
            "cumulative_earnings_usd": str(earnings),
            "milestones": milestones_detail,
            "next_milestone": next_milestone,
            "total_achieved": len(achieved),
            "total_milestones": len(MILESTONES),
        }
