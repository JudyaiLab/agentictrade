"""Tests for the promo code system."""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from marketplace.db import Database
from marketplace.promo import (
    ensure_promo_tables,
    seed_default_promos,
    validate_promo_code,
    redeem_promo_code,
    get_provider_promo_free_months,
)
from marketplace.commission import CommissionEngine


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


def _insert_promo(db, code: str, free_months: int, expires_at: str, active: int = 1, max_redemptions=None):
    """Helper: insert a promo code."""
    ensure_promo_tables(db)
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO promo_codes (code, description, free_months, expires_at, max_redemptions, active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (code, f"Test promo {code}", free_months, expires_at, max_redemptions, active, now),
        )


# ── Schema & Seeding ──

class TestPromoSetup:
    def test_ensure_tables_idempotent(self, db):
        """ensure_promo_tables can be called multiple times safely."""
        ensure_promo_tables(db)
        ensure_promo_tables(db)
        # No exception means pass
        with db.connect() as conn:
            conn.execute("SELECT * FROM promo_codes").fetchall()
            conn.execute("SELECT * FROM promo_redemptions").fetchall()

    def test_seed_default_promos(self, db):
        """seed_default_promos creates the PRODUCTHUNT code."""
        seed_default_promos(db)
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM promo_codes WHERE code = 'PRODUCTHUNT'"
            ).fetchone()
        assert row is not None
        assert row["free_months"] == 3
        assert "2026-06-06" in row["expires_at"]

    def test_seed_idempotent(self, db):
        """Seeding twice does not create duplicates."""
        seed_default_promos(db)
        seed_default_promos(db)
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) as cnt FROM promo_codes WHERE code = 'PRODUCTHUNT'"
            ).fetchone()
        assert rows["cnt"] == 1


# ── Validation ──

