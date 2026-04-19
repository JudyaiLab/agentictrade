"""
Provider Growth Program — dynamic commission engine.

Calculates per-provider commission rate based on registration age
and quality tier (health score).

Time-based tiers (PRODUCT_SPEC):
  Month 1:    0% (free trial)
  Months 2-3: 5% (growth rate)
  Month 4+:   10% (standard rate)

Referred provider bonus:
  Months 1-2: 0% (extended free trial)
  Months 3-4: 5% (growth rate)
  Month 5+:   10% (standard rate)

Quality tiers (health-score-based override):
  Standard:  10% (health_score < 80)
  Verified:   8% (health_score >= 80)
  Premium:    6% (health_score >= 95)

The effective rate is the LOWER of time-based and quality-based rates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from .db import Database


@dataclass(frozen=True)
class CommissionTier:
    """A single commission tier in the growth program."""

    month_start: int  # inclusive, 1-based
    month_end: Optional[int]  # inclusive, None = unlimited
    rate: Decimal


@dataclass(frozen=True)
class QualityTier:
    """A quality-based commission tier driven by provider health score."""

    name: str
    min_health_score: float  # inclusive threshold
    rate: Decimal


DEFAULT_TIERS: tuple[CommissionTier, ...] = (
    CommissionTier(month_start=1, month_end=1, rate=Decimal("0.00")),
    CommissionTier(month_start=2, month_end=3, rate=Decimal("0.05")),
    CommissionTier(month_start=4, month_end=None, rate=Decimal("0.10")),
)

# Referred providers get an extended free trial: 2 months instead of 1
REFERRED_TIERS: tuple[CommissionTier, ...] = (
    CommissionTier(month_start=1, month_end=2, rate=Decimal("0.00")),
    CommissionTier(month_start=3, month_end=4, rate=Decimal("0.05")),
    CommissionTier(month_start=5, month_end=None, rate=Decimal("0.10")),
)

# Quality tiers ordered from highest threshold to lowest so the first
# match is the best tier the provider qualifies for.
QUALITY_TIERS: tuple[QualityTier, ...] = (
    QualityTier(name="premium", min_health_score=95.0, rate=Decimal("0.06")),
    QualityTier(name="verified", min_health_score=80.0, rate=Decimal("0.08")),
    QualityTier(name="standard", min_health_score=0.0, rate=Decimal("0.10")),
)

STANDARD_RATE = Decimal("0.10")


class CommissionEngine:
    """Calculate dynamic commission rates per provider based on registration age and quality tier."""

    def __init__(
        self,
        db: Database,
        tiers: tuple[CommissionTier, ...] = DEFAULT_TIERS,
        quality_tiers: tuple[QualityTier, ...] = QUALITY_TIERS,
    ):
        self.db = db
        self.tiers = tiers
        self.quality_tiers = quality_tiers

    def _get_provider_registration_date(
        self, provider_id: str
    ) -> Optional[datetime]:
        """Get the earliest service registration date for a provider."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT MIN(created_at) AS first_registered "
                "FROM services WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()

        if not row or not row["first_registered"]:
            return None
        return datetime.fromisoformat(row["first_registered"])

    def _months_since(
        self, start: datetime, now: Optional[datetime] = None
    ) -> int:
        """
        Calculate which month the provider is in (1-based).

        Month 1 = from registration day to the same day next month.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        month_diff = (now.year - start.year) * 12 + (now.month - start.month)

        # If we haven't reached the registration day in the current month,
        # we're still in the previous billing month
        if now.day < start.day:
            month_diff -= 1

        return max(1, month_diff + 1)

    def get_quality_tier(
        self, provider_id: str, lookback_days: int = 30,
    ) -> QualityTier:
        """Determine quality tier for a provider based on health score.

        Reads health data from the database via HealthMonitor and returns
        the best tier the provider qualifies for. Providers with no health
        data default to the standard tier.
        """
        from .health_monitor import HealthMonitor

        monitor = HealthMonitor(self.db)
        summary = monitor.get_provider_health_summary(
            provider_id, lookback_days,
        )
        health_score = summary.get("avg_quality_score", 0)

        # quality_tiers is sorted highest-threshold-first so the first
        # match is the best tier the provider qualifies for.
        for tier in self.quality_tiers:
            if health_score >= tier.min_health_score:
                return tier

        # Fallback (should not happen since standard has min 0)
        return self.quality_tiers[-1]

    # Micropayment commission reduction: transactions under this amount
    # get a reduced rate to make small API calls economically viable.
    MICROPAYMENT_THRESHOLD = Decimal("1.00")  # USD
    MICROPAYMENT_RATE = Decimal("0.05")       # 5% for <$1 transactions

    def get_effective_rate(
        self,
        provider_id: str,
        now: Optional[datetime] = None,
        lookback_days: int = 30,
        transaction_amount: Optional[Decimal] = None,
    ) -> Decimal:
        """Get the effective commission rate combining time-based, quality-based,
        and micropayment logic.

        Returns the LOWER of:
        - Time-based rate (from get_commission_rate)
        - Quality-tier rate (from get_quality_tier)
        - Micropayment rate (5% for transactions < $1)
        """
        time_rate = self.get_commission_rate(provider_id, now)
        quality_tier = self.get_quality_tier(provider_id, lookback_days)
        rate = min(time_rate, quality_tier.rate)

        # Micropayment reduction: cap commission at 5% for sub-$1 transactions
        if transaction_amount is not None and transaction_amount < self.MICROPAYMENT_THRESHOLD:
            rate = min(rate, self.MICROPAYMENT_RATE)

        return rate

    def _is_referred_provider(self, provider_id: str) -> bool:
        """Check if provider was referred (has an active referral record)."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM referrals WHERE referred_provider_id = ? AND status = 'active' LIMIT 1",
                (provider_id,),
            ).fetchone()
        return row is not None

    def _get_promo_free_months(self, provider_id: str) -> int:
        """Check if provider has promo-granted free commission months.

        Returns 0 if no promo was redeemed.
        """
        try:
            from .promo import get_provider_promo_free_months
            return get_provider_promo_free_months(self.db, provider_id)
        except Exception:
            return 0

    def _build_promo_tiers(self, free_months: int) -> tuple[CommissionTier, ...]:
        """Build a custom tier schedule with extended free trial from a promo code.

        The growth period (5%) spans from free_months+1 to free_months+2,
        then standard rate (10%) kicks in after that.
        """
        return (
            CommissionTier(month_start=1, month_end=free_months, rate=Decimal("0.00")),
            CommissionTier(month_start=free_months + 1, month_end=free_months + 2, rate=Decimal("0.05")),
            CommissionTier(month_start=free_months + 3, month_end=None, rate=Decimal("0.10")),
        )

    def get_commission_rate(
        self, provider_id: str, now: Optional[datetime] = None
    ) -> Decimal:
        """Get current commission rate for a provider.

        Priority: promo code > referral > default tiers.
        Promo codes grant custom free months (e.g. PRODUCTHUNT = 3 months free).
        Referred providers get extended free trial (2 months vs 1).
        Founding Sellers get a reduced cap (8% vs 10% standard).
        """
        reg_date = self._get_provider_registration_date(provider_id)
        if reg_date is None:
            return STANDARD_RATE

        month = self._months_since(reg_date, now)

        # Promo code grants the most free months — check first
        promo_free = self._get_promo_free_months(provider_id)
        if promo_free > 1:
            # Promo overrides default and referral tiers
            tiers = self._build_promo_tiers(promo_free)
        elif self._is_referred_provider(provider_id):
            # Referred providers use extended tiers (2 months free)
            tiers = REFERRED_TIERS
        else:
            tiers = self.tiers

        rate = STANDARD_RATE
        for tier in tiers:
            if tier.month_start <= month and (
                tier.month_end is None or month <= tier.month_end
            ):
                rate = tier.rate
                break

        # Founding Sellers have a reduced maximum rate
        founding = self.db.get_founding_seller(provider_id)
        if founding:
            founding_cap = Decimal(str(founding["commission_rate"]))
            rate = min(rate, founding_cap)

        # Milestone-based reduction: $200 earnings → cap at 8%
        from .milestones import MilestoneTracker
        tracker = MilestoneTracker(self.db)
        if tracker.has_milestone(provider_id, "tier_upgrade"):
            rate = min(rate, Decimal("0.08"))

        return rate

    def get_provider_commission_info(
        self, provider_id: str, now: Optional[datetime] = None
    ) -> dict:
        """Get detailed commission info for a provider."""
        if now is None:
            now = datetime.now(timezone.utc)

        reg_date = self._get_provider_registration_date(provider_id)
        if reg_date is None:
            return {
                "provider_id": provider_id,
                "registered": False,
                "current_rate": STANDARD_RATE,
                "current_tier": "standard",
                "registration_date": None,
                "month_number": None,
                "next_tier_date": None,
                "next_tier_rate": None,
            }

        month = self._months_since(reg_date, now)
        current_rate = self.get_commission_rate(provider_id, now)

        # Determine current tier name
        tier_name = "standard"
        if current_rate == Decimal("0.00"):
            tier_name = "free_trial"
        elif current_rate == Decimal("0.05"):
            tier_name = "growth"

        # Find next tier transition
        next_tier_date = None
        next_tier_rate = None
        for tier in self.tiers:
            if tier.month_start > month:
                next_tier_rate = tier.rate
                # Calculate the date when next tier starts
                months_offset = tier.month_start - 1
                target_year = reg_date.year + (
                    reg_date.month - 1 + months_offset
                ) // 12
                target_month = (
                    reg_date.month - 1 + months_offset
                ) % 12 + 1
                # Clamp day to valid range for target month
                import calendar

                max_day = calendar.monthrange(target_year, target_month)[1]
                target_day = min(reg_date.day, max_day)
                next_tier_date = datetime(
                    target_year, target_month, target_day,
                    tzinfo=timezone.utc,
                ).isoformat()
                break

        # Check founding seller status
        founding = self.db.get_founding_seller(provider_id)
        founding_info = None
        if founding:
            tier_name = "founding_seller"
            founding_cap = Decimal(str(founding["commission_rate"]))
            current_rate = min(current_rate, founding_cap)
            founding_info = {
                "sequence_number": founding["sequence_number"],
                "badge_tier": founding["badge_tier"],
                "commission_cap": str(founding_cap),
                "awarded_at": founding["awarded_at"],
            }

        return {
            "provider_id": provider_id,
            "registered": True,
            "current_rate": current_rate,
            "current_tier": tier_name,
            "registration_date": reg_date.isoformat(),
            "month_number": month,
            "next_tier_date": next_tier_date,
            "next_tier_rate": next_tier_rate,
            "founding_seller": founding_info,
        }
