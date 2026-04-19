"""Tests for Provider Milestone Tracker."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from api.main import app
from marketplace.auth import APIKeyManager
from marketplace.db import Database
from marketplace.milestones import MILESTONES, MilestoneTracker


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "milestones_test.db")


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


@pytest.fixture
def tracker(db):
    return MilestoneTracker(db)


@pytest.fixture
def provider_creds(auth):
    key_id, secret = auth.create_key(owner_id="prov-ms", role="provider")
    return key_id, secret


@pytest.fixture
def admin_creds(auth):
    key_id, secret = auth.create_key(owner_id="admin-ms", role="admin")
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


def _insert_usage(db, provider_id: str, amount: float, count: int, status_code: int = 200):
    """Insert usage records contributing to provider earnings."""
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        for _ in range(count):
            conn.execute(
                """INSERT INTO usage_records
                   (id, service_id, buyer_id, provider_id, amount_usd,
                    status_code, latency_ms, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    "svc-test",
                    "buyer-test",
                    provider_id,
                    amount,
                    status_code,
                    50,
                    now,
                ),
            )


class TestMilestoneTracker:
    """Unit tests for MilestoneTracker."""

    def test_no_earnings_no_milestones(self, tracker):
        result = tracker.check_and_award("nobody-provider")
        assert result == []

    def test_cumulative_earnings_zero(self, tracker):
        earnings = tracker.get_cumulative_earnings("nobody-provider")
        assert earnings == Decimal("0")

    def test_active_seller_milestone(self, tracker, db):
        provider = "prov-active"
        _insert_usage(db, provider, 10.0, 5)  # $50

        awarded = tracker.check_and_award(provider)
        assert len(awarded) == 1
        assert awarded[0]["milestone_type"] == "active_seller"
        assert awarded[0]["label"] == "Active Seller"

    def test_multiple_milestones_at_once(self, tracker, db):
        provider = "prov-multi"
        _insert_usage(db, provider, 100.0, 6)  # $600

        awarded = tracker.check_and_award(provider)
        assert len(awarded) == 3
        types = {m["milestone_type"] for m in awarded}
        assert types == {"active_seller", "tier_upgrade", "cashback"}

    def test_no_duplicate_awards(self, tracker, db):
        provider = "prov-nodup"
        _insert_usage(db, provider, 10.0, 5)  # $50

        first = tracker.check_and_award(provider)
        assert len(first) == 1

        second = tracker.check_and_award(provider)
        assert len(second) == 0

    def test_below_threshold_no_award(self, tracker, db):
        provider = "prov-below"
        _insert_usage(db, provider, 5.0, 5)  # $25

        awarded = tracker.check_and_award(provider)
        assert awarded == []

    def test_has_milestone(self, tracker, db):
        provider = "prov-has"
        _insert_usage(db, provider, 10.0, 5)
        tracker.check_and_award(provider)

        assert tracker.has_milestone(provider, "active_seller") is True
        assert tracker.has_milestone(provider, "tier_upgrade") is False

    def test_cashback_applied(self, tracker, db):
        provider = "prov-cb"
        _insert_usage(db, provider, 100.0, 6)
        tracker.check_and_award(provider)

        result = tracker.apply_cashback(provider)
        assert result is True

        bal = db.get_balance(provider)
        assert bal >= Decimal("25")

    def test_cashback_not_double_applied(self, tracker, db):
        provider = "prov-cb2"
        _insert_usage(db, provider, 100.0, 6)
        tracker.check_and_award(provider)

        tracker.apply_cashback(provider)
        result = tracker.apply_cashback(provider)
        assert result is False

    def test_cashback_not_eligible(self, tracker, db):
        provider = "prov-nocb"
        _insert_usage(db, provider, 10.0, 5)  # $50 only
        tracker.check_and_award(provider)

        result = tracker.apply_cashback(provider)
        assert result is False

    def test_progress_summary(self, tracker, db):
        provider = "prov-prog"
        _insert_usage(db, provider, 25.0, 5)  # $125
        tracker.check_and_award(provider)

        progress = tracker.get_progress(provider)
        assert progress["provider_id"] == provider
        assert float(progress["cumulative_earnings_usd"]) == 125.0
        assert progress["total_milestones"] == 3
        assert progress["total_achieved"] == 1
        assert progress["next_milestone"]["milestone_type"] == "tier_upgrade"
        assert float(progress["next_milestone"]["remaining_usd"]) == 75.0

    def test_progress_all_achieved(self, tracker, db):
        provider = "prov-all"
        _insert_usage(db, provider, 200.0, 5)  # $1000
        tracker.check_and_award(provider)

        progress = tracker.get_progress(provider)
        assert progress["total_achieved"] == 3
        assert progress["next_milestone"] is None

    def test_500_error_excluded(self, tracker, db):
        """Usage records with status 500 should NOT count toward earnings."""
        provider = "prov-500"
        _insert_usage(db, provider, 10.0, 10, status_code=500)

        earnings = tracker.get_cumulative_earnings(provider)
        assert earnings == Decimal("0")

    def test_achieved_milestones_list(self, tracker, db):
        provider = "prov-list"
        _insert_usage(db, provider, 50.0, 5)  # $250
        tracker.check_and_award(provider)

        achieved = tracker.get_achieved_milestones(provider)
        assert len(achieved) == 2
        types = {m["milestone_type"] for m in achieved}
        assert types == {"active_seller", "tier_upgrade"}


class TestMilestoneAPI:
    """Integration tests for milestone API endpoints."""

    def test_provider_milestones_endpoint(self, client, provider_creds):
        resp = client.get(
            "/api/v1/provider/milestones",
            headers=_headers(provider_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "milestones" in data
        assert "next_milestone" in data
        assert "cumulative_earnings_usd" in data
        assert len(data["milestones"]) == 3

    def test_provider_milestones_with_earnings(self, client, db, provider_creds):
        _insert_usage(db, "prov-ms", 15.0, 5)  # $75

        resp = client.get(
            "/api/v1/provider/milestones",
            headers=_headers(provider_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert float(data["cumulative_earnings_usd"]) == 75.0
        awarded_types = {m["milestone_type"] for m in data["newly_awarded"]}
        assert "active_seller" in awarded_types

    def test_admin_milestones_endpoint(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/providers/some-provider/milestones",
            headers=_headers(admin_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider_id"] == "some-provider"
        assert "milestones" in data

    def test_milestones_auto_cashback(self, client, db, provider_creds):
        _insert_usage(db, "prov-ms", 200.0, 5)  # $1000

        resp = client.get(
            "/api/v1/provider/milestones",
            headers=_headers(provider_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        achieved = [m for m in data["milestones"] if m["achieved"]]
        assert len(achieved) == 3
        bal = db.get_balance("prov-ms")
        assert bal >= Decimal("25")

    def test_unauthenticated_blocked(self, client):
        resp = client.get("/api/v1/provider/milestones")
        assert resp.status_code == 401
