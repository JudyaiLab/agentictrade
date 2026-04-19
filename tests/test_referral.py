"""Tests for referral system — marketplace logic and API routes."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from marketplace.db import Database
from marketplace.auth import APIKeyManager
from marketplace.referral import (
    ReferralManager,
    REFERRAL_PAYOUT_RATE,
    CODE_LENGTH,
    _generate_code,
)
from api.main import app


# ── Fixtures ──


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test_referral.db")


@pytest.fixture
def manager(db):
    return ReferralManager(db)


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


@pytest.fixture
def provider_a_creds(auth):
    """Provider A — the referrer."""
    key_id, secret = auth.create_key(owner_id="provider-a", role="provider")
    return key_id, secret


@pytest.fixture
def provider_b_creds(auth):
    """Provider B — the referred."""
    key_id, secret = auth.create_key(owner_id="provider-b", role="provider")
    return key_id, secret


@pytest.fixture
def provider_c_creds(auth):
    """Provider C — another provider."""
    key_id, secret = auth.create_key(owner_id="provider-c", role="provider")
    return key_id, secret


@pytest.fixture
def buyer_creds(auth):
    key_id, secret = auth.create_key(owner_id="buyer-1", role="buyer")
    return key_id, secret


@pytest.fixture
def client(db, auth):
    with TestClient(app, raise_server_exceptions=False) as c:
        app.state.db = db
        app.state.auth = auth
        # Clear cached referral manager so it re-creates with test db
        if hasattr(app.state, "referral_manager"):
            del app.state.referral_manager
        yield c


def _headers(creds):
    key_id, secret = creds
    return {"Authorization": f"Bearer {key_id}:{secret}"}


def _insert_usage(db, provider_id: str, amount: float, timestamp: str) -> None:
    """Insert a usage record for payout calculation tests."""
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO usage_records
               (id, buyer_id, service_id, provider_id, timestamp,
                latency_ms, status_code, amount_usd, payment_method, payment_tx)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                "buyer-test",
                f"svc-{uuid.uuid4().hex[:8]}",
                provider_id,
                timestamp,
                50,
                200,
                amount,
                "x402",
                None,
            ),
        )


# ── Code generation tests ──


class TestGenerateCode:
    def test_code_length(self):
        code = _generate_code()
        assert len(code) == CODE_LENGTH

    def test_code_is_alphanumeric(self):
        code = _generate_code()
        assert code.isalnum()

    def test_codes_are_unique(self):
        codes = {_generate_code() for _ in range(100)}
        assert len(codes) == 100


# ── ReferralManager unit tests ──


class TestReferralManagerGenerateCode:
    def test_generate_code_returns_record(self, manager):
        result = manager.generate_code("provider-a")
        assert result["referrer_provider_id"] == "provider-a"
        assert result["status"] == "pending"
        assert len(result["referral_code"]) == CODE_LENGTH
        assert result["id"] is not None
        assert result["created_at"] is not None

    def test_generate_multiple_codes(self, manager):
        r1 = manager.generate_code("provider-a")
        r2 = manager.generate_code("provider-a")
        assert r1["referral_code"] != r2["referral_code"]
        assert r1["id"] != r2["id"]

    def test_different_providers_get_different_codes(self, manager):
        r1 = manager.generate_code("provider-a")
        r2 = manager.generate_code("provider-b")
        assert r1["referral_code"] != r2["referral_code"]


class TestReferralManagerApplyCode:
    def test_apply_valid_code(self, manager):
        ref = manager.generate_code("provider-a")
        result = manager.apply_code("provider-b", ref["referral_code"])
        assert result["status"] == "active"
        assert result["referred_provider_id"] == "provider-b"
        assert result["referrer_provider_id"] == "provider-a"
        assert result["activated_at"] is not None

    def test_apply_invalid_code_raises(self, manager):
        with pytest.raises(ValueError, match="Invalid referral code"):
            manager.apply_code("provider-b", "NONEXIST")

    def test_apply_already_used_code_raises(self, manager):
        ref = manager.generate_code("provider-a")
        manager.apply_code("provider-b", ref["referral_code"])
        with pytest.raises(ValueError, match="already used"):
            manager.apply_code("provider-c", ref["referral_code"])

    def test_cannot_self_refer(self, manager):
        ref = manager.generate_code("provider-a")
        with pytest.raises(ValueError, match="Cannot use your own"):
            manager.apply_code("provider-a", ref["referral_code"])

    def test_provider_cannot_be_referred_twice(self, manager):
        r1 = manager.generate_code("provider-a")
        r2 = manager.generate_code("provider-c")
        manager.apply_code("provider-b", r1["referral_code"])
        with pytest.raises(ValueError, match="already referred"):
            manager.apply_code("provider-b", r2["referral_code"])


class TestReferralManagerGetReferrals:
    def test_empty_referrals(self, manager):
        result = manager.get_referrals("provider-a")
        assert result == []

    def test_returns_own_referrals_only(self, manager):
        manager.generate_code("provider-a")
        manager.generate_code("provider-a")
        manager.generate_code("provider-b")

        result_a = manager.get_referrals("provider-a")
        assert len(result_a) == 2
        for r in result_a:
            assert r["referrer_provider_id"] == "provider-a"

        result_b = manager.get_referrals("provider-b")
        assert len(result_b) == 1

    def test_includes_active_and_pending(self, manager):
        r1 = manager.generate_code("provider-a")
        r2 = manager.generate_code("provider-a")
        manager.apply_code("provider-b", r1["referral_code"])

        referrals = manager.get_referrals("provider-a")
        statuses = {r["referral_code"]: r["status"] for r in referrals}
        assert statuses[r1["referral_code"]] == "active"
        assert statuses[r2["referral_code"]] == "pending"


class TestReferralManagerCalculatePayout:
    def test_no_usage_no_payout(self, manager):
        ref = manager.generate_code("provider-a")
        manager.apply_code("provider-b", ref["referral_code"])

        payouts = manager.calculate_payout("provider-a", "2026-03")
        assert payouts == []

    def test_payout_calculation(self, db, manager):
        ref = manager.generate_code("provider-a")
        manager.apply_code("provider-b", ref["referral_code"])

        # Insert usage for referred provider (provider-b)
        _insert_usage(db, "provider-b", 100.0, "2026-03-15T10:00:00+00:00")
        _insert_usage(db, "provider-b", 50.0, "2026-03-16T10:00:00+00:00")

        payouts = manager.calculate_payout("provider-a", "2026-03")
        assert len(payouts) == 1
        payout = payouts[0]

        # Total revenue = $150
        # Platform commission = $150 * 10% = $15
        # Referral payout = $15 * 20% = $3
        assert payout["platform_revenue"] == Decimal("150")
        assert payout["payout_amount"] == Decimal("3")
        assert payout["referred_provider_id"] == "provider-b"
        assert payout["period"] == "2026-03"
        assert payout["status"] == "pending"

    def test_payout_only_for_matching_period(self, db, manager):
        ref = manager.generate_code("provider-a")
        manager.apply_code("provider-b", ref["referral_code"])

        _insert_usage(db, "provider-b", 100.0, "2026-03-15T10:00:00+00:00")
        _insert_usage(db, "provider-b", 200.0, "2026-04-01T10:00:00+00:00")

        payouts_march = manager.calculate_payout("provider-a", "2026-03")
        assert len(payouts_march) == 1
        assert payouts_march[0]["platform_revenue"] == Decimal("100")

    def test_multiple_referrals_multiple_payouts(self, db, manager):
        r1 = manager.generate_code("provider-a")
        r2 = manager.generate_code("provider-a")
        manager.apply_code("provider-b", r1["referral_code"])
        manager.apply_code("provider-c", r2["referral_code"])

        _insert_usage(db, "provider-b", 100.0, "2026-03-15T10:00:00+00:00")
        _insert_usage(db, "provider-c", 200.0, "2026-03-15T10:00:00+00:00")

        payouts = manager.calculate_payout("provider-a", "2026-03")
        assert len(payouts) == 2

        by_referred = {p["referred_provider_id"]: p for p in payouts}
        assert by_referred["provider-b"]["payout_amount"] == Decimal("2")  # 100*0.10*0.20
        assert by_referred["provider-c"]["payout_amount"] == Decimal("4")  # 200*0.10*0.20

    def test_payout_upsert_on_recalculation(self, db, manager):
        """Recalculating for the same period updates the existing payout."""
        ref = manager.generate_code("provider-a")
        manager.apply_code("provider-b", ref["referral_code"])

        _insert_usage(db, "provider-b", 100.0, "2026-03-15T10:00:00+00:00")
        payouts1 = manager.calculate_payout("provider-a", "2026-03")
        assert payouts1[0]["payout_amount"] == Decimal("2")

        # Add more usage and recalculate
        _insert_usage(db, "provider-b", 100.0, "2026-03-20T10:00:00+00:00")
        payouts2 = manager.calculate_payout("provider-a", "2026-03")
        assert payouts2[0]["payout_amount"] == Decimal("4")  # 200*0.10*0.20

    def test_pending_referrals_not_included(self, db, manager):
        """Pending (unused) referral codes don't generate payouts."""
        manager.generate_code("provider-a")  # Not applied

        _insert_usage(db, "provider-b", 100.0, "2026-03-15T10:00:00+00:00")
        payouts = manager.calculate_payout("provider-a", "2026-03")
        assert payouts == []


