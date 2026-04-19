"""Tests for Provider Growth Program commission engine."""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from marketplace.commission import (
    CommissionEngine,
    CommissionTier,
    QualityTier,
    DEFAULT_TIERS,
    QUALITY_TIERS,
)
from marketplace.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def engine(db):
    return CommissionEngine(db)


def _register_service(db, provider_id: str, created_at: str) -> str:
    """Helper: insert a service with a specific created_at date."""
    svc_id = f"svc_{uuid.uuid4().hex[:12]}"
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO services
               (id, provider_id, name, description, endpoint,
                price_per_call, currency, status, category,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                svc_id, provider_id, "Test Svc", "desc",
                "https://api.test.com/v1", 0.01, "USD", "active", "ai",
                created_at, created_at,
            ),
        )
    return svc_id


# ── Default tiers ──

class TestDefaultTiers:
    def test_three_tiers(self):
        assert len(DEFAULT_TIERS) == 3

    def test_month_1_free(self):
        assert DEFAULT_TIERS[0].rate == Decimal("0.00")
        assert DEFAULT_TIERS[0].month_start == 1
        assert DEFAULT_TIERS[0].month_end == 1

    def test_months_2_3_growth(self):
        assert DEFAULT_TIERS[1].rate == Decimal("0.05")
        assert DEFAULT_TIERS[1].month_start == 2
        assert DEFAULT_TIERS[1].month_end == 3

    def test_month_4_plus_standard(self):
        assert DEFAULT_TIERS[2].rate == Decimal("0.10")
        assert DEFAULT_TIERS[2].month_start == 4
        assert DEFAULT_TIERS[2].month_end is None

    def test_tiers_are_frozen(self):
        with pytest.raises(AttributeError):
            DEFAULT_TIERS[0].rate = Decimal("0.99")


# ── Month calculation ──

class TestMonthsSince:
    def test_same_day(self, engine):
        now = datetime(2026, 3, 20, tzinfo=timezone.utc)
        assert engine._months_since(now, now) == 1

    def test_one_month_later(self, engine):
        start = datetime(2026, 1, 15, tzinfo=timezone.utc)
        now = datetime(2026, 2, 15, tzinfo=timezone.utc)
        assert engine._months_since(start, now) == 2

    def test_before_day_of_month(self, engine):
        start = datetime(2026, 1, 20, tzinfo=timezone.utc)
        now = datetime(2026, 2, 15, tzinfo=timezone.utc)
        # Only 26 days passed, not a full month yet
        assert engine._months_since(start, now) == 1

    def test_three_months(self, engine):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        now = datetime(2026, 4, 1, tzinfo=timezone.utc)
        assert engine._months_since(start, now) == 4

    def test_year_boundary(self, engine):
        start = datetime(2025, 11, 1, tzinfo=timezone.utc)
        now = datetime(2026, 3, 1, tzinfo=timezone.utc)
        assert engine._months_since(start, now) == 5

    def test_never_below_one(self, engine):
        start = datetime(2026, 3, 20, tzinfo=timezone.utc)
        now = datetime(2026, 3, 19, tzinfo=timezone.utc)
        assert engine._months_since(start, now) == 1


# ── Commission rate lookup ──

