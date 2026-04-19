"""Tests for admin dashboard API endpoints."""
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
    """Create a fresh in-memory database for each test."""
    return Database(tmp_path / "admin_test.db")


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


@pytest.fixture
def admin_creds(auth):
    """Create an admin API key and return (key_id, secret) pair."""
    key_id, secret = auth.create_key(owner_id="admin-1", role="admin")
    return key_id, secret


@pytest.fixture
def buyer_creds(auth):
    """Create a buyer API key and return (key_id, secret) pair."""
    key_id, secret = auth.create_key(owner_id="buyer-1", role="buyer")
    return key_id, secret


@pytest.fixture
def client(db, auth):
    """Create a FastAPI TestClient with a fresh DB injected into app state.

    The startup event sets app.state from module-level objects, so we
    override app.state.db and app.state.auth *after* entering the
    TestClient context manager (which triggers startup).
    """
    with TestClient(app, raise_server_exceptions=False) as c:
        # Override after startup has run
        app.state.db = db
        app.state.auth = auth
        yield c


def _admin_headers(creds):
    """Build Authorization header from (key_id, secret) tuple."""
    key_id, secret = creds
    return {"Authorization": f"Bearer {key_id}:{secret}"}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _insert_service(db, service_id=None, provider_id="prov-1", name="Test Svc",
                    status="active"):
    """Insert a service directly into the DB."""
    sid = service_id or f"svc-{uuid.uuid4().hex[:8]}"
    now = _now_iso()
    db.insert_service({
        "id": sid,
        "provider_id": provider_id,
        "name": name,
        "endpoint": "https://example.com/api",
        "price_per_call": 0.01,
        "currency": "USDC",
        "payment_method": "x402",
        "free_tier_calls": 0,
        "status": status,
        "category": "test",
        "tags": [],
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    })
    return sid


def _insert_usage(db, service_id="svc-1", buyer_id="buyer-1",
                  provider_id="prov-1", amount_usd=0.01, status_code=200,
                  latency_ms=50, payment_method="x402", timestamp=None):
    """Insert a usage record directly into the DB."""
    record_id = f"usage-{uuid.uuid4().hex[:8]}"
    ts = timestamp or _now_iso()
    db.insert_usage({
        "id": record_id,
        "buyer_id": buyer_id,
        "service_id": service_id,
        "provider_id": provider_id,
        "timestamp": ts,
        "latency_ms": latency_ms,
        "status_code": status_code,
        "amount_usd": amount_usd,
        "payment_method": payment_method,
        "payment_tx": None,
    })
    return record_id


def _insert_agent(db, agent_id=None, owner_id="owner-1",
                  display_name="Test Agent", status="active"):
    """Insert an agent identity directly into the DB."""
    aid = agent_id or f"agent-{uuid.uuid4().hex[:8]}"
    now = _now_iso()
    db.insert_agent({
        "agent_id": aid,
        "display_name": display_name,
        "owner_id": owner_id,
        "identity_type": "api_key_only",
        "capabilities": [],
        "wallet_address": None,
        "verified": False,
        "reputation_score": 0.0,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "metadata": {},
    })
    return aid


def _insert_team(db, team_id=None, owner_id="owner-1", name="Test Team"):
    """Insert a team directly into the DB."""
    tid = team_id or f"team-{uuid.uuid4().hex[:8]}"
    now = _now_iso()
    db.insert_team({
        "id": tid,
        "name": name,
        "owner_id": owner_id,
        "description": "",
        "config": {},
        "status": "active",
        "created_at": now,
        "updated_at": now,
    })
    return tid


def _insert_settlement(db, provider_id="prov-1", total_amount=1.0,
                       status="pending"):
    """Insert a settlement directly into the DB."""
    sid = f"settle-{uuid.uuid4().hex[:8]}"
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO settlements
               (id, provider_id, period_start, period_end,
                total_amount, platform_fee, net_amount, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, provider_id, "2026-01-01", "2026-02-01",
             total_amount, total_amount * 0.1, total_amount * 0.9, status),
        )
    return sid


def _insert_webhook(db, owner_id="owner-1", active=True):
    """Insert a webhook directly into the DB."""
    wid = f"wh-{uuid.uuid4().hex[:8]}"
    db.insert_webhook({
        "id": wid,
        "owner_id": owner_id,
        "url": "https://example.com/hook",
        "events": ["service.called"],
        "secret": "test-secret",
        "active": active,
        "created_at": _now_iso(),
    })
    return wid