class TestReferralManagerGetStats:
    def test_empty_stats(self, manager):
        stats = manager.get_stats("provider-a")
        assert stats["total_referred"] == 0
        assert stats["active"] == 0
        assert stats["pending"] == 0
        assert stats["total_earned"] == Decimal("0")

    def test_stats_with_referrals(self, manager):
        r1 = manager.generate_code("provider-a")
        manager.generate_code("provider-a")  # pending code
        manager.apply_code("provider-b", r1["referral_code"])

        stats = manager.get_stats("provider-a")
        assert stats["total_referred"] == 2
        assert stats["active"] == 1
        assert stats["pending"] == 1

    def test_stats_with_earnings(self, db, manager):
        ref = manager.generate_code("provider-a")
        manager.apply_code("provider-b", ref["referral_code"])

        _insert_usage(db, "provider-b", 1000.0, "2026-03-15T10:00:00+00:00")
        manager.calculate_payout("provider-a", "2026-03")

        stats = manager.get_stats("provider-a")
        assert stats["total_earned"] == Decimal("20")  # 1000*0.10*0.20

    def test_stats_scoped_to_provider(self, manager):
        """Stats only include referrals where the provider is the referrer."""
        r1 = manager.generate_code("provider-a")
        manager.generate_code("provider-c")
        manager.apply_code("provider-b", r1["referral_code"])

        stats_a = manager.get_stats("provider-a")
        assert stats_a["total_referred"] == 1

        stats_c = manager.get_stats("provider-c")
        assert stats_c["total_referred"] == 1  # their own generated code