class TestValidatePromoCode:
    def test_empty_code(self, db):
        ensure_promo_tables(db)
        is_valid, error, promo = validate_promo_code(db, "")
        assert not is_valid
        assert "No promo code" in error
        assert promo is None

    def test_invalid_code(self, db):
        ensure_promo_tables(db)
        is_valid, error, promo = validate_promo_code(db, "DOESNOTEXIST")
        assert not is_valid
        assert "Invalid promo code" in error

    def test_valid_code(self, db):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        _insert_promo(db, "TESTCODE", 2, future)
        is_valid, error, promo = validate_promo_code(db, "TESTCODE")
        assert is_valid
        assert error == ""
        assert promo["free_months"] == 2

    def test_case_insensitive(self, db):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        _insert_promo(db, "MYCODE", 2, future)
        is_valid, _, _ = validate_promo_code(db, "mycode")
        assert is_valid

    def test_expired_code(self, db):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        _insert_promo(db, "EXPIRED", 2, past)
        is_valid, error, _ = validate_promo_code(db, "EXPIRED")
        assert not is_valid
        assert "expired" in error.lower()

    def test_inactive_code(self, db):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        _insert_promo(db, "INACTIVE", 2, future, active=0)
        is_valid, error, _ = validate_promo_code(db, "INACTIVE")
        assert not is_valid
        assert "no longer active" in error.lower()

    def test_max_redemptions_reached(self, db):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        _insert_promo(db, "LIMITED", 2, future, max_redemptions=1)

        # Manually insert one redemption
        ensure_promo_tables(db)
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO promo_redemptions (id, code, provider_id, free_months, redeemed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), "LIMITED", "prov_1", 2, datetime.now(timezone.utc).isoformat()),
            )

        is_valid, error, _ = validate_promo_code(db, "LIMITED")
        assert not is_valid
        assert "redemption limit" in error.lower()

    def test_unlimited_redemptions(self, db):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        _insert_promo(db, "UNLIMITED", 2, future, max_redemptions=None)

        # Insert many redemptions
        ensure_promo_tables(db)
        with db.connect() as conn:
            for i in range(50):
                conn.execute(
                    """INSERT INTO promo_redemptions (id, code, provider_id, free_months, redeemed_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), "UNLIMITED", f"prov_{i}", 2, datetime.now(timezone.utc).isoformat()),
                )

        is_valid, _, _ = validate_promo_code(db, "UNLIMITED")
        assert is_valid


# ── Redemption ──

class TestRedeemPromoCode:
    def test_successful_redemption(self, db):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        _insert_promo(db, "PROMO1", 3, future)

        success, msg, free_months = redeem_promo_code(db, "provider_abc", "PROMO1")
        assert success
        assert free_months == 3
        assert "3 months" in msg

    def test_duplicate_redemption_blocked(self, db):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        _insert_promo(db, "PROMO2", 3, future)

        success1, _, _ = redeem_promo_code(db, "provider_xyz", "PROMO2")
        assert success1

        success2, msg, free_months = redeem_promo_code(db, "provider_xyz", "PROMO2")
        assert not success2
        assert free_months == 0
        assert "already used" in msg.lower()

    def test_different_providers_can_use_same_code(self, db):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        _insert_promo(db, "SHARED", 3, future)

        s1, _, _ = redeem_promo_code(db, "prov_a", "SHARED")
        s2, _, _ = redeem_promo_code(db, "prov_b", "SHARED")
        assert s1
        assert s2

    def test_redeem_invalid_code(self, db):
        ensure_promo_tables(db)
        success, msg, free_months = redeem_promo_code(db, "prov_x", "NOPE")
        assert not success
        assert free_months == 0


# ── Free months lookup ──

class TestGetProviderPromoFreeMonths:
    def test_no_promo(self, db):
        ensure_promo_tables(db)
        assert get_provider_promo_free_months(db, "prov_no_promo") == 0

    def test_with_promo(self, db):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        _insert_promo(db, "FREE3", 3, future)
        redeem_promo_code(db, "prov_has_promo", "FREE3")
        assert get_provider_promo_free_months(db, "prov_has_promo") == 3

    def test_max_of_multiple_promos(self, db):
        """If multiple promos redeemed, return the max free_months."""
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        _insert_promo(db, "SMALL", 2, future)
        _insert_promo(db, "BIG", 6, future)

        redeem_promo_code(db, "prov_multi", "SMALL")
        redeem_promo_code(db, "prov_multi", "BIG")
        assert get_provider_promo_free_months(db, "prov_multi") == 6


# ── Commission Engine Integration ──

class TestPromoCommissionIntegration:
    def test_promo_3_months_free(self, db, engine):
        """Provider with PRODUCTHUNT promo gets 3 months free commission."""
        seed_default_promos(db)

        provider_id = "prov_ph"
        reg = "2026-04-01T00:00:00+00:00"
        _register_service(db, provider_id, reg)

        # Redeem PRODUCTHUNT promo
        redeem_promo_code(db, provider_id, "PRODUCTHUNT")

        # Month 1: 0%
        now_m1 = datetime(2026, 4, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate(provider_id, now_m1) == Decimal("0.00")

        # Month 2: 0% (promo extends free period)
        now_m2 = datetime(2026, 5, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate(provider_id, now_m2) == Decimal("0.00")

        # Month 3: 0% (still in promo free period)
        now_m3 = datetime(2026, 6, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate(provider_id, now_m3) == Decimal("0.00")

        # Month 4: 5% (growth tier after promo ends)
        now_m4 = datetime(2026, 7, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate(provider_id, now_m4) == Decimal("0.05")

        # Month 5: 5% (still growth)
        now_m5 = datetime(2026, 8, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate(provider_id, now_m5) == Decimal("0.05")

        # Month 6+: 10% (standard)
        now_m6 = datetime(2026, 9, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate(provider_id, now_m6) == Decimal("0.10")

    def test_no_promo_default_tiers(self, db, engine):
        """Provider without promo gets standard 1-month free trial."""
        from marketplace.referral import ReferralManager
        ReferralManager(db)  # ensure referrals table exists

        provider_id = "prov_no_ph"
        reg = "2026-04-01T00:00:00+00:00"
        _register_service(db, provider_id, reg)
        ensure_promo_tables(db)

        # Month 1: 0%
        now_m1 = datetime(2026, 4, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate(provider_id, now_m1) == Decimal("0.00")

        # Month 2: 5% (growth — NOT 0%)
        now_m2 = datetime(2026, 5, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate(provider_id, now_m2) == Decimal("0.05")

    def test_promo_overrides_referral(self, db, engine):
        """Promo (3 months free) beats referral (2 months free)."""
        seed_default_promos(db)

        provider_id = "prov_both"
        reg = "2026-04-01T00:00:00+00:00"
        _register_service(db, provider_id, reg)

        # Simulate referral
        from marketplace.referral import ReferralManager
        rm = ReferralManager(db)
        referrer_code = rm.generate_code("referrer_prov")
        rm.apply_code(provider_id, referrer_code["referral_code"])

        # Also redeem PRODUCTHUNT promo
        redeem_promo_code(db, provider_id, "PRODUCTHUNT")

        # Month 3 should be 0% (promo gives 3 free, referral gives 2 free)
        now_m3 = datetime(2026, 6, 15, tzinfo=timezone.utc)
        assert engine.get_commission_rate(provider_id, now_m3) == Decimal("0.00")


# ── PRODUCTHUNT specific test ──

class TestProductHuntPromo:
    def test_producthunt_seed_values(self, db):
        """PRODUCTHUNT promo has correct values after seeding."""
        seed_default_promos(db)

        is_valid, _, promo = validate_promo_code(db, "PRODUCTHUNT")
        assert is_valid
        assert promo["free_months"] == 3
        assert promo["code"] == "PRODUCTHUNT"
        assert "2026-06-06" in promo["expires_at"]
        assert promo["active"] == 1