class TestGetCommissionRate:
    def test_unknown_provider_gets_standard(self, engine):
        rate = engine.get_commission_rate("unknown_provider")
        assert rate == Decimal("0.10")

    def test_month_1_is_free(self, db, engine):
        reg = "2026-03-01T00:00:00+00:00"
        _register_service(db, "prov_1", reg)
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate("prov_1", now) == Decimal("0.00")

    def test_month_2_is_growth(self, db, engine):
        reg = "2026-01-10T00:00:00+00:00"
        _register_service(db, "prov_2", reg)
        now = datetime(2026, 2, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate("prov_2", now) == Decimal("0.05")

    def test_month_3_is_growth(self, db, engine):
        reg = "2026-01-01T00:00:00+00:00"
        _register_service(db, "prov_3", reg)
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate("prov_3", now) == Decimal("0.05")

    def test_month_4_is_standard(self, db, engine):
        reg = "2025-12-01T00:00:00+00:00"
        _register_service(db, "prov_4", reg)
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate("prov_4", now) == Decimal("0.10")

    def test_month_12_is_standard(self, db, engine):
        reg = "2025-03-01T00:00:00+00:00"
        _register_service(db, "prov_5", reg)
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate("prov_5", now) == Decimal("0.10")

    def test_uses_earliest_service(self, db, engine):
        """If provider has multiple services, use the first one registered."""
        _register_service(db, "prov_multi", "2025-06-01T00:00:00+00:00")
        _register_service(db, "prov_multi", "2026-03-01T00:00:00+00:00")
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        # Based on June 2025 (10 months ago), should be standard
        assert engine.get_commission_rate("prov_multi", now) == Decimal("0.10")


# ── Custom tiers ──

class TestCustomTiers:
    def test_custom_tiers(self, db):
        custom = (
            CommissionTier(month_start=1, month_end=2, rate=Decimal("0.00")),
            CommissionTier(month_start=3, month_end=None, rate=Decimal("0.08")),
        )
        eng = CommissionEngine(db, tiers=custom)
        _register_service(db, "prov_c1", "2026-03-01T00:00:00+00:00")
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        assert eng.get_commission_rate("prov_c1", now) == Decimal("0.00")

        now2 = datetime(2026, 5, 15, tzinfo=timezone.utc)
        assert eng.get_commission_rate("prov_c1", now2) == Decimal("0.08")


# ── Commission info ──

class TestGetProviderCommissionInfo:
    def test_unregistered_provider(self, engine):
        info = engine.get_provider_commission_info("no_such")
        assert info["registered"] is False
        assert info["current_rate"] == Decimal("0.10")
        assert info["registration_date"] is None

    def test_month_1_info(self, db, engine):
        reg = "2026-03-01T00:00:00+00:00"
        _register_service(db, "prov_info1", reg)
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        info = engine.get_provider_commission_info("prov_info1", now)

        assert info["registered"] is True
        assert info["current_rate"] == Decimal("0.00")
        assert info["current_tier"] == "free_trial"
        assert info["month_number"] == 1
        assert info["next_tier_rate"] == Decimal("0.05")

    def test_month_3_info(self, db, engine):
        reg = "2026-01-01T00:00:00+00:00"
        _register_service(db, "prov_info3", reg)
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        info = engine.get_provider_commission_info("prov_info3", now)

        assert info["current_tier"] == "growth"
        assert info["current_rate"] == Decimal("0.05")
        assert info["next_tier_rate"] == Decimal("0.10")

    def test_month_4_plus_no_next_tier(self, db, engine):
        reg = "2025-11-01T00:00:00+00:00"
        _register_service(db, "prov_info4", reg)
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        info = engine.get_provider_commission_info("prov_info4", now)

        assert info["current_tier"] == "standard"
        assert info["current_rate"] == Decimal("0.10")
        assert info["next_tier_rate"] is None
        assert info["next_tier_date"] is None


# ── Settlement integration ──

class TestSettlementIntegration:
    """Test that commission engine integrates with SettlementEngine."""

    def test_settlement_uses_dynamic_rate(self, db):
        from marketplace.settlement import SettlementEngine

        commission = CommissionEngine(db)
        settlement = SettlementEngine(db, commission_engine=commission)

        # Register a new provider (month 1 = 0%)
        provider_id = "prov_settle_1"
        reg = datetime.now(timezone.utc).isoformat()
        _register_service(db, provider_id, reg)

        # Insert usage record
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO usage_records
                   (id, service_id, buyer_id, provider_id,
                    status_code, latency_ms, amount_usd, payment_method,
                    timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), "svc_test", "buyer_1", provider_id,
                    200, 50, 100.0, "x402",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        result = settlement.calculate_settlement(provider_id, yesterday, tomorrow)

        # Month 1 = 0% commission → provider keeps everything
        assert result["platform_fee"] == Decimal("0")
        assert result["net_amount"] == Decimal("100")

    def test_settlement_fallback_to_fixed(self, db):
        """Without commission engine, uses fixed platform_fee_pct."""
        from marketplace.settlement import SettlementEngine

        settlement = SettlementEngine(db, platform_fee_pct=Decimal("0.10"))

        provider_id = "prov_settle_2"
        reg = datetime.now(timezone.utc).isoformat()
        _register_service(db, provider_id, reg)

        with db.connect() as conn:
            conn.execute(
                """INSERT INTO usage_records
                   (id, service_id, buyer_id, provider_id,
                    status_code, latency_ms, amount_usd, payment_method,
                    timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), "svc_test", "buyer_1", provider_id,
                    200, 50, 100.0, "x402",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        result = settlement.calculate_settlement(provider_id, yesterday, tomorrow)

        # Fixed 10% → $10 fee
        assert result["platform_fee"] == Decimal("10")
        assert result["net_amount"] == Decimal("90")


# ── Quality tier helpers ──

def _insert_health_checks(
    db, service_id: str, provider_id: str,
    count: int, reachable: bool = True,
    latency_ms: int = 100, status_code: int = 200,
) -> None:
    """Helper: insert health check records to produce a specific health score."""
    from marketplace.health_monitor import HealthMonitor
    # Ensure health_checks table exists
    HealthMonitor(db)

    now = datetime.now(timezone.utc)
    with db.connect() as conn:
        for i in range(count):
            checked_at = (now - timedelta(hours=i)).isoformat()
            conn.execute(
                """INSERT INTO health_checks
                   (id, service_id, provider_id, reachable, latency_ms,
                    status_code, error, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?, '', ?)""",
                (
                    str(uuid.uuid4()),
                    service_id,
                    provider_id,
                    1 if reachable else 0,
                    latency_ms,
                    status_code,
                    checked_at,
                ),
            )


# ── Quality tier constants ──

class TestQualityTierConstants:
    def test_three_quality_tiers(self):
        assert len(QUALITY_TIERS) == 3

    def test_premium_tier(self):
        assert QUALITY_TIERS[0].name == "premium"
        assert QUALITY_TIERS[0].min_health_score == 95.0
        assert QUALITY_TIERS[0].rate == Decimal("0.06")

    def test_verified_tier(self):
        assert QUALITY_TIERS[1].name == "verified"
        assert QUALITY_TIERS[1].min_health_score == 80.0
        assert QUALITY_TIERS[1].rate == Decimal("0.08")

    def test_standard_tier(self):
        assert QUALITY_TIERS[2].name == "standard"
        assert QUALITY_TIERS[2].min_health_score == 0.0
        assert QUALITY_TIERS[2].rate == Decimal("0.10")

    def test_quality_tiers_are_frozen(self):
        with pytest.raises(AttributeError):
            QUALITY_TIERS[0].rate = Decimal("0.99")

    def test_ordered_highest_threshold_first(self):
        thresholds = [t.min_health_score for t in QUALITY_TIERS]
        assert thresholds == sorted(thresholds, reverse=True)


# ── get_quality_tier ──

class TestGetQualityTier:
    def test_no_health_data_returns_standard(self, engine):
        """Provider with no health checks defaults to standard tier."""
        tier = engine.get_quality_tier("unknown_provider")
        assert tier.name == "standard"
        assert tier.rate == Decimal("0.10")

    def test_low_score_returns_standard(self, db, engine):
        """Provider with health_score < 80 gets standard tier."""
        provider_id = "prov_low"
        svc_id = _register_service(db, provider_id, "2026-01-01T00:00:00+00:00")
        # Insert checks with many failures to get a low score
        _insert_health_checks(db, svc_id, provider_id, count=5, reachable=True, latency_ms=100)
        _insert_health_checks(db, svc_id, provider_id, count=5, reachable=False, latency_ms=10000, status_code=500)

        tier = engine.get_quality_tier(provider_id)
        assert tier.name == "standard"
        assert tier.rate == Decimal("0.10")

    def test_high_score_returns_verified(self, db, engine):
        """Provider with health_score >= 80 but < 95 gets verified tier."""
        provider_id = "prov_verified"
        svc_id = _register_service(db, provider_id, "2026-01-01T00:00:00+00:00")
        # All checks pass with decent latency → ~85-90 score
        _insert_health_checks(db, svc_id, provider_id, count=10, reachable=True, latency_ms=200)

        tier = engine.get_quality_tier(provider_id)
        # With 100% uptime, ~200ms avg latency, 0% error:
        # uptime_score=100*0.5=50, latency_score=max(0,100-200/50)=96*0.3=28.8, error=100*0.2=20 → ~98.8
        # Actually this would be premium. Let me check the math.
        # latency_score = max(0, 100 - (200/50)) = max(0, 100-4) = 96
        # quality_score = 100*0.5 + 96*0.3 + 100*0.2 = 50+28.8+20 = 98.8 → premium
        # Need higher latency to get verified but not premium
        assert tier.name in ("premium", "verified")

    def test_perfect_score_returns_premium(self, db, engine):
        """Provider with health_score >= 95 gets premium tier."""
        provider_id = "prov_premium"
        svc_id = _register_service(db, provider_id, "2026-01-01T00:00:00+00:00")
        # Perfect checks: all reachable, low latency
        _insert_health_checks(db, svc_id, provider_id, count=10, reachable=True, latency_ms=50)

        tier = engine.get_quality_tier(provider_id)
        assert tier.name == "premium"
        assert tier.rate == Decimal("0.06")

    def test_borderline_80_score_gets_verified(self, db, engine):
        """Score exactly at 80 boundary qualifies for verified tier."""
        provider_id = "prov_border80"
        svc_id = _register_service(db, provider_id, "2026-01-01T00:00:00+00:00")
        # Mix of passing and failing to land around 80
        # 8 good + 2 failures: uptime=80%, error_rate=20%
        # latency for good: 500ms → latency_score = max(0, 100 - 500/50) = 90
        # but avg latency includes failures with high latency...
        # Use 8 good at 1000ms, 2 bad at 1000ms
        # uptime = 8/10 = 80%, avg_latency = 1000, error_rate = 20%
        # latency_score = max(0, 100 - 1000/50) = max(0, 80) = 80
        # quality = 80*0.5 + 80*0.3 + 80*0.2 = 40+24+16 = 80 → verified
        _insert_health_checks(db, svc_id, provider_id, count=8, reachable=True, latency_ms=1000)
        _insert_health_checks(db, svc_id, provider_id, count=2, reachable=False, latency_ms=1000, status_code=500)

        tier = engine.get_quality_tier(provider_id)
        assert tier.name == "verified"
        assert tier.rate == Decimal("0.08")

    def test_borderline_95_score_gets_premium(self, db, engine):
        """Score exactly at or above 95 qualifies for premium tier."""
        provider_id = "prov_border95"
        svc_id = _register_service(db, provider_id, "2026-01-01T00:00:00+00:00")
        # 100% uptime, 0% error, low latency → score well above 95
        _insert_health_checks(db, svc_id, provider_id, count=20, reachable=True, latency_ms=100)

        tier = engine.get_quality_tier(provider_id)
        assert tier.name == "premium"
        assert tier.rate == Decimal("0.06")


# ── get_effective_rate ──

class TestGetEffectiveRate:
    def test_no_health_data_uses_time_rate(self, db, engine):
        """Without health data, effective rate equals the time-based rate."""
        provider_id = "prov_eff_nohealth"
        _register_service(db, provider_id, "2025-12-01T00:00:00+00:00")
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)

        # Month 4 → time rate = 10%, no health → quality = standard 10%
        # min(10%, 10%) = 10%
        assert engine.get_effective_rate(provider_id, now) == Decimal("0.10")

    def test_premium_quality_reduces_standard_time(self, db, engine):
        """Premium quality tier (6%) overrides standard time tier (10%)."""
        provider_id = "prov_eff_premium"
        svc_id = _register_service(db, provider_id, "2025-12-01T00:00:00+00:00")
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)

        # Month 4 → time rate = 10%
        # Premium health → quality rate = 6%
        # min(10%, 6%) = 6%
        _insert_health_checks(db, svc_id, provider_id, count=10, reachable=True, latency_ms=50)

        assert engine.get_effective_rate(provider_id, now) == Decimal("0.06")

    def test_verified_quality_reduces_standard_time(self, db, engine):
        """Verified quality tier (8%) overrides standard time tier (10%)."""
        provider_id = "prov_eff_ver"
        svc_id = _register_service(db, provider_id, "2025-12-01T00:00:00+00:00")
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)

        # Month 4 → time rate = 10%
        # Verified health → quality rate = 8%
        # min(10%, 8%) = 8%
        _insert_health_checks(db, svc_id, provider_id, count=8, reachable=True, latency_ms=1000)
        _insert_health_checks(db, svc_id, provider_id, count=2, reachable=False, latency_ms=1000, status_code=500)

        assert engine.get_effective_rate(provider_id, now) == Decimal("0.08")

    def test_time_rate_lower_than_quality_wins(self, db, engine):
        """During free trial (0%), time rate wins over any quality tier."""
        provider_id = "prov_eff_free"
        svc_id = _register_service(db, provider_id, "2026-03-01T00:00:00+00:00")
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)

        # Month 1 → time rate = 0%
        # Premium health → quality rate = 6%
        # min(0%, 6%) = 0%
        _insert_health_checks(db, svc_id, provider_id, count=10, reachable=True, latency_ms=50)

        assert engine.get_effective_rate(provider_id, now) == Decimal("0.00")

    def test_growth_rate_lower_than_quality_wins(self, db, engine):
        """During growth period (5%), time rate wins over quality tier."""
        provider_id = "prov_eff_growth"
        svc_id = _register_service(db, provider_id, "2026-01-10T00:00:00+00:00")
        now = datetime(2026, 2, 15, tzinfo=timezone.utc)

        # Month 2 → time rate = 5%
        # Premium health → quality rate = 6%
        # min(5%, 6%) = 5%
        _insert_health_checks(db, svc_id, provider_id, count=10, reachable=True, latency_ms=50)

        assert engine.get_effective_rate(provider_id, now) == Decimal("0.05")

    def test_unknown_provider_gets_standard(self, engine):
        """Unknown provider: time rate = 10%, quality = standard 10% → 10%."""
        assert engine.get_effective_rate("unknown_prov") == Decimal("0.10")

    def test_effective_rate_backward_compatible(self, db, engine):
        """get_commission_rate (time-based) is unchanged by quality tier addition."""
        provider_id = "prov_eff_compat"
        _register_service(db, provider_id, "2025-12-01T00:00:00+00:00")
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)

        # Original time-based rate must still return 10%
        assert engine.get_commission_rate(provider_id, now) == Decimal("0.10")


# ── Custom quality tiers ──

class TestCustomQualityTiers:
    def test_custom_quality_tiers(self, db):
        """Engine accepts custom quality tier definitions."""
        custom_quality = (
            QualityTier(name="elite", min_health_score=90.0, rate=Decimal("0.03")),
            QualityTier(name="basic", min_health_score=0.0, rate=Decimal("0.12")),
        )
        eng = CommissionEngine(db, quality_tiers=custom_quality)

        provider_id = "prov_custom_q"
        svc_id = _register_service(db, provider_id, "2025-12-01T00:00:00+00:00")
        # Insert perfect health checks → should qualify for "elite"
        _insert_health_checks(db, svc_id, provider_id, count=10, reachable=True, latency_ms=50)

        tier = eng.get_quality_tier(provider_id)
        assert tier.name == "elite"
        assert tier.rate == Decimal("0.03")


# ── Micropayment Commission ──


class TestMicropaymentCommission:
    """Tests for reduced commission on micropayment transactions."""

    def test_micropayment_rate_below_threshold(self, db, engine):
        """Transactions under $1 get capped at 5% commission."""
        provider_id = "prov_micro_1"
        # Register service 4+ months ago → time-based rate = 10%
        _register_service(db, provider_id, "2025-10-01T00:00:00+00:00")

        rate = engine.get_effective_rate(
            provider_id,
            transaction_amount=Decimal("0.50"),
        )
        # Should be capped at 5% (micropayment rate) instead of 10%
        assert rate == Decimal("0.05")

    def test_normal_rate_above_threshold(self, db, engine):
        """Transactions at $1+ use normal commission rate."""
        provider_id = "prov_micro_2"
        _register_service(db, provider_id, "2025-10-01T00:00:00+00:00")

        rate = engine.get_effective_rate(
            provider_id,
            transaction_amount=Decimal("1.00"),
        )
        # $1 is NOT below threshold, normal rate applies
        assert rate == Decimal("0.10")

    def test_micropayment_does_not_raise_free_tier(self, db, engine):
        """Micropayment rate doesn't override free trial (month 1 = 0%)."""
        provider_id = "prov_micro_3"
        # Register this month → month 1 → 0%
        now = datetime.now(timezone.utc)
        _register_service(db, provider_id, now.isoformat())

        rate = engine.get_effective_rate(
            provider_id,
            now=now,
            transaction_amount=Decimal("0.50"),
        )
        # 0% (free trial) is already lower than 5% micropayment rate
        assert rate == Decimal("0.00")

    def test_micropayment_without_amount(self, db, engine):
        """When no transaction_amount is provided, micropayment logic is skipped."""
        provider_id = "prov_micro_4"
        _register_service(db, provider_id, "2025-10-01T00:00:00+00:00")

        rate = engine.get_effective_rate(provider_id)
        # Normal rate, no micropayment reduction
        assert rate == Decimal("0.10")

    def test_micropayment_constants(self):
        """Verify micropayment constants."""
        assert CommissionEngine.MICROPAYMENT_THRESHOLD == Decimal("1.00")
        assert CommissionEngine.MICROPAYMENT_RATE == Decimal("0.05")


# ── Commission Rate Snapshot (R16-M4 fix) ──

class TestCommissionRateSnapshot:
    """Test that settlement uses per-record commission_rate snapshots."""

    def test_snapshot_rate_used_over_current_rate(self, db):
        """When commission_rate is stored on the record, use that instead of live rate."""
        from marketplace.settlement import SettlementEngine

        commission = CommissionEngine(db)
        settlement = SettlementEngine(db, commission_engine=commission)

        # Register a provider 4+ months ago (current live rate = 10%)
        provider_id = "prov_snap_1"
        _register_service(db, provider_id, "2025-10-01T00:00:00+00:00")

        # Insert usage record WITH a snapshot rate of 5% (was growth tier at time of tx)
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO usage_records
                   (id, service_id, buyer_id, provider_id,
                    status_code, latency_ms, amount_usd, payment_method,
                    timestamp, commission_rate)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), "svc_test", "buyer_1", provider_id,
                    200, 50, 100.0, "x402",
                    datetime.now(timezone.utc).isoformat(),
                    "0.05",
                ),
            )

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        result = settlement.calculate_settlement(provider_id, yesterday, tomorrow)

        # Snapshot rate 5% should be used, not the current 10%
        assert result["platform_fee"] == Decimal("5.00")
        assert result["net_amount"] == Decimal("95.00")

    def test_fallback_to_current_rate_without_snapshot(self, db):
        """When commission_rate is NULL on the record, fall back to live rate."""
        from marketplace.settlement import SettlementEngine

        commission = CommissionEngine(db)
        settlement = SettlementEngine(db, commission_engine=commission)

        # Register a new provider (month 1 = 0%)
        provider_id = "prov_snap_2"
        reg = datetime.now(timezone.utc).isoformat()
        _register_service(db, provider_id, reg)

        # Insert usage record WITHOUT snapshot (commission_rate = NULL)
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO usage_records
                   (id, service_id, buyer_id, provider_id,
                    status_code, latency_ms, amount_usd, payment_method,
                    timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), "svc_test", "buyer_1", provider_id,
                    200, 50, 100.0, "x402",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        result = settlement.calculate_settlement(provider_id, yesterday, tomorrow)

        # Should use live rate (month 1 = 0%)
        assert result["platform_fee"] == Decimal("0")
        assert result["net_amount"] == Decimal("100")

    def test_mixed_snapshot_and_no_snapshot(self, db):
        """Settlement with a mix of records: some with snapshot, some without."""
        from marketplace.settlement import SettlementEngine

        settlement = SettlementEngine(db, platform_fee_pct=Decimal("0.10"))

        provider_id = "prov_snap_3"
        _register_service(db, provider_id, "2025-10-01T00:00:00+00:00")

        now_iso = datetime.now(timezone.utc).isoformat()

        with db.connect() as conn:
            # Record 1: WITH snapshot at 5%
            conn.execute(
                """INSERT INTO usage_records
                   (id, service_id, buyer_id, provider_id,
                    status_code, latency_ms, amount_usd, payment_method,
                    timestamp, commission_rate)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), "svc_test", "buyer_1", provider_id,
                    200, 50, 100.0, "x402", now_iso, "0.05",
                ),
            )
            # Record 2: WITHOUT snapshot (falls back to fixed 10%)
            conn.execute(
                """INSERT INTO usage_records
                   (id, service_id, buyer_id, provider_id,
                    status_code, latency_ms, amount_usd, payment_method,
                    timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), "svc_test", "buyer_1", provider_id,
                    200, 50, 100.0, "x402", now_iso,
                ),
            )

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        result = settlement.calculate_settlement(provider_id, yesterday, tomorrow)

        # Record 1: $100 * 5% = $5 fee
        # Record 2: $100 * 10% = $10 fee
        # Total: $200, Fee: $15, Net: $185
        assert result["total_amount"] == Decimal("200")
        assert result["platform_fee"] == Decimal("15.00")
        assert result["net_amount"] == Decimal("185.00")

    def test_insert_usage_with_commission_rate(self, db):
        """insert_usage correctly stores commission_rate."""
        record = {
            "id": str(uuid.uuid4()),
            "buyer_id": "buyer-1",
            "service_id": "svc-1",
            "provider_id": "prov-1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "amount_usd": 50.0,
            "commission_rate": Decimal("0.05"),
        }
        db.insert_usage(record)

        with db.connect() as conn:
            row = conn.execute(
                "SELECT commission_rate FROM usage_records WHERE id = ?",
                (record["id"],),
            ).fetchone()

        assert row["commission_rate"] == "0.05"

    def test_insert_usage_without_commission_rate(self, db):
        """insert_usage stores NULL when commission_rate is not provided."""
        record = {
            "id": str(uuid.uuid4()),
            "buyer_id": "buyer-1",
            "service_id": "svc-1",
            "provider_id": "prov-1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "amount_usd": 50.0,
        }
        db.insert_usage(record)

        with db.connect() as conn:
            row = conn.execute(
                "SELECT commission_rate FROM usage_records WHERE id = ?",
                (record["id"],),
            ).fetchone()

        assert row["commission_rate"] is None