# ── API Route tests ──


class TestReferralAPIGenerateCode:
    def test_generate_code_success(self, client, provider_a_creds):
        resp = client.post(
            "/api/v1/referrals/code",
            headers=_headers(provider_a_creds),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "referral_code" in data
        assert len(data["referral_code"]) == CODE_LENGTH
        assert data["status"] == "pending"

    def test_generate_code_no_auth(self, client):
        resp = client.post("/api/v1/referrals/code")
        assert resp.status_code == 401


class TestReferralAPIApplyCode:
    def test_apply_code_success(self, client, provider_a_creds, provider_b_creds):
        # Generate code as provider A
        gen_resp = client.post(
            "/api/v1/referrals/code",
            headers=_headers(provider_a_creds),
        )
        code = gen_resp.json()["referral_code"]

        # Apply code as provider B
        resp = client.post(
            "/api/v1/referrals/apply",
            json={"code": code},
            headers=_headers(provider_b_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"
        assert data["referred_provider_id"] == "provider-b"

    def test_apply_invalid_code(self, client, provider_b_creds):
        resp = client.post(
            "/api/v1/referrals/apply",
            json={"code": "INVALID1"},
            headers=_headers(provider_b_creds),
        )
        assert resp.status_code == 400

    def test_apply_code_no_auth(self, client):
        resp = client.post(
            "/api/v1/referrals/apply",
            json={"code": "WHATEVER"},
        )
        assert resp.status_code == 401

    def test_apply_own_code_rejected(self, client, provider_a_creds):
        gen_resp = client.post(
            "/api/v1/referrals/code",
            headers=_headers(provider_a_creds),
        )
        code = gen_resp.json()["referral_code"]

        resp = client.post(
            "/api/v1/referrals/apply",
            json={"code": code},
            headers=_headers(provider_a_creds),
        )
        assert resp.status_code == 400
        assert "own" in resp.json()["error"].lower()


class TestReferralAPIListReferrals:
    def test_list_empty(self, client, provider_a_creds):
        resp = client.get(
            "/api/v1/referrals",
            headers=_headers(provider_a_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["referrals"] == []
        assert data["count"] == 0

    def test_list_with_referrals(self, client, provider_a_creds, provider_b_creds):
        # Generate two codes
        client.post("/api/v1/referrals/code", headers=_headers(provider_a_creds))
        gen_resp = client.post(
            "/api/v1/referrals/code", headers=_headers(provider_a_creds)
        )
        code = gen_resp.json()["referral_code"]

        # Apply one
        client.post(
            "/api/v1/referrals/apply",
            json={"code": code},
            headers=_headers(provider_b_creds),
        )

        resp = client.get(
            "/api/v1/referrals",
            headers=_headers(provider_a_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2

    def test_list_no_auth(self, client):
        resp = client.get("/api/v1/referrals")
        assert resp.status_code == 401


class TestReferralAPIStats:
    def test_stats_empty(self, client, provider_a_creds):
        resp = client.get(
            "/api/v1/referrals/stats",
            headers=_headers(provider_a_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_referred"] == 0
        assert data["active"] == 0
        assert data["total_earned"] == "0"

    def test_stats_with_referral(self, client, provider_a_creds, provider_b_creds):
        gen_resp = client.post(
            "/api/v1/referrals/code",
            headers=_headers(provider_a_creds),
        )
        code = gen_resp.json()["referral_code"]

        client.post(
            "/api/v1/referrals/apply",
            json={"code": code},
            headers=_headers(provider_b_creds),
        )

        resp = client.get(
            "/api/v1/referrals/stats",
            headers=_headers(provider_a_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_referred"] == 1
        assert data["active"] == 1

    def test_stats_no_auth(self, client):
        resp = client.get("/api/v1/referrals/stats")
        assert resp.status_code == 401


# ── Schema initialization ──


class TestReferralSchema:
    def test_tables_created(self, db, manager):
        """Verify referral tables exist after ReferralManager init."""
        with db.connect() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'referral%'"
            ).fetchall()
            table_names = {r["name"] for r in tables}
            assert "referrals" in table_names
            assert "referral_payouts" in table_names

    def test_schema_idempotent(self, db):
        """Creating ReferralManager twice doesn't fail."""
        m1 = ReferralManager(db)
        m2 = ReferralManager(db)
        assert m1 is not m2  # Different instances, same schema


# ── Constants ──


class TestConstants:
    def test_payout_rate(self):
        assert REFERRAL_PAYOUT_RATE == Decimal("0.20")

    def test_code_length(self):
        assert CODE_LENGTH == 8
