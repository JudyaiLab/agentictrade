"""Tests for provider self-service API endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from marketplace.db import Database
from marketplace.auth import APIKeyManager
from api.main import app


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "provider_test.db")


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


@pytest.fixture
def provider_creds(auth):
    key_id, secret = auth.create_key(owner_id="prov-1", role="provider")
    return key_id, secret


@pytest.fixture
def admin_creds(auth):
    key_id, secret = auth.create_key(owner_id="admin-1", role="admin")
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
        yield c


def _headers(creds):
    key_id, secret = creds
    return {"Authorization": f"Bearer {key_id}:{secret}"}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _insert_service(db, service_id=None, provider_id="prov-1", name="Test Svc",
                    status="active", price=1.00):
    sid = service_id or f"svc-{uuid.uuid4().hex[:8]}"
    now = _now_iso()
    db.insert_service({
        "id": sid,
        "provider_id": provider_id,
        "name": name,
        "endpoint": "https://example.com/api",
        "price_per_call": price,
        "currency": "USDC",
        "payment_method": "x402",
        "free_tier_calls": 0,
        "status": status,
        "category": "crypto",
        "tags": ["test"],
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    })
    return sid


def _insert_usage(db, service_id, provider_id="prov-1", buyer_id="buyer-1",
                  amount=1.00, status_code=200, latency_ms=50, ts=None):
    uid = str(uuid.uuid4())
    ts = ts or _now_iso()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO usage_records
               (id, buyer_id, service_id, provider_id, timestamp,
                latency_ms, status_code, amount_usd, payment_method)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, buyer_id, service_id, provider_id, ts,
             latency_ms, status_code, amount, "x402"),
        )
    return uid


def _insert_settlement(db, provider_id="prov-1", total=10.0, fee=1.0,
                       net=9.0, status="completed"):
    sid = str(uuid.uuid4())
    now = _now_iso()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO settlements
               (id, provider_id, period_start, period_end,
                total_amount, platform_fee, net_amount, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, provider_id, now, now, total, fee, net, status),
        )
    return sid


# ===== Provider Dashboard =====

class TestProviderDashboard:
    """GET /api/v1/provider/dashboard — overview of provider's account."""

    def test_dashboard_returns_overview(self, client, db, provider_creds):
        svc_id = _insert_service(db, provider_id="prov-1")
        _insert_usage(db, svc_id, provider_id="prov-1", amount=2.00)
        _insert_usage(db, svc_id, provider_id="prov-1", amount=3.00)
        _insert_settlement(db, provider_id="prov-1", total=5.0, fee=0.5, net=4.5)

        resp = client.get("/api/v1/provider/dashboard", headers=_headers(provider_creds))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_services"] == 1
        assert data["total_calls"] == 2
        assert data["total_revenue"] == pytest.approx(5.0, abs=0.01)
        assert data["total_settled"] == pytest.approx(4.5, abs=0.01)

    def test_dashboard_buyer_denied(self, client, buyer_creds):
        resp = client.get("/api/v1/provider/dashboard", headers=_headers(buyer_creds))
        assert resp.status_code == 403

    def test_dashboard_no_auth(self, client):
        resp = client.get("/api/v1/provider/dashboard")
        assert resp.status_code == 401

    def test_dashboard_empty(self, client, provider_creds):
        resp = client.get("/api/v1/provider/dashboard", headers=_headers(provider_creds))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_services"] == 0
        assert data["total_calls"] == 0


# ===== Provider Services =====

class TestProviderServices:
    """GET /api/v1/provider/services — list provider's own services with stats."""

    def test_list_own_services(self, client, db, provider_creds):
        svc1 = _insert_service(db, provider_id="prov-1", name="Svc A")
        svc2 = _insert_service(db, provider_id="prov-1", name="Svc B")
        _insert_service(db, provider_id="other-prov", name="Other")
        _insert_usage(db, svc1, provider_id="prov-1", amount=1.00)
        _insert_usage(db, svc1, provider_id="prov-1", amount=2.00)

        resp = client.get("/api/v1/provider/services", headers=_headers(provider_creds))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["services"]) == 2  # only own services
        # Check the first service has stats
        svc_a = next(s for s in data["services"] if s["name"] == "Svc A")
        assert svc_a["total_calls"] == 2
        assert svc_a["total_revenue"] == pytest.approx(3.0, abs=0.01)

    def test_only_own_services(self, client, db, provider_creds):
        _insert_service(db, provider_id="other-prov", name="Not Mine")
        resp = client.get("/api/v1/provider/services", headers=_headers(provider_creds))
        assert resp.status_code == 200
        assert len(resp.json()["services"]) == 0


# ===== Service Analytics =====

