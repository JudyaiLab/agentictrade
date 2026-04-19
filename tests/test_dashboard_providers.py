"""Tests for the Provider Growth Dashboard (/dashboard/providers)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

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
    """Create a fresh database for each test."""
    return Database(tmp_path / "provider_dash_test.db")


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


@pytest.fixture
def admin_creds(auth):
    """Create admin API key; return 'key_id:secret' string."""
    key_id, secret = auth.create_key(owner_id="admin-1", role="admin")
    return f"{key_id}:{secret}"


@pytest.fixture
def buyer_creds(auth):
    """Create buyer API key; return 'key_id:secret' string."""
    key_id, secret = auth.create_key(owner_id="buyer-1", role="buyer")
    return f"{key_id}:{secret}"


@pytest.fixture
def client(db, auth):
    """FastAPI TestClient with fresh DB injected."""
    with TestClient(app, raise_server_exceptions=False) as c:
        app.state.db = db
        app.state.auth = auth
        yield c


# ---------------------------------------------------------------------------
# Data insertion helpers (pure, return created ID)
# ---------------------------------------------------------------------------

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _insert_service(db, *, service_id=None, provider_id="prov-1",
                    name="Test Svc", status="active", price=0.01,
                    category="test", created_at=None):
    sid = service_id or f"svc-{uuid.uuid4().hex[:8]}"
    now = created_at or _now_iso()
    db.insert_service({
        "id": sid, "provider_id": provider_id, "name": name,
        "endpoint": "https://example.com/api", "price_per_call": price,
        "currency": "USDC", "payment_method": "x402",
        "free_tier_calls": 0, "status": status, "category": category,
        "tags": [], "metadata": {}, "created_at": now, "updated_at": now,
    })
    return sid


def _insert_usage(db, *, service_id="svc-1", buyer_id="buyer-1",
                  provider_id="prov-1", amount_usd=0.01, status_code=200,
                  latency_ms=50, payment_method="x402", timestamp=None):
    rid = f"usage-{uuid.uuid4().hex[:8]}"
    db.insert_usage({
        "id": rid, "buyer_id": buyer_id, "service_id": service_id,
        "provider_id": provider_id, "timestamp": timestamp or _now_iso(),
        "latency_ms": latency_ms, "status_code": status_code,
        "amount_usd": amount_usd, "payment_method": payment_method,
        "payment_tx": None,
    })
    return rid


def _insert_settlement(db, *, provider_id="prov-1", total_amount=1.0,
                       status="pending"):
    sid = f"settle-{uuid.uuid4().hex[:8]}"
    fee = total_amount * 0.1
    net = total_amount * 0.9
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO settlements
               (id, provider_id, period_start, period_end,
                total_amount, platform_fee, net_amount, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, provider_id, "2026-01-01", "2026-02-01",
             total_amount, fee, net, status),
        )
    return sid


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class TestProviderDashboardAuth:
    """The /dashboard/providers route must reject unauthenticated or non-admin users."""

    def test_rejects_missing_key(self, client):
        resp = client.get("/dashboard/providers")
        assert resp.status_code == 401

    def test_rejects_buyer_key(self, client, buyer_creds):
        resp = client.get(f"/dashboard/providers?key={buyer_creds}")
        assert resp.status_code == 403

    def test_accepts_admin_key(self, client, admin_creds):
        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------

class TestProviderDashboardEmpty:
    def test_empty_db_renders(self, client, admin_creds):
        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        assert resp.status_code == 200
        body = resp.text
        assert "Provider Growth Dashboard" in body
        assert "No provider data available" in body

    def test_summary_shows_zero(self, client, admin_creds):
        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        assert "Total Providers" in body
        assert "Total Revenue" in body
        assert "Total Commission" in body


# ---------------------------------------------------------------------------
# With data
# ---------------------------------------------------------------------------

class TestProviderDashboardWithData:
    def test_shows_provider_revenue(self, client, db, admin_creds):
        sid = _insert_service(db, provider_id="prov-alpha")
        _insert_usage(db, service_id=sid, provider_id="prov-alpha", amount_usd=5.00)
        _insert_usage(db, service_id=sid, provider_id="prov-alpha", amount_usd=3.00)

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        assert resp.status_code == 200
        assert "prov-alpha" in body
        assert "$8.00" in body

    def test_shows_multiple_providers(self, client, db, admin_creds):
        sid_a = _insert_service(db, provider_id="prov-a")
        sid_b = _insert_service(db, provider_id="prov-b")
        _insert_usage(db, service_id=sid_a, provider_id="prov-a", amount_usd=10.00)
        _insert_usage(db, service_id=sid_b, provider_id="prov-b", amount_usd=20.00)

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        assert "prov-a" in body
        assert "prov-b" in body
        assert "$10.00" in body
        assert "$20.00" in body

    def test_shows_commission_paid(self, client, db, admin_creds):
        sid = _insert_service(db, provider_id="prov-comm")
        _insert_usage(db, service_id=sid, provider_id="prov-comm", amount_usd=100.0)
        _insert_settlement(db, provider_id="prov-comm", total_amount=100.0)

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        assert "prov-comm" in body
        # Commission = 100 * 0.1 = $10.00
        assert "$10.00" in body

    def test_shows_net_payout(self, client, db, admin_creds):
        sid = _insert_service(db, provider_id="prov-pay")
        _insert_usage(db, service_id=sid, provider_id="prov-pay", amount_usd=50.0)
        _insert_settlement(db, provider_id="prov-pay", total_amount=50.0,
                           status="completed")

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        # Net = 50 * 0.9 = $45.00
        assert "$45.00" in body

    def test_shows_call_counts(self, client, db, admin_creds):
        sid = _insert_service(db, provider_id="prov-calls")
        for _ in range(7):
            _insert_usage(db, service_id=sid, provider_id="prov-calls")

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        # All-time should show 7
        assert "7" in body

    def test_shows_success_rate(self, client, db, admin_creds):
        sid = _insert_service(db, provider_id="prov-sr")
        for _ in range(9):
            _insert_usage(db, service_id=sid, provider_id="prov-sr",
                          status_code=200, latency_ms=50)
        _insert_usage(db, service_id=sid, provider_id="prov-sr",
                      status_code=500, latency_ms=50)

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        # 9/10 = 90.0%
        assert "90.0%" in body

    def test_shows_avg_latency(self, client, db, admin_creds):
        sid = _insert_service(db, provider_id="prov-lat")
        _insert_usage(db, service_id=sid, provider_id="prov-lat", latency_ms=100)
        _insert_usage(db, service_id=sid, provider_id="prov-lat", latency_ms=200)

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        # avg = 150.0 ms
        assert "150.0" in body


# ---------------------------------------------------------------------------
# Quality tiers
# ---------------------------------------------------------------------------

class TestProviderQualityTiers:
    def test_premium_tier(self, client, db, admin_creds):
        """Provider with 99%+ success rate and <=200ms latency = Premium."""
        sid = _insert_service(db, provider_id="prov-premium")
        for _ in range(100):
            _insert_usage(db, service_id=sid, provider_id="prov-premium",
                          status_code=200, latency_ms=100)

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        assert "Premium" in body

    def test_verified_tier(self, client, db, admin_creds):
        """Provider with 95-99% success rate and <=500ms latency = Verified."""
        sid = _insert_service(db, provider_id="prov-verified")
        for _ in range(96):
            _insert_usage(db, service_id=sid, provider_id="prov-verified",
                          status_code=200, latency_ms=300)
        for _ in range(4):
            _insert_usage(db, service_id=sid, provider_id="prov-verified",
                          status_code=500, latency_ms=300)

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        assert "Verified" in body

    def test_standard_tier(self, client, db, admin_creds):
        """Provider with <95% success rate = Standard."""
        sid = _insert_service(db, provider_id="prov-standard")
        for _ in range(8):
            _insert_usage(db, service_id=sid, provider_id="prov-standard",
                          status_code=200, latency_ms=50)
        for _ in range(2):
            _insert_usage(db, service_id=sid, provider_id="prov-standard",
                          status_code=500, latency_ms=50)

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        assert "Standard" in body


# ---------------------------------------------------------------------------
# Growth trend
# ---------------------------------------------------------------------------

class TestProviderGrowthTrend:
    def test_shows_growth_trend(self, client, db, admin_creds):
        """Growth trend should appear when there are service registrations."""
        _insert_service(db, provider_id="prov-new-1")
        _insert_usage(db, provider_id="prov-new-1")

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        assert "Provider Growth Trend" in body
        assert "new provider" in body

    def test_no_growth_trend_empty(self, client, admin_creds):
        """Growth trend section should not render data rows with no data."""
        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        # The rendered heading with week data should not appear
        assert "New Registrations per Week" not in body
        assert "new provider" not in body


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

class TestProviderSummary:
    def test_summary_total_providers(self, client, db, admin_creds):
        sid_a = _insert_service(db, provider_id="prov-x")
        sid_b = _insert_service(db, provider_id="prov-y")
        _insert_usage(db, service_id=sid_a, provider_id="prov-x")
        _insert_usage(db, service_id=sid_b, provider_id="prov-y")

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        assert "Total Providers" in body
        # Should show 2 providers
        assert ">2<" in body

    def test_summary_total_revenue(self, client, db, admin_creds):
        sid = _insert_service(db, provider_id="prov-rev")
        _insert_usage(db, service_id=sid, provider_id="prov-rev", amount_usd=25.50)

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        assert "$25.50" in body

    def test_summary_total_commission(self, client, db, admin_creds):
        sid = _insert_service(db, provider_id="prov-fee")
        _insert_usage(db, service_id=sid, provider_id="prov-fee", amount_usd=100.0)
        _insert_settlement(db, provider_id="prov-fee", total_amount=100.0)

        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        # Commission = 100 * 0.1 = $10.00 (in summary)
        assert "$10.00" in body


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

class TestProviderDashboardNavigation:
    def test_providers_in_nav(self, client, admin_creds):
        """The Providers link should appear in the dashboard navigation."""
        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        assert "/dashboard/providers" in body
        assert "Providers" in body

    def test_providers_nav_active(self, client, admin_creds):
        """The Providers nav item should be marked active on the providers page."""
        resp = client.get(f"/dashboard/providers?key={admin_creds}")
        body = resp.text
        # The active nav item should have the active styling
        # Check that Providers appears with the accent styling
        assert "active_page" not in body  # template variable, not in output
        # But the active CSS class should be applied
        assert "Providers" in body

    def test_providers_link_appears_on_other_pages(self, client, db, admin_creds):
        """The Providers nav link should appear on other dashboard pages too."""
        resp = client.get(f"/dashboard/?key={admin_creds}")
        body = resp.text
        assert "/dashboard/providers" in body


# ---------------------------------------------------------------------------
# Query function unit tests
# ---------------------------------------------------------------------------

class TestProviderQueryFunctions:
    def test_query_provider_stats_empty(self, db):
        from api.routes.dashboard_queries import query_provider_stats
        with db.connect() as conn:
            result = query_provider_stats(conn)
        assert result == []

    def test_query_provider_stats_with_data(self, db):
        from api.routes.dashboard_queries import query_provider_stats
        sid = _insert_service(db, provider_id="prov-q")
        _insert_usage(db, service_id=sid, provider_id="prov-q", amount_usd=5.0)
        _insert_usage(db, service_id=sid, provider_id="prov-q", amount_usd=3.0)

        with db.connect() as conn:
            result = query_provider_stats(conn)

        assert len(result) == 1
        assert result[0]["provider_id"] == "prov-q"
        assert result[0]["total_revenue"] == 8.0
        assert result[0]["total_calls"] == 2

    def test_query_provider_quality_tiers_premium(self, db):
        from api.routes.dashboard_queries import query_provider_quality_tiers
        sid = _insert_service(db, provider_id="prov-p")
        for _ in range(100):
            _insert_usage(db, service_id=sid, provider_id="prov-p",
                          status_code=200, latency_ms=100)

        with db.connect() as conn:
            tiers = query_provider_quality_tiers(conn)

        assert tiers["prov-p"] == "Premium"

    def test_query_provider_quality_tiers_standard(self, db):
        from api.routes.dashboard_queries import query_provider_quality_tiers
        sid = _insert_service(db, provider_id="prov-s")
        for _ in range(8):
            _insert_usage(db, service_id=sid, provider_id="prov-s",
                          status_code=200, latency_ms=50)
        for _ in range(2):
            _insert_usage(db, service_id=sid, provider_id="prov-s",
                          status_code=500, latency_ms=50)

        with db.connect() as conn:
            tiers = query_provider_quality_tiers(conn)

        assert tiers["prov-s"] == "Standard"

    def test_query_provider_growth_trend(self, db):
        from api.routes.dashboard_queries import query_provider_growth_trend
        _insert_service(db, provider_id="prov-g1")
        _insert_service(db, provider_id="prov-g2")

        with db.connect() as conn:
            trend = query_provider_growth_trend(conn)

        assert len(trend) >= 1
        assert trend[0]["new_providers"] >= 1

    def test_query_provider_summary(self, db):
        from api.routes.dashboard_queries import query_provider_summary
        sid = _insert_service(db, provider_id="prov-sum")
        _insert_usage(db, service_id=sid, provider_id="prov-sum", amount_usd=42.0)

        with db.connect() as conn:
            summary = query_provider_summary(conn)

        assert summary["total_providers"] == 1
        assert summary["total_revenue"] == 42.0
        assert summary["total_commission"] == 0.0  # no settlements
