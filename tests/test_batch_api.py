"""Tests for batch operations API endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from marketplace.db import Database
from marketplace.auth import APIKeyManager
from api.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Fresh SQLite database for each test."""
    return Database(tmp_path / "batch_test.db")


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


@pytest.fixture
def admin_creds(auth):
    """Admin key pair (key_id, secret)."""
    key_id, secret = auth.create_key(owner_id="admin-fleet", role="admin")
    return key_id, secret


@pytest.fixture
def buyer_creds(auth):
    """Buyer key pair (not admin)."""
    key_id, secret = auth.create_key(owner_id="buyer-fleet", role="buyer")
    return key_id, secret


@pytest.fixture
def client(db, auth):
    """TestClient with fresh DB and auth injected into app state."""
    with TestClient(app, raise_server_exceptions=False) as c:
        app.state.db = db
        app.state.auth = auth
        yield c


def _admin_headers(creds):
    key_id, secret = creds
    return {"Authorization": f"Bearer {key_id}:{secret}"}


def _buyer_headers(creds):
    key_id, secret = creds
    return {"Authorization": f"Bearer {key_id}:{secret}"}


# ---------------------------------------------------------------------------
# POST /api/v1/batch/keys
# ---------------------------------------------------------------------------

class TestBatchCreateKeys:
    def test_batch_create_5_keys_returns_all(self, client, admin_creds):
        """Batch create 5 keys — all 5 should be returned with key_id and api_key."""
        resp = client.post(
            "/api/v1/batch/keys",
            json={"count": 5, "owner_id": "org-123", "role": "buyer", "rate_limit": 100},
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["count"] == 5
        assert len(body["keys"]) == 5
        for item in body["keys"]:
            assert "key_id" in item
            assert "api_key" in item
            # api_key should be formatted as "key_id:secret"
            assert ":" in item["api_key"]
            assert item["api_key"].startswith(item["key_id"])

    def test_batch_create_keys_all_unique(self, client, admin_creds):
        """All returned key_ids must be unique."""
        resp = client.post(
            "/api/v1/batch/keys",
            json={"count": 10, "owner_id": "org-unique", "role": "buyer", "rate_limit": 60},
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 201
        body = resp.json()
        key_ids = [k["key_id"] for k in body["keys"]]
        assert len(key_ids) == len(set(key_ids)), "Duplicate key_ids returned"

    def test_batch_create_exceeding_limit_returns_400(self, client, admin_creds):
        """Requesting 11 keys (above cap of 10) must return 422 validation error."""
        resp = client.post(
            "/api/v1/batch/keys",
            json={"count": 11, "owner_id": "org-123", "role": "buyer", "rate_limit": 60},
            headers=_admin_headers(admin_creds),
        )
        # Pydantic validation rejects before handler — 422 Unprocessable Entity
        assert resp.status_code == 422

    def test_batch_create_exactly_at_limit_succeeds(self, client, admin_creds):
        """Requesting exactly 10 keys (the cap) must succeed."""
        resp = client.post(
            "/api/v1/batch/keys",
            json={"count": 10, "owner_id": "org-cap", "role": "buyer", "rate_limit": 60},
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 201
        assert resp.json()["count"] == 10

    def test_batch_create_zero_count_returns_422(self, client, admin_creds):
        """count=0 should fail validation."""
        resp = client.post(
            "/api/v1/batch/keys",
            json={"count": 0, "owner_id": "org-123", "role": "buyer", "rate_limit": 60},
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 422

    def test_batch_create_invalid_role_returns_422(self, client, admin_creds):
        """Invalid role must be rejected."""
        resp = client.post(
            "/api/v1/batch/keys",
            json={"count": 1, "owner_id": "org-123", "role": "superuser", "rate_limit": 60},
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 422

    def test_batch_create_non_admin_returns_403(self, client, buyer_creds):
        """Non-admin key must receive 403."""
        resp = client.post(
            "/api/v1/batch/keys",
            json={"count": 5, "owner_id": "org-123", "role": "buyer", "rate_limit": 60},
            headers=_buyer_headers(buyer_creds),
        )
        assert resp.status_code == 403

    def test_batch_create_no_auth_returns_401(self, client):
        """Missing auth header must return 401."""
        resp = client.post(
            "/api/v1/batch/keys",
            json={"count": 1, "owner_id": "org-123", "role": "buyer", "rate_limit": 60},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/batch/deposits
# ---------------------------------------------------------------------------

class TestBatchDeposits:
    def test_batch_deposit_3_buyers(self, client, admin_creds, db):
        """Deposit to 3 buyers — each should show updated balance."""
        resp = client.post(
            "/api/v1/batch/deposits",
            json={
                "deposits": [
                    {"buyer_id": "buyer-A", "amount": "100.00"},
                    {"buyer_id": "buyer-B", "amount": "50.00"},
                    {"buyer_id": "buyer-C", "amount": "25.50"},
                ]
            },
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 3
        assert body["total_deposited"] == "175.50"

        balances = {r["buyer_id"]: r["new_balance"] for r in body["results"]}
        assert balances["buyer-A"] == "100.00"
        assert balances["buyer-B"] == "50.00"
        assert balances["buyer-C"] == "25.50"

    def test_batch_deposit_accumulates_existing_balance(self, client, admin_creds, db):
        """Second deposit to same buyer_id adds to existing balance."""
        from decimal import Decimal
        db.credit_balance("buyer-existing", Decimal("50.00"))

        resp = client.post(
            "/api/v1/batch/deposits",
            json={"deposits": [{"buyer_id": "buyer-existing", "amount": "30.00"}]},
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        result = resp.json()["results"][0]
        assert result["new_balance"] == "80.00"

    def test_batch_deposit_total_deposited_sum(self, client, admin_creds):
        """total_deposited in response must be the sum of all individual amounts."""
        resp = client.post(
            "/api/v1/batch/deposits",
            json={
                "deposits": [
                    {"buyer_id": "b1", "amount": "10.00"},
                    {"buyer_id": "b2", "amount": "20.00"},
                    {"buyer_id": "b3", "amount": "30.00"},
                ]
            },
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        assert resp.json()["total_deposited"] == "60.00"

    def test_batch_deposit_exceeds_limit_returns_422(self, client, admin_creds):
        """101 deposits (above cap of 100) must be rejected."""
        deposits = [{"buyer_id": f"b-{i}", "amount": "1.00"} for i in range(101)]
        resp = client.post(
            "/api/v1/batch/deposits",
            json={"deposits": deposits},
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 422

    def test_batch_deposit_invalid_amount_returns_422(self, client, admin_creds):
        """Non-numeric amount must fail validation."""
        resp = client.post(
            "/api/v1/batch/deposits",
            json={"deposits": [{"buyer_id": "b1", "amount": "abc"}]},
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 422

    def test_batch_deposit_zero_amount_returns_422(self, client, admin_creds):
        """Zero amount must fail validation."""
        resp = client.post(
            "/api/v1/batch/deposits",
            json={"deposits": [{"buyer_id": "b1", "amount": "0.00"}]},
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 422

    def test_batch_deposit_non_admin_returns_403(self, client, buyer_creds):
        """Non-admin key must receive 403."""
        resp = client.post(
            "/api/v1/batch/deposits",
            json={"deposits": [{"buyer_id": "b1", "amount": "10.00"}]},
            headers=_buyer_headers(buyer_creds),
        )
        assert resp.status_code == 403

    def test_batch_deposit_no_auth_returns_401(self, client):
        """Missing auth header must return 401."""
        resp = client.post(
            "/api/v1/batch/deposits",
            json={"deposits": [{"buyer_id": "b1", "amount": "10.00"}]},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/batch/usage
# ---------------------------------------------------------------------------

class TestBatchUsage:
    def _insert_usage(self, db, buyer_id: str, amount_usd: float = 0.01):
        """Helper to insert a usage record for a buyer."""
        db.insert_usage({
            "id": str(uuid.uuid4()),
            "buyer_id": buyer_id,
            "service_id": "svc-test",
            "provider_id": "prov-test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latency_ms": 100,
            "status_code": 200,
            "amount_usd": amount_usd,
            "payment_method": "balance",
            "payment_tx": None,
        })

    def test_aggregate_usage_for_owner(self, client, admin_creds, auth, db):
        """Usage query returns aggregated counts and spend across all owner's keys."""
        # Create 3 buyer keys under same owner
        kid1, _ = auth.create_key(owner_id="org-usage", role="buyer")
        kid2, _ = auth.create_key(owner_id="org-usage", role="buyer")
        kid3, _ = auth.create_key(owner_id="org-usage", role="buyer")

        # Insert usage records using key_ids as buyer_id (standard pattern)
        self._insert_usage(db, kid1, 1.00)
        self._insert_usage(db, kid1, 2.00)
        self._insert_usage(db, kid2, 3.00)
        # kid3 has no usage

        resp = client.get(
            "/api/v1/batch/usage",
            params={"owner_id": "org-usage"},
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["owner_id"] == "org-usage"
        assert body["total_calls"] == 3
        assert body["keys_count"] == 3
        assert "period" in body
        # total_spend should include all 3 records: 1.00 + 2.00 + 3.00 = 6.00
        from decimal import Decimal
        assert Decimal(body["total_spend"]) == Decimal("6.00")

    def test_usage_owner_with_no_keys_returns_zeros(self, client, admin_creds):
        """Owner with no keys should return zero counts and empty-period data."""
        resp = client.get(
            "/api/v1/batch/usage",
            params={"owner_id": "org-nobody"},
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["owner_id"] == "org-nobody"
        assert body["total_calls"] == 0
        assert body["keys_count"] == 0
        assert body["total_spend"] == "0.00"

    def test_usage_missing_owner_id_returns_422(self, client, admin_creds):
        """Missing owner_id query param must return 422."""
        resp = client.get(
            "/api/v1/batch/usage",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 422

    def test_usage_non_admin_returns_403(self, client, buyer_creds):
        """Non-admin key must receive 403."""
        resp = client.get(
            "/api/v1/batch/usage",
            params={"owner_id": "org-123"},
            headers=_buyer_headers(buyer_creds),
        )
        assert resp.status_code == 403

    def test_usage_no_auth_returns_401(self, client):
        """Missing auth header must return 401."""
        resp = client.get(
            "/api/v1/batch/usage",
            params={"owner_id": "org-123"},
        )
        assert resp.status_code == 401

    def test_usage_period_field_format(self, client, admin_creds, auth):
        """Period field must be formatted as YYYY-MM."""
        auth.create_key(owner_id="org-period", role="buyer")
        resp = client.get(
            "/api/v1/batch/usage",
            params={"owner_id": "org-period"},
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        period = resp.json()["period"]
        assert len(period) == 7
        assert period[4] == "-"

    def test_usage_custom_date_range_iso_dates(self, client, admin_creds, auth, db):
        """Custom date range using ISO date strings should filter usage records."""
        from datetime import datetime, timezone, timedelta

        # Create a key
        kid, _ = auth.create_key(owner_id="org-range", role="buyer")

        # Insert usage on 2026-01-15
        jan_record = {
            "id": str(uuid.uuid4()),
            "buyer_id": kid,
            "service_id": "svc-test",
            "provider_id": "prov-test",
            "timestamp": "2026-01-15T12:00:00+00:00",
            "latency_ms": 100,
            "status_code": 200,
            "amount_usd": 5.00,
            "payment_method": "balance",
            "payment_tx": None,
        }
        db.insert_usage(jan_record)

        # Insert usage on 2026-02-10
        feb_record = {
            "id": str(uuid.uuid4()),
            "buyer_id": kid,
            "service_id": "svc-test",
            "provider_id": "prov-test",
            "timestamp": "2026-02-10T12:00:00+00:00",
            "latency_ms": 100,
            "status_code": 200,
            "amount_usd": 10.00,
            "payment_method": "balance",
            "payment_tx": None,
        }
        db.insert_usage(feb_record)

        # Query for January only
        resp = client.get(
            "/api/v1/batch/usage",
            params={
                "owner_id": "org-range",
                "period_start": "2026-01-01",
                "period_end": "2026-01-31",
            },
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_calls"] == 1
        from decimal import Decimal
        assert Decimal(body["total_spend"]) == Decimal("5.00")
        assert "2026-01-01" in body["period"]

        # Query for February only
        resp = client.get(
            "/api/v1/batch/usage",
            params={
                "owner_id": "org-range",
                "period_start": "2026-02-01",
                "period_end": "2026-02-28",
            },
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_calls"] == 1
        assert Decimal(body["total_spend"]) == Decimal("10.00")

        # Query for both months
        resp = client.get(
            "/api/v1/batch/usage",
            params={
                "owner_id": "org-range",
                "period_start": "2026-01-01",
                "period_end": "2026-02-28",
            },
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_calls"] == 2
        assert Decimal(body["total_spend"]) == Decimal("15.00")

    def test_usage_custom_date_range_datetime_iso(self, client, admin_creds, auth, db):
        """Custom date range using full ISO 8601 datetime strings."""
        from datetime import datetime, timezone

        kid, _ = auth.create_key(owner_id="org-datetime", role="buyer")

        # Insert usage at specific times
        db.insert_usage({
            "id": str(uuid.uuid4()),
            "buyer_id": kid,
            "service_id": "svc-test",
            "provider_id": "prov-test",
            "timestamp": "2026-03-15T10:30:00+00:00",
            "latency_ms": 100,
            "status_code": 200,
            "amount_usd": 7.50,
            "payment_method": "balance",
            "payment_tx": None,
        })

        # Query using full datetime format
        resp = client.get(
            "/api/v1/batch/usage",
            params={
                "owner_id": "org-datetime",
                "period_start": "2026-03-15T00:00:00Z",
                "period_end": "2026-03-15T23:59:59Z",
            },
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_calls"] == 1
        from decimal import Decimal
        assert Decimal(body["total_spend"]) == Decimal("7.50")

    def test_usage_invalid_period_start_returns_400(self, client, admin_creds):
        """Invalid period_start format must return 400."""
        resp = client.get(
            "/api/v1/batch/usage",
            params={
                "owner_id": "org-123",
                "period_start": "not-a-date",
                "period_end": "2026-01-31",
            },
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 400
        assert "Invalid date format" in resp.json()["error"]

    def test_usage_invalid_period_end_returns_400(self, client, admin_creds):
        """Invalid period_end format must return 400."""
        resp = client.get(
            "/api/v1/batch/usage",
            params={
                "owner_id": "org-123",
                "period_start": "2026-01-01",
                "period_end": "invalid-date",
            },
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 400
        assert "Invalid date format" in resp.json()["error"]

    def test_usage_period_start_after_period_end_returns_400(self, client, admin_creds):
        """period_start after period_end must return 400."""
        resp = client.get(
            "/api/v1/batch/usage",
            params={
                "owner_id": "org-123",
                "period_start": "2026-02-01",
                "period_end": "2026-01-01",
            },
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 400
        assert "period_start must be before or equal to period_end" in resp.json()["error"]

    def test_usage_only_period_start_uses_default(self, client, admin_creds, auth, db):
        """Providing only period_start without period_end should fall back to current month default."""
        kid, _ = auth.create_key(owner_id="org-partial", role="buyer")
        self._insert_usage(db, kid, 1.00)

        # Should fall back to current month even though period_start is provided alone
        resp = client.get(
            "/api/v1/batch/usage",
            params={
                "owner_id": "org-partial",
                "period_start": "2026-01-01",
            },
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        # Since only one param is set, both should be None, falling back to default month logic
        body = resp.json()
        assert "period" in body

    def test_usage_date_range_with_no_records_returns_zero(self, client, admin_creds, auth):
        """Date range query with no matching records should return 0 spend and calls."""
        kid, _ = auth.create_key(owner_id="org-empty-range", role="buyer")

        resp = client.get(
            "/api/v1/batch/usage",
            params={
                "owner_id": "org-empty-range",
                "period_start": "2025-01-01",
                "period_end": "2025-01-31",
            },
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_calls"] == 0
        assert body["total_spend"] == "0.00"
        assert body["keys_count"] == 1
