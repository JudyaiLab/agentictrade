"""Tests for the multi-page admin dashboard routes."""
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
    return Database(tmp_path / "dash_test.db")


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
                    category="test"):
    sid = service_id or f"svc-{uuid.uuid4().hex[:8]}"
    now = _now_iso()
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


def _insert_agent(db, *, agent_id=None, owner_id="owner-1",
                  display_name="Test Agent", status="active",
                  reputation_score=0.0, wallet_address=None):
    aid = agent_id or f"agent-{uuid.uuid4().hex[:8]}"
    now = _now_iso()
    db.insert_agent({
        "agent_id": aid, "display_name": display_name,
        "owner_id": owner_id, "identity_type": "api_key_only",
        "capabilities": [], "wallet_address": wallet_address,
        "verified": False, "reputation_score": reputation_score,
        "status": status, "created_at": now, "updated_at": now,
        "metadata": {},
    })
    return aid


def _insert_settlement(db, *, provider_id="prov-1", total_amount=1.0):
    sid = f"settle-{uuid.uuid4().hex[:8]}"
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO settlements
               (id, provider_id, period_start, period_end,
                total_amount, platform_fee, net_amount, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, provider_id, "2026-01-01", "2026-02-01",
             total_amount, total_amount * 0.1, total_amount * 0.9, "pending"),
        )
    return sid


# ---------------------------------------------------------------------------
# Auth tests (all dashboard pages require admin)
# ---------------------------------------------------------------------------