# ---------------------------------------------------------------------------
# Authentication / Authorization Tests
# ---------------------------------------------------------------------------


class TestAdminAuth:
    """Verify that admin endpoints reject unauthenticated / non-admin users."""

    def test_stats_requires_auth(self, client):
        resp = client.get("/api/v1/admin/stats")
        assert resp.status_code == 401

    def test_stats_rejects_buyer(self, client, buyer_creds):
        resp = client.get(
            "/api/v1/admin/stats",
            headers=_admin_headers(buyer_creds),
        )
        assert resp.status_code == 403

    def test_daily_usage_requires_auth(self, client):
        resp = client.get("/api/v1/admin/usage/daily")
        assert resp.status_code == 401

    def test_provider_ranking_requires_auth(self, client):
        resp = client.get("/api/v1/admin/providers/ranking")
        assert resp.status_code == 401

    def test_services_health_requires_auth(self, client):
        resp = client.get("/api/v1/admin/services/health")
        assert resp.status_code == 401

    def test_payments_summary_requires_auth(self, client):
        resp = client.get("/api/v1/admin/payments/summary")
        assert resp.status_code == 401

    def test_payments_summary_rejects_buyer(self, client, buyer_creds):
        resp = client.get(
            "/api/v1/admin/payments/summary",
            headers=_admin_headers(buyer_creds),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v1/admin/stats
# ---------------------------------------------------------------------------


class TestPlatformStats:
    def test_empty_db_returns_zeroes(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/stats",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_services"] == 0
        assert data["total_agents"] == 0
        assert data["total_teams"] == 0
        assert data["total_usage_records"] == 0
        assert data["total_revenue_usd"] == 0
        assert data["total_settlements"] == 0
        assert data["active_webhooks"] == 0

    def test_counts_active_services_only(self, client, db, admin_creds):
        _insert_service(db, status="active")
        _insert_service(db, status="active")
        _insert_service(db, status="removed")

        resp = client.get(
            "/api/v1/admin/stats",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert data["total_services"] == 2

    def test_counts_active_agents_only(self, client, db, admin_creds):
        _insert_agent(db, status="active")
        _insert_agent(db, status="deactivated")

        resp = client.get(
            "/api/v1/admin/stats",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert data["total_agents"] == 1

    def test_revenue_sums_correctly(self, client, db, admin_creds):
        _insert_usage(db, amount_usd=0.005)
        _insert_usage(db, amount_usd=0.010)
        _insert_usage(db, amount_usd=0.015)

        resp = client.get(
            "/api/v1/admin/stats",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert data["total_usage_records"] == 3
        assert abs(data["total_revenue_usd"] - 0.030) < 1e-6

    def test_counts_teams_settlements_webhooks(self, client, db, admin_creds):
        _insert_team(db)
        _insert_team(db)
        _insert_settlement(db)
        _insert_webhook(db, active=True)
        _insert_webhook(db, active=False)  # inactive should not count

        resp = client.get(
            "/api/v1/admin/stats",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert data["total_teams"] == 2
        assert data["total_settlements"] == 1
        assert data["active_webhooks"] == 1


# ---------------------------------------------------------------------------
# GET /api/v1/admin/usage/daily
# ---------------------------------------------------------------------------


class TestDailyUsage:
    def test_empty_returns_empty_array(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/usage/daily",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["days"] == 30
        assert data["data"] == []

    def test_groups_by_date(self, client, db, admin_creds):
        today = datetime.now(timezone.utc)
        yesterday = today - timedelta(days=1)

        _insert_usage(db, amount_usd=0.01, timestamp=today.isoformat())
        _insert_usage(db, amount_usd=0.02, timestamp=today.isoformat())
        _insert_usage(db, amount_usd=0.03, timestamp=yesterday.isoformat())

        resp = client.get(
            "/api/v1/admin/usage/daily",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert len(data["data"]) == 2

        # Sorted DESC — today first
        today_entry = data["data"][0]
        assert today_entry["call_count"] == 2
        assert abs(today_entry["revenue_usd"] - 0.03) < 1e-6

    def test_respects_days_param(self, client, db, admin_creds):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        _insert_usage(db, timestamp=old_ts)

        resp = client.get(
            "/api/v1/admin/usage/daily?days=30",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        # Record is 60 days old, so 30 day window shouldn't include it
        assert data["data"] == []

    def test_unique_buyers_and_services(self, client, db, admin_creds):
        ts = datetime.now(timezone.utc).isoformat()
        _insert_usage(db, buyer_id="b1", service_id="s1", timestamp=ts)
        _insert_usage(db, buyer_id="b1", service_id="s2", timestamp=ts)
        _insert_usage(db, buyer_id="b2", service_id="s1", timestamp=ts)

        resp = client.get(
            "/api/v1/admin/usage/daily",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert len(data["data"]) == 1
        entry = data["data"][0]
        assert entry["unique_buyers"] == 2
        assert entry["unique_services"] == 2

    def test_days_max_clamped(self, client, admin_creds):
        """Query param days > 90 should be rejected by validation."""
        resp = client.get(
            "/api/v1/admin/usage/daily?days=200",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 422  # FastAPI validation error


# ---------------------------------------------------------------------------
# GET /api/v1/admin/providers/ranking
# ---------------------------------------------------------------------------


class TestProviderRanking:
    def test_empty_returns_empty(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/providers/ranking",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["providers"] == []
        assert data["period"] == "all-time"

    def test_ranks_by_revenue_desc(self, client, db, admin_creds):
        _insert_usage(db, provider_id="prov-a", amount_usd=0.50)
        _insert_usage(db, provider_id="prov-b", amount_usd=1.00)

        resp = client.get(
            "/api/v1/admin/providers/ranking",
            headers=_admin_headers(admin_creds),
        )
        providers = resp.json()["providers"]
        assert len(providers) == 2
        assert providers[0]["provider_id"] == "prov-b"
        assert providers[0]["total_revenue"] > providers[1]["total_revenue"]

    def test_success_rate_calculation(self, client, db, admin_creds):
        _insert_usage(db, provider_id="prov-x", status_code=200)
        _insert_usage(db, provider_id="prov-x", status_code=200)
        _insert_usage(db, provider_id="prov-x", status_code=500)
        _insert_usage(db, provider_id="prov-x", status_code=502)

        resp = client.get(
            "/api/v1/admin/providers/ranking",
            headers=_admin_headers(admin_creds),
        )
        providers = resp.json()["providers"]
        assert len(providers) == 1
        # 2 successes out of 4 calls = 50%
        assert providers[0]["success_rate"] == 50.0

    def test_limit_param(self, client, db, admin_creds):
        for i in range(5):
            _insert_usage(db, provider_id=f"prov-{i}", amount_usd=float(i))

        resp = client.get(
            "/api/v1/admin/providers/ranking?limit=3",
            headers=_admin_headers(admin_creds),
        )
        providers = resp.json()["providers"]
        assert len(providers) == 3

    def test_display_name_from_agent(self, client, db, admin_creds):
        _insert_agent(db, owner_id="prov-named", display_name="Named Provider")
        _insert_usage(db, provider_id="prov-named", amount_usd=0.01)

        resp = client.get(
            "/api/v1/admin/providers/ranking",
            headers=_admin_headers(admin_creds),
        )
        providers = resp.json()["providers"]
        assert providers[0]["display_name"] == "Named Provider"


# ---------------------------------------------------------------------------
# GET /api/v1/admin/services/health
# ---------------------------------------------------------------------------


class TestServicesHealth:
    def test_empty_returns_empty(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/services/health",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        assert resp.json()["services"] == []

    def test_returns_active_services(self, client, db, admin_creds):
        sid = _insert_service(db, name="Healthy Svc")
        _insert_service(db, status="removed")

        resp = client.get(
            "/api/v1/admin/services/health",
            headers=_admin_headers(admin_creds),
        )
        services = resp.json()["services"]
        assert len(services) == 1
        assert services[0]["name"] == "Healthy Svc"
        assert services[0]["service_id"] == sid

    def test_error_rate_calculation(self, client, db, admin_creds):
        sid = _insert_service(db, service_id="svc-err-test")
        _insert_usage(db, service_id=sid, status_code=200)
        _insert_usage(db, service_id=sid, status_code=200)
        _insert_usage(db, service_id=sid, status_code=500)
        _insert_usage(db, service_id=sid, status_code=503)

        resp = client.get(
            "/api/v1/admin/services/health",
            headers=_admin_headers(admin_creds),
        )
        services = resp.json()["services"]
        svc = services[0]
        # 2 errors out of 4 calls = 50%
        assert svc["error_rate"] == 50.0

    def test_last_called_populated(self, client, db, admin_creds):
        sid = _insert_service(db, service_id="svc-last")
        ts = "2026-03-15T10:00:00+00:00"
        _insert_usage(db, service_id=sid, timestamp=ts)

        resp = client.get(
            "/api/v1/admin/services/health",
            headers=_admin_headers(admin_creds),
        )
        services = resp.json()["services"]
        assert services[0]["last_called"] == ts

    def test_service_with_no_usage(self, client, db, admin_creds):
        _insert_service(db, service_id="svc-no-use", name="Unused Svc")

        resp = client.get(
            "/api/v1/admin/services/health",
            headers=_admin_headers(admin_creds),
        )
        services = resp.json()["services"]
        svc = services[0]
        assert svc["avg_latency_ms"] == 0
        assert svc["error_rate"] == 0.0
        assert svc["last_called"] is None


# ---------------------------------------------------------------------------
# GET /api/v1/admin/payments/summary
# ---------------------------------------------------------------------------


class TestPaymentsSummary:
    def test_empty_returns_empty(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/payments/summary",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        assert resp.json()["methods"] == {}

    def test_groups_by_payment_method(self, client, db, admin_creds):
        _insert_usage(db, payment_method="x402", amount_usd=0.10)
        _insert_usage(db, payment_method="x402", amount_usd=0.20)
        _insert_usage(db, payment_method="stripe", amount_usd=0.50)

        resp = client.get(
            "/api/v1/admin/payments/summary",
            headers=_admin_headers(admin_creds),
        )
        methods = resp.json()["methods"]
        assert "x402" in methods
        assert "stripe" in methods
        assert methods["x402"]["count"] == 2
        assert abs(methods["x402"]["total_usd"] - 0.30) < 1e-6
        assert methods["stripe"]["count"] == 1
        assert abs(methods["stripe"]["total_usd"] - 0.50) < 1e-6

    def test_ordered_by_total_usd_desc(self, client, db, admin_creds):
        _insert_usage(db, payment_method="small", amount_usd=0.01)
        _insert_usage(db, payment_method="large", amount_usd=10.0)

        resp = client.get(
            "/api/v1/admin/payments/summary",
            headers=_admin_headers(admin_creds),
        )
        methods = resp.json()["methods"]
        keys = list(methods.keys())
        # large should come first because it has higher total
        assert keys[0] == "large"


# ---------------------------------------------------------------------------
# GET /api/v1/admin/analytics/trends
# ---------------------------------------------------------------------------


class TestAnalyticsTrends:
    def test_empty_returns_empty(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/analytics/trends",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["granularity"] == "weekly"
        assert data["data"] == []

    def test_daily_granularity(self, client, db, admin_creds):
        today = datetime.now(timezone.utc)
        _insert_usage(db, amount_usd=1.0, timestamp=today.isoformat())
        _insert_usage(db, amount_usd=2.0, timestamp=today.isoformat())

        resp = client.get(
            "/api/v1/admin/analytics/trends?granularity=daily",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert data["granularity"] == "daily"
        assert len(data["data"]) == 1
        assert data["data"][0]["calls"] == 2
        assert data["data"][0]["revenue"] == pytest.approx(3.0, abs=0.01)

    def test_monthly_granularity(self, client, db, admin_creds):
        _insert_usage(db, amount_usd=5.0)

        resp = client.get(
            "/api/v1/admin/analytics/trends?granularity=monthly",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert data["granularity"] == "monthly"
        assert len(data["data"]) >= 1

    def test_includes_success_rate(self, client, db, admin_creds):
        ts = datetime.now(timezone.utc).isoformat()
        _insert_usage(db, status_code=200, timestamp=ts)
        _insert_usage(db, status_code=500, timestamp=ts)

        resp = client.get(
            "/api/v1/admin/analytics/trends?granularity=daily",
            headers=_admin_headers(admin_creds),
        )
        entry = resp.json()["data"][0]
        assert entry["success_rate"] == 50.0

    def test_requires_admin(self, client, buyer_creds):
        resp = client.get(
            "/api/v1/admin/analytics/trends",
            headers=_admin_headers(buyer_creds),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v1/admin/analytics/top-services
# ---------------------------------------------------------------------------


class TestTopServices:
    def test_empty_returns_empty(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/analytics/top-services",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        assert resp.json()["services"] == []

    def test_ranks_by_revenue(self, client, db, admin_creds):
        sid_a = _insert_service(db, name="Svc A", service_id="svc-a")
        sid_b = _insert_service(db, name="Svc B", service_id="svc-b")
        _insert_usage(db, service_id=sid_a, amount_usd=1.0)
        _insert_usage(db, service_id=sid_b, amount_usd=5.0)

        resp = client.get(
            "/api/v1/admin/analytics/top-services?sort_by=revenue",
            headers=_admin_headers(admin_creds),
        )
        services = resp.json()["services"]
        assert len(services) == 2
        assert services[0]["service_id"] == sid_b  # higher revenue

    def test_ranks_by_calls(self, client, db, admin_creds):
        sid_a = _insert_service(db, name="Many Calls", service_id="svc-many")
        sid_b = _insert_service(db, name="Few Calls", service_id="svc-few")
        _insert_usage(db, service_id=sid_a, amount_usd=0.01)
        _insert_usage(db, service_id=sid_a, amount_usd=0.01)
        _insert_usage(db, service_id=sid_a, amount_usd=0.01)
        _insert_usage(db, service_id=sid_b, amount_usd=10.0)

        resp = client.get(
            "/api/v1/admin/analytics/top-services?sort_by=calls",
            headers=_admin_headers(admin_creds),
        )
        services = resp.json()["services"]
        assert services[0]["service_id"] == sid_a  # more calls

    def test_includes_unique_buyers(self, client, db, admin_creds):
        sid = _insert_service(db, service_id="svc-buyers")
        _insert_usage(db, service_id=sid, buyer_id="b1")
        _insert_usage(db, service_id=sid, buyer_id="b2")
        _insert_usage(db, service_id=sid, buyer_id="b1")

        resp = client.get(
            "/api/v1/admin/analytics/top-services",
            headers=_admin_headers(admin_creds),
        )
        svc = resp.json()["services"][0]
        assert svc["unique_buyers"] == 2

    def test_requires_admin(self, client, buyer_creds):
        resp = client.get(
            "/api/v1/admin/analytics/top-services",
            headers=_admin_headers(buyer_creds),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v1/admin/analytics/buyers
# ---------------------------------------------------------------------------


class TestBuyerMetrics:
    def test_empty_returns_zeroes(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/analytics/buyers",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_buyers_all_time"] == 0
        assert data["active_buyers"] == 0
        assert data["repeat_buyers"] == 0

    def test_counts_active_and_repeat(self, client, db, admin_creds):
        ts = datetime.now(timezone.utc).isoformat()
        _insert_usage(db, buyer_id="b1", timestamp=ts)
        _insert_usage(db, buyer_id="b1", timestamp=ts)  # repeat
        _insert_usage(db, buyer_id="b2", timestamp=ts)

        resp = client.get(
            "/api/v1/admin/analytics/buyers?days=30",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert data["active_buyers"] == 2
        assert data["repeat_buyers"] == 1  # b1 has >1 call
        assert data["repeat_rate"] == pytest.approx(50.0, abs=0.1)

    def test_top_spenders(self, client, db, admin_creds):
        ts = datetime.now(timezone.utc).isoformat()
        _insert_usage(db, buyer_id="big-spender", amount_usd=100.0, timestamp=ts)
        _insert_usage(db, buyer_id="small-spender", amount_usd=1.0, timestamp=ts)

        resp = client.get(
            "/api/v1/admin/analytics/buyers",
            headers=_admin_headers(admin_creds),
        )
        spenders = resp.json()["top_spenders"]
        assert len(spenders) == 2
        assert spenders[0]["buyer_id"] == "big-spender"
        assert spenders[0]["total_spent"] == pytest.approx(100.0, abs=0.01)

    def test_requires_admin(self, client, buyer_creds):
        resp = client.get(
            "/api/v1/admin/analytics/buyers",
            headers=_admin_headers(buyer_creds),
        )
        assert resp.status_code == 403