class TestServiceAnalytics:
    """GET /api/v1/provider/services/{id}/analytics — per-service stats."""

    def test_analytics_for_own_service(self, client, db, provider_creds):
        svc_id = _insert_service(db, provider_id="prov-1")
        for i in range(5):
            _insert_usage(db, svc_id, provider_id="prov-1", amount=1.00,
                          latency_ms=50 + i * 10)
        _insert_usage(db, svc_id, provider_id="prov-1", amount=1.00,
                      status_code=500, latency_ms=200)

        resp = client.get(
            f"/api/v1/provider/services/{svc_id}/analytics",
            headers=_headers(provider_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 6
        assert data["total_revenue"] == pytest.approx(6.0, abs=0.01)
        assert data["success_rate"] == pytest.approx(5 / 6 * 100, abs=1)
        assert "avg_latency_ms" in data

    def test_analytics_for_others_service_denied(self, client, db, provider_creds):
        svc_id = _insert_service(db, provider_id="other-prov")
        resp = client.get(
            f"/api/v1/provider/services/{svc_id}/analytics",
            headers=_headers(provider_creds),
        )
        assert resp.status_code == 403

    def test_analytics_service_not_found(self, client, provider_creds):
        resp = client.get(
            "/api/v1/provider/services/nonexistent/analytics",
            headers=_headers(provider_creds),
        )
        assert resp.status_code == 404


# ===== Provider Earnings =====

class TestProviderEarnings:
    """GET /api/v1/provider/earnings — earnings summary and settlement history."""

    def test_earnings_summary(self, client, db, provider_creds):
        svc_id = _insert_service(db, provider_id="prov-1")
        _insert_usage(db, svc_id, provider_id="prov-1", amount=10.0)
        _insert_settlement(db, "prov-1", total=10.0, fee=1.0, net=9.0, status="completed")
        _insert_settlement(db, "prov-1", total=5.0, fee=0.5, net=4.5, status="pending")

        resp = client.get("/api/v1/provider/earnings", headers=_headers(provider_creds))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_earned"] == pytest.approx(10.0, abs=0.01)
        assert data["total_settled"] == pytest.approx(9.0, abs=0.01)
        assert data["pending_settlement"] == pytest.approx(4.5, abs=0.01)
        assert len(data["settlements"]) == 2

    def test_earnings_empty(self, client, provider_creds):
        resp = client.get("/api/v1/provider/earnings", headers=_headers(provider_creds))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_earned"] == 0
        assert data["total_settled"] == 0


# ===== Provider Keys =====

class TestProviderKeys:
    """GET/DELETE /api/v1/provider/keys — list and revoke own API keys."""

    def test_list_own_keys(self, client, db, auth, provider_creds):
        # provider_creds already created 1 key; create another
        auth.create_key(owner_id="prov-1", role="provider")
        auth.create_key(owner_id="other-prov", role="provider")

        resp = client.get("/api/v1/provider/keys", headers=_headers(provider_creds))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["keys"]) == 2  # only prov-1's keys

    def test_revoke_own_key(self, client, db, auth, provider_creds):
        key_id2, _ = auth.create_key(owner_id="prov-1", role="provider")

        resp = client.delete(
            f"/api/v1/provider/keys/{key_id2}",
            headers=_headers(provider_creds),
        )
        assert resp.status_code == 200

        # Verify key is gone
        resp2 = client.get("/api/v1/provider/keys", headers=_headers(provider_creds))
        key_ids = [k["key_id"] for k in resp2.json()["keys"]]
        assert key_id2 not in key_ids

    def test_revoke_other_providers_key_denied(self, client, db, auth, provider_creds):
        other_key, _ = auth.create_key(owner_id="other-prov", role="provider")
        resp = client.delete(
            f"/api/v1/provider/keys/{other_key}",
            headers=_headers(provider_creds),
        )
        assert resp.status_code == 403


# ===== Service Health Test =====

class TestServiceHealthCheck:
    """POST /api/v1/provider/services/{id}/test — test endpoint connectivity."""

    def test_health_check_own_service(self, client, db, provider_creds):
        svc_id = _insert_service(db, provider_id="prov-1")
        resp = client.post(
            f"/api/v1/provider/services/{svc_id}/test",
            headers=_headers(provider_creds),
        )
        # endpoint is example.com, may fail, but status should be 200 with result
        assert resp.status_code == 200
        data = resp.json()
        assert "reachable" in data
        assert "latency_ms" in data

    def test_health_check_others_service_denied(self, client, db, provider_creds):
        svc_id = _insert_service(db, provider_id="other-prov")
        resp = client.post(
            f"/api/v1/provider/services/{svc_id}/test",
            headers=_headers(provider_creds),
        )
        assert resp.status_code == 403


# ===== Onboarding Status =====

class TestOnboardingStatus:
    """GET /api/v1/provider/onboarding — track onboarding progress."""

    def test_new_provider_onboarding(self, client, provider_creds):
        resp = client.get("/api/v1/provider/onboarding", headers=_headers(provider_creds))
        assert resp.status_code == 200
        data = resp.json()
        assert "steps" in data
        assert data["steps"]["create_api_key"]["completed"] is True  # they have a key
        assert data["steps"]["register_service"]["completed"] is False
        assert data["completion_pct"] > 0

    def test_onboarding_with_service(self, client, db, provider_creds):
        _insert_service(db, provider_id="prov-1")
        resp = client.get("/api/v1/provider/onboarding", headers=_headers(provider_creds))
        data = resp.json()
        assert data["steps"]["register_service"]["completed"] is True
        assert data["completion_pct"] > data["steps"]["create_api_key"]["completed"]

    def test_onboarding_buyer_denied(self, client, buyer_creds):
        resp = client.get("/api/v1/provider/onboarding", headers=_headers(buyer_creds))
        assert resp.status_code == 403