class TestDashboardAuth:
    """All /dashboard/ routes must reject unauthenticated or non-admin users."""

    @pytest.mark.parametrize("path", [
        "/dashboard/",
        "/dashboard/services",
        "/dashboard/agents",
        "/dashboard/transactions",
        "/dashboard/quality",
    ])
    def test_rejects_missing_key(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 401

    @pytest.mark.parametrize("path", [
        "/dashboard/",
        "/dashboard/services",
        "/dashboard/agents",
        "/dashboard/transactions",
        "/dashboard/quality",
    ])
    def test_rejects_buyer_key(self, client, buyer_creds, path):
        resp = client.get(f"{path}?key={buyer_creds}")
        assert resp.status_code == 403

    def test_rejects_invalid_key_format(self, client):
        resp = client.get("/dashboard/?key=invalid-no-colon")
        assert resp.status_code == 401

    @pytest.mark.parametrize("path", [
        "/dashboard/",
        "/dashboard/services",
        "/dashboard/agents",
        "/dashboard/transactions",
        "/dashboard/quality",
    ])
    def test_accepts_admin_key(self, client, admin_creds, path):
        resp = client.get(f"{path}?key={admin_creds}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# GET /dashboard/ (Overview)
# ---------------------------------------------------------------------------

class TestDashboardOverview:
    def test_empty_db_renders(self, client, admin_creds):
        resp = client.get(f"/dashboard/?key={admin_creds}")
        assert resp.status_code == 200
        body = resp.text
        assert "Services" in body
        assert "Agents" in body
        assert "Transactions" in body

    def test_shows_stats_with_data(self, client, db, admin_creds):
        _insert_service(db)
        _insert_service(db)
        _insert_agent(db)
        _insert_usage(db, amount_usd=0.50, payment_method="x402")
        _insert_usage(db, amount_usd=0.30, payment_method="stripe")

        resp = client.get(f"/dashboard/?key={admin_creds}")
        body = resp.text
        assert resp.status_code == 200
        # Stats cards should contain service count and transaction count
        assert "Services" in body
        assert "Transactions" in body
        # Revenue from the two transactions: $0.80
        assert "$0.80" in body

    def test_payment_method_breakdown(self, client, db, admin_creds):
        _insert_usage(db, payment_method="x402", amount_usd=1.00)
        _insert_usage(db, payment_method="stripe", amount_usd=2.00)

        resp = client.get(f"/dashboard/?key={admin_creds}")
        body = resp.text
        assert "x402" in body
        assert "stripe" in body

    def test_recent_transactions_limited_to_20(self, client, db, admin_creds):
        for i in range(25):
            _insert_usage(db, amount_usd=0.01)

        resp = client.get(f"/dashboard/?key={admin_creds}")
        body = resp.text
        assert resp.status_code == 200
        # Count table rows (each tx has a <tr> in tbody)
        # The overview shows at most 20 recent transactions
        assert body.count("usage-") <= 20

    def test_revenue_summary(self, client, db, admin_creds):
        _insert_usage(db, amount_usd=10.50)
        _insert_settlement(db, total_amount=5.0)

        resp = client.get(f"/dashboard/?key={admin_creds}")
        body = resp.text
        assert "$10.50" in body


# ---------------------------------------------------------------------------
# GET /dashboard/services
# ---------------------------------------------------------------------------

class TestDashboardServices:
    def test_empty_shows_message(self, client, admin_creds):
        resp = client.get(f"/dashboard/services?key={admin_creds}")
        assert resp.status_code == 200
        assert "No services registered" in resp.text

    def test_lists_services(self, client, db, admin_creds):
        _insert_service(db, name="Alpha API", category="ml")
        _insert_service(db, name="Beta API", category="data")

        resp = client.get(f"/dashboard/services?key={admin_creds}")
        body = resp.text
        assert "Alpha API" in body
        assert "Beta API" in body
        assert "ml" in body

    def test_shows_call_count(self, client, db, admin_creds):
        sid = _insert_service(db, service_id="svc-count-test", name="Counted")
        for _ in range(5):
            _insert_usage(db, service_id=sid)

        resp = client.get(f"/dashboard/services?key={admin_creds}")
        body = resp.text
        assert "5" in body

    def test_shows_inactive_services(self, client, db, admin_creds):
        _insert_service(db, name="Active One", status="active")
        _insert_service(db, name="Paused One", status="paused")

        resp = client.get(f"/dashboard/services?key={admin_creds}")
        body = resp.text
        assert "Active One" in body
        assert "Paused One" in body

    def test_shows_avg_latency(self, client, db, admin_creds):
        sid = _insert_service(db, service_id="svc-lat", name="Latency Svc")
        _insert_usage(db, service_id=sid, latency_ms=100)
        _insert_usage(db, service_id=sid, latency_ms=200)

        resp = client.get(f"/dashboard/services?key={admin_creds}")
        body = resp.text
        # avg is 150.0
        assert "150.0" in body


# ---------------------------------------------------------------------------
# GET /dashboard/agents
# ---------------------------------------------------------------------------

class TestDashboardAgents:
    def test_empty_shows_message(self, client, admin_creds):
        resp = client.get(f"/dashboard/agents?key={admin_creds}")
        assert resp.status_code == 200
        assert "No agents registered" in resp.text

    def test_lists_agents(self, client, db, admin_creds):
        _insert_agent(db, display_name="Agent Alpha", reputation_score=85.0)
        _insert_agent(db, display_name="Agent Beta", reputation_score=42.0)

        resp = client.get(f"/dashboard/agents?key={admin_creds}")
        body = resp.text
        assert "Agent Alpha" in body
        assert "Agent Beta" in body
        assert "85.0" in body

    def test_shows_provided_services(self, client, db, admin_creds):
        _insert_agent(db, owner_id="prov-x", display_name="Provider X")
        _insert_service(db, provider_id="prov-x", name="X Service")

        resp = client.get(f"/dashboard/agents?key={admin_creds}")
        body = resp.text
        assert "1 service" in body

    def test_shows_consumed_services(self, client, db, admin_creds):
        _insert_agent(db, owner_id="buyer-x", display_name="Buyer X")
        sid = _insert_service(db, service_id="svc-consumed")
        _insert_usage(db, buyer_id="buyer-x", service_id=sid)

        resp = client.get(f"/dashboard/agents?key={admin_creds}")
        body = resp.text
        assert "1 service" in body

    def test_excludes_inactive_agents(self, client, db, admin_creds):
        _insert_agent(db, display_name="Active Agent", status="active")
        _insert_agent(db, display_name="Dead Agent", status="deactivated")

        resp = client.get(f"/dashboard/agents?key={admin_creds}")
        body = resp.text
        assert "Active Agent" in body
        assert "Dead Agent" not in body

    def test_shows_wallet_address(self, client, db, admin_creds):
        _insert_agent(
            db, display_name="Wallet Agent",
            wallet_address="0xAbCdEf1234567890AbCdEf1234567890AbCdEf12",
        )

        resp = client.get(f"/dashboard/agents?key={admin_creds}")
        body = resp.text
        assert "0xAbCdEf12" in body


# ---------------------------------------------------------------------------
# GET /dashboard/transactions
# ---------------------------------------------------------------------------

class TestDashboardTransactions:
    def test_empty_shows_message(self, client, admin_creds):
        resp = client.get(f"/dashboard/transactions?key={admin_creds}")
        assert resp.status_code == 200
        assert "No transactions found" in resp.text

    def test_lists_transactions(self, client, db, admin_creds):
        _insert_usage(db, amount_usd=0.05, payment_method="x402")
        _insert_usage(db, amount_usd=0.10, payment_method="stripe")

        resp = client.get(f"/dashboard/transactions?key={admin_creds}")
        body = resp.text
        assert "x402" in body
        assert "stripe" in body

    def test_filter_by_service_id(self, client, db, admin_creds):
        _insert_usage(db, service_id="svc-a", amount_usd=0.01)
        _insert_usage(db, service_id="svc-b", amount_usd=0.02)

        resp = client.get(
            f"/dashboard/transactions?key={admin_creds}&service_id=svc-a"
        )
        body = resp.text
        assert "svc-a" in body
        # total count should be 1
        assert "1 total transaction" in body

    def test_filter_by_buyer_id(self, client, db, admin_creds):
        _insert_usage(db, buyer_id="b-alpha")
        _insert_usage(db, buyer_id="b-beta")

        resp = client.get(
            f"/dashboard/transactions?key={admin_creds}&buyer_id=b-alpha"
        )
        body = resp.text
        assert "1 total transaction" in body

    def test_filter_by_payment_method(self, client, db, admin_creds):
        _insert_usage(db, payment_method="x402")
        _insert_usage(db, payment_method="x402")
        _insert_usage(db, payment_method="stripe")

        resp = client.get(
            f"/dashboard/transactions?key={admin_creds}&payment_method=x402"
        )
        body = resp.text
        assert "2 total transaction" in body

    def test_filter_by_date_range(self, client, db, admin_creds):
        old_ts = "2025-01-15T10:00:00+00:00"
        new_ts = "2026-03-15T10:00:00+00:00"
        _insert_usage(db, timestamp=old_ts)
        _insert_usage(db, timestamp=new_ts)

        resp = client.get(
            f"/dashboard/transactions?key={admin_creds}"
            "&date_from=2026-01-01&date_to=2026-12-31"
        )
        body = resp.text
        assert "1 total transaction" in body

    def test_pagination(self, client, db, admin_creds):
        # Insert 30 records (per_page is 25)
        for i in range(30):
            _insert_usage(db, amount_usd=0.01 * i)

        resp = client.get(f"/dashboard/transactions?key={admin_creds}")
        body = resp.text
        assert "Page 1 of 2" in body
        assert "Next" in body

        # Page 2
        resp2 = client.get(f"/dashboard/transactions?key={admin_creds}&page=2")
        body2 = resp2.text
        assert "Page 2 of 2" in body2
        assert "Prev" in body2

    def test_combined_filters(self, client, db, admin_creds):
        ts = "2026-03-15T10:00:00+00:00"
        _insert_usage(
            db, service_id="svc-combo", buyer_id="b-combo",
            payment_method="x402", timestamp=ts,
        )
        _insert_usage(
            db, service_id="svc-combo", buyer_id="b-other",
            payment_method="stripe", timestamp=ts,
        )
        _insert_usage(
            db, service_id="svc-other", buyer_id="b-combo",
            payment_method="x402", timestamp=ts,
        )

        resp = client.get(
            f"/dashboard/transactions?key={admin_creds}"
            "&service_id=svc-combo&buyer_id=b-combo&payment_method=x402"
        )
        body = resp.text
        assert "1 total transaction" in body

    def test_shows_total_count(self, client, db, admin_creds):
        for _ in range(3):
            _insert_usage(db)

        resp = client.get(f"/dashboard/transactions?key={admin_creds}")
        body = resp.text
        assert "3 total transaction" in body


# ---------------------------------------------------------------------------
# GET /dashboard/quality
# ---------------------------------------------------------------------------

class TestDashboardQuality:
    def test_empty_shows_message(self, client, admin_creds):
        resp = client.get(f"/dashboard/quality?key={admin_creds}")
        assert resp.status_code == 200
        assert "No active services to monitor" in resp.text

    def test_shows_service_metrics(self, client, db, admin_creds):
        sid = _insert_service(db, service_id="svc-q1", name="Quality Svc")
        _insert_usage(db, service_id=sid, status_code=200, latency_ms=100)
        _insert_usage(db, service_id=sid, status_code=200, latency_ms=200)
        _insert_usage(db, service_id=sid, status_code=500, latency_ms=300)

        resp = client.get(f"/dashboard/quality?key={admin_creds}")
        body = resp.text
        assert "Quality Svc" in body
        # 1 error out of 3 = 33.3%
        assert "33.3" in body

    def test_sla_compliant(self, client, db, admin_creds):
        sid = _insert_service(db, service_id="svc-good", name="Good Svc")
        for _ in range(10):
            _insert_usage(db, service_id=sid, status_code=200, latency_ms=50)

        resp = client.get(f"/dashboard/quality?key={admin_creds}")
        body = resp.text
        assert "Compliant" in body

    def test_sla_violation(self, client, db, admin_creds):
        sid = _insert_service(db, service_id="svc-bad", name="Bad Svc")
        # All 500 errors
        for _ in range(10):
            _insert_usage(db, service_id=sid, status_code=500, latency_ms=50)

        resp = client.get(f"/dashboard/quality?key={admin_creds}")
        body = resp.text
        assert "Violation" in body

    def test_summary_cards(self, client, db, admin_creds):
        sid = _insert_service(db, service_id="svc-sum", name="Summary Svc")
        _insert_usage(db, service_id=sid, status_code=200, latency_ms=100)
        _insert_usage(db, service_id=sid, status_code=200, latency_ms=200)

        resp = client.get(f"/dashboard/quality?key={admin_creds}")
        body = resp.text
        # Should show P50, P95, Avg SLA, Avg Error Rate
        assert "P50 Latency" in body
        assert "P95 Latency" in body
        assert "Avg SLA Compliance" in body
        assert "Avg Error Rate" in body

    def test_no_data_services(self, client, db, admin_creds):
        _insert_service(db, service_id="svc-nodata", name="No Data Svc")

        resp = client.get(f"/dashboard/quality?key={admin_creds}")
        body = resp.text
        assert "No Data" in body

    def test_success_rate_100_percent(self, client, db, admin_creds):
        sid = _insert_service(db, service_id="svc-perfect", name="Perfect Svc")
        for _ in range(5):
            _insert_usage(db, service_id=sid, status_code=200, latency_ms=30)

        resp = client.get(f"/dashboard/quality?key={admin_creds}")
        body = resp.text
        assert "100.0%" in body


# ---------------------------------------------------------------------------
# GET /admin/dashboard (Legacy route)
# ---------------------------------------------------------------------------

class TestLegacyDashboard:
    def test_legacy_route_still_works(self, client, admin_creds):
        resp = client.get(f"/admin/dashboard?key={admin_creds}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Agent Commerce Framework" in resp.text

    def test_legacy_route_requires_auth(self, client):
        resp = client.get("/admin/dashboard?key=bad")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------

class TestHelperFunctions:
    def test_fmt_number_integers(self):
        from api.routes.dashboard_queries import fmt_number
        assert fmt_number(0) == "0"
        assert fmt_number(1234) == "1,234"
        assert fmt_number(1000000) == "1,000,000"

    def test_fmt_number_decimals(self):
        from api.routes.dashboard_queries import fmt_number
        assert fmt_number(1234.56, 2) == "1,234.56"
        assert fmt_number(0.0, 2) == "0.00"

    def test_safe_pct_zero_denominator(self):
        from api.routes.dashboard_queries import safe_pct
        assert safe_pct(10, 0) == 0.0

    def test_safe_pct_normal(self):
        from api.routes.dashboard_queries import safe_pct
        assert safe_pct(1, 4) == 25.0

    def test_clamp_page(self):
        from api.routes.dashboard_queries import clamp_page
        assert clamp_page(0, 5) == 1
        assert clamp_page(3, 5) == 3
        assert clamp_page(10, 5) == 5
        assert clamp_page(1, 0) == 1

    def test_build_page_range(self):
        from api.routes.dashboard_queries import build_page_range
        assert build_page_range(1, 3) == [1, 2, 3]
        assert build_page_range(5, 10) == [3, 4, 5, 6, 7]
        assert build_page_range(1, 1) == [1]

    def test_percentile(self):
        from api.routes.dashboard_queries import percentile
        assert percentile([], 50) == 0.0
        assert percentile([10], 50) == 10.0
        assert percentile([10, 20, 30, 40, 50], 50) == 30.0
        p95 = percentile(list(range(1, 101)), 95)
        assert 94.0 <= p95 <= 96.0

    def test_sla_status(self):
        from api.routes.dashboard_queries import sla_status
        assert sla_status(0.0, 100.0, 10) == "compliant"
        assert sla_status(10.0, 100.0, 10) == "violation"
        assert sla_status(0.0, 6000.0, 10) == "violation"
        assert sla_status(3.5, 100.0, 10) == "warning"
        assert sla_status(0.0, 4100.0, 10) == "warning"
        assert sla_status(0.0, 0.0, 0) == "no_data"
