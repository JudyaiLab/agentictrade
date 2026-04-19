"""Tests for the audit log system (marketplace/audit.py and api/routes/audit.py)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from marketplace.audit import (
    AuditLogger,
    VALID_EVENT_TYPES,
    _GENESIS_HASH,
    _compute_entry_hash,
)
from marketplace.auth import APIKeyManager
from marketplace.db import Database
from api.main import app


# ---------------------------------------------------------------------------
# Unit tests for AuditLogger
# ---------------------------------------------------------------------------


@pytest.fixture
def audit(tmp_path):
    """Create a fresh AuditLogger backed by a temp database."""
    return AuditLogger(tmp_path / "audit_test.db")


class TestAuditLoggerInit:
    def test_creates_table(self, audit):
        """Table should exist after init."""
        events = audit.get_events()
        assert events == []

    def test_idempotent_init(self, tmp_path):
        """Calling init twice on same DB should not fail."""
        db_path = tmp_path / "audit_idem.db"
        a1 = AuditLogger(db_path)
        a2 = AuditLogger(db_path)
        assert a1.get_events() == []
        assert a2.get_events() == []


class TestLogEvent:
    def test_log_valid_event(self, audit):
        row_id = audit.log_event(
            event_type="key_created",
            actor="owner-1",
            target="acf_abc123",
            details="role=buyer",
            ip_address="127.0.0.1",
        )
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_log_event_returns_sequential_ids(self, audit):
        id1 = audit.log_event("auth_success", actor="a")
        id2 = audit.log_event("auth_failure", actor="b")
        assert id2 > id1

    def test_invalid_event_type_raises(self, audit):
        with pytest.raises(ValueError, match="Invalid event_type"):
            audit.log_event("nonexistent_type", actor="x")

    def test_empty_actor_raises(self, audit):
        with pytest.raises(ValueError, match="actor is required"):
            audit.log_event("auth_failure", actor="")

    def test_all_valid_event_types_accepted(self, audit):
        for et in VALID_EVENT_TYPES:
            row_id = audit.log_event(et, actor="test-actor")
            assert row_id > 0

    def test_defaults_for_optional_fields(self, audit):
        audit.log_event("auth_success", actor="actor-1")
        events = audit.get_events()
        assert len(events) == 1
        evt = events[0]
        assert evt["target"] == ""
        assert evt["details"] == ""
        assert evt["ip_address"] == ""

    def test_timestamp_is_iso_format(self, audit):
        audit.log_event("auth_success", actor="actor-1")
        events = audit.get_events()
        ts = events[0]["timestamp"]
        # Should parse without error
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None


class TestGetEvents:
    def test_returns_all_events(self, audit):
        audit.log_event("auth_success", actor="a")
        audit.log_event("auth_failure", actor="b")
        audit.log_event("key_created", actor="c")

        events = audit.get_events()
        assert len(events) == 3

    def test_filter_by_event_type(self, audit):
        audit.log_event("auth_success", actor="a")
        audit.log_event("auth_failure", actor="b")
        audit.log_event("auth_failure", actor="c")

        events = audit.get_events(event_type="auth_failure")
        assert len(events) == 2
        assert all(e["event_type"] == "auth_failure" for e in events)

    def test_filter_by_actor(self, audit):
        audit.log_event("auth_success", actor="alice")
        audit.log_event("auth_success", actor="bob")
        audit.log_event("auth_failure", actor="alice")

        events = audit.get_events(actor="alice")
        assert len(events) == 2
        assert all(e["actor"] == "alice" for e in events)

    def test_filter_combined(self, audit):
        audit.log_event("auth_success", actor="alice")
        audit.log_event("auth_failure", actor="alice")
        audit.log_event("auth_failure", actor="bob")

        events = audit.get_events(event_type="auth_failure", actor="alice")
        assert len(events) == 1
        assert events[0]["actor"] == "alice"
        assert events[0]["event_type"] == "auth_failure"

    def test_limit_and_offset(self, audit):
        for i in range(10):
            audit.log_event("auth_success", actor=f"actor-{i}")

        page1 = audit.get_events(limit=3, offset=0)
        page2 = audit.get_events(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        # No overlap between pages
        ids1 = {e["id"] for e in page1}
        ids2 = {e["id"] for e in page2}
        assert ids1.isdisjoint(ids2)

    def test_ordered_by_timestamp_desc(self, audit):
        audit.log_event("auth_success", actor="first")
        audit.log_event("auth_success", actor="second")

        events = audit.get_events()
        assert events[0]["actor"] == "second"
        assert events[1]["actor"] == "first"


class TestGetRecent:
    def test_returns_recent_events(self, audit):
        audit.log_event("auth_success", actor="recent")
        events = audit.get_recent(hours=1)
        assert len(events) == 1
        assert events[0]["actor"] == "recent"

    def test_empty_when_no_recent(self, audit):
        events = audit.get_recent(hours=1)
        assert events == []


class TestGetSummary:
    def test_counts_by_type(self, audit):
        audit.log_event("auth_success", actor="a")
        audit.log_event("auth_success", actor="b")
        audit.log_event("auth_failure", actor="c")
        audit.log_event("key_created", actor="d")

        summary = audit.get_summary(hours=1)
        assert summary["auth_success"] == 2
        assert summary["auth_failure"] == 1
        assert summary["key_created"] == 1

    def test_empty_summary(self, audit):
        summary = audit.get_summary(hours=1)
        assert summary == {}


# ---------------------------------------------------------------------------
# API route tests
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "api_audit_test.db")


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


@pytest.fixture
def admin_creds(auth):
    key_id, secret = auth.create_key(owner_id="admin-1", role="admin")
    return key_id, secret


@pytest.fixture
def buyer_creds(auth):
    key_id, secret = auth.create_key(owner_id="buyer-1", role="buyer")
    return key_id, secret


@pytest.fixture
def api_audit(tmp_path):
    """AuditLogger instance for API tests."""
    return AuditLogger(tmp_path / "api_audit.db")


@pytest.fixture
def client(db, auth, api_audit):
    with TestClient(app, raise_server_exceptions=False) as c:
        app.state.db = db
        app.state.auth = auth
        app.state.audit = api_audit
        yield c


def _admin_headers(creds):
    key_id, secret = creds
    return {"Authorization": f"Bearer {key_id}:{secret}"}


class TestAuditRouteAuth:
    """Audit endpoints require admin auth."""

    def test_audit_list_requires_auth(self, client):
        resp = client.get("/api/v1/admin/audit")
        assert resp.status_code == 401

    def test_audit_list_rejects_buyer(self, client, buyer_creds):
        resp = client.get(
            "/api/v1/admin/audit",
            headers=_admin_headers(buyer_creds),
        )
        assert resp.status_code == 403

    def test_audit_summary_requires_auth(self, client):
        resp = client.get("/api/v1/admin/audit/summary")
        assert resp.status_code == 401

    def test_audit_summary_rejects_buyer(self, client, buyer_creds):
        resp = client.get(
            "/api/v1/admin/audit/summary",
            headers=_admin_headers(buyer_creds),
        )
        assert resp.status_code == 403


class TestAuditListEndpoint:
    def test_empty_returns_empty(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/audit",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []
        assert data["count"] == 0

    def test_returns_logged_events(self, client, admin_creds, api_audit):
        api_audit.log_event("auth_success", actor="alice", target="api")
        api_audit.log_event("auth_failure", actor="bob", ip_address="10.0.0.1")

        resp = client.get(
            "/api/v1/admin/audit",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert data["count"] == 2

    def test_filter_by_event_type(self, client, admin_creds, api_audit):
        api_audit.log_event("auth_success", actor="a")
        api_audit.log_event("auth_failure", actor="b")

        resp = client.get(
            "/api/v1/admin/audit?event_type=auth_failure",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert data["count"] == 1
        assert data["events"][0]["event_type"] == "auth_failure"

    def test_filter_by_actor(self, client, admin_creds, api_audit):
        api_audit.log_event("auth_success", actor="alice")
        api_audit.log_event("auth_success", actor="bob")

        resp = client.get(
            "/api/v1/admin/audit?actor=alice",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert data["count"] == 1
        assert data["events"][0]["actor"] == "alice"

    def test_pagination(self, client, admin_creds, api_audit):
        for i in range(5):
            api_audit.log_event("auth_success", actor=f"actor-{i}")

        resp = client.get(
            "/api/v1/admin/audit?limit=2&offset=0",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert data["count"] == 2

        resp2 = client.get(
            "/api/v1/admin/audit?limit=2&offset=2",
            headers=_admin_headers(admin_creds),
        )
        data2 = resp2.json()
        assert data2["count"] == 2


class TestAuditSummaryEndpoint:
    def test_empty_summary(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/audit/summary",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hours"] == 24
        assert data["summary"] == {}

    def test_summary_counts(self, client, admin_creds, api_audit):
        api_audit.log_event("auth_success", actor="a")
        api_audit.log_event("auth_success", actor="b")
        api_audit.log_event("key_created", actor="c")

        resp = client.get(
            "/api/v1/admin/audit/summary",
            headers=_admin_headers(admin_creds),
        )
        data = resp.json()
        assert data["summary"]["auth_success"] == 2
        assert data["summary"]["key_created"] == 1

    def test_summary_custom_hours(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/audit/summary?hours=48",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        assert resp.json()["hours"] == 48


# ---------------------------------------------------------------------------
# Integration: audit logging in auth routes
# ---------------------------------------------------------------------------


class TestAuthAuditIntegration:
    """Verify that key creation and validation failures are audit-logged."""

    @pytest.fixture
    def integrated_audit(self, tmp_path):
        """Shared audit logger for integration tests."""
        return AuditLogger(tmp_path / "integrated_audit.db")

    @pytest.fixture
    def integrated_client(self, db, auth, integrated_audit):
        with TestClient(app, raise_server_exceptions=False) as c:
            app.state.db = db
            app.state.auth = auth
            app.state.audit = integrated_audit
            yield c

    def test_key_creation_logs_audit(self, integrated_client, integrated_audit):
        resp = integrated_client.post("/api/v1/keys", json={
            "owner_id": "new-buyer",
            "role": "buyer",
        })
        assert resp.status_code == 201
        key_id = resp.json()["key_id"]

        events = integrated_audit.get_events(event_type="key_created")
        assert len(events) == 1
        evt = events[0]
        assert evt["actor"] == "new-buyer"
        assert evt["target"] == key_id
        assert "role=buyer" in evt["details"]

    def test_validation_failure_logs_audit(self, integrated_client, integrated_audit):
        resp = integrated_client.post("/api/v1/keys/validate", json={
            "key_id": "acf_nonexistent",
            "secret": "wrong",
        })
        assert resp.status_code == 401

        events = integrated_audit.get_events(event_type="auth_failure")
        assert len(events) == 1
        evt = events[0]
        assert evt["actor"] == "acf_nonexistent"
        assert evt["target"] == "keys/validate"


class TestEscrowAuditEventTypes:
    """Verify that escrow-related event types are accepted."""

    def test_escrow_created_accepted(self, audit):
        row_id = audit.log_event(
            "escrow_created", actor="buyer-1", target="hold-123",
            details="amount=10.0 USDC, provider=prov-1",
        )
        assert row_id > 0

    def test_escrow_released_accepted(self, audit):
        row_id = audit.log_event(
            "escrow_released", actor="admin", target="hold-123",
            details="amount=10.0 USDC",
        )
        assert row_id > 0

    def test_escrow_disputed_accepted(self, audit):
        row_id = audit.log_event(
            "escrow_disputed", actor="buyer-1", target="hold-123",
            details="category=quality_issue",
        )
        assert row_id > 0

    def test_escrow_resolved_accepted(self, audit):
        row_id = audit.log_event(
            "escrow_resolved", actor="admin", target="hold-123",
            details="outcome=partial_refund, refund_amount=5.0",
        )
        assert row_id > 0

    def test_escrow_events_queryable(self, audit):
        audit.log_event("escrow_created", actor="buyer-1", target="hold-1")
        audit.log_event("escrow_released", actor="admin", target="hold-1")
        audit.log_event("auth_success", actor="other")

        created = audit.get_events(event_type="escrow_created")
        assert len(created) == 1
        released = audit.get_events(event_type="escrow_released")
        assert len(released) == 1


# ---------------------------------------------------------------------------
# Hash chain tamper detection (R15-M1)
# ---------------------------------------------------------------------------


class TestHashChain:
    """Verify the audit log hash chain for tamper detection."""

    def test_single_entry_chain_valid(self, audit):
        audit.log_event("auth_success", actor="alice")
        valid, errors = audit.verify_chain()
        assert valid is True
        assert errors == []

    def test_multiple_entries_chain_valid(self, audit):
        for i in range(10):
            audit.log_event("auth_success", actor=f"actor-{i}")
        valid, errors = audit.verify_chain()
        assert valid is True
        assert errors == []

    def test_first_entry_uses_genesis_hash(self, audit):
        audit.log_event("auth_success", actor="first")
        events = audit.get_events()
        assert events[0]["prev_hash"] == _GENESIS_HASH

    def test_second_entry_chains_from_first(self, audit):
        audit.log_event("auth_success", actor="first")
        audit.log_event("auth_failure", actor="second")
        events = audit.get_events(limit=10)
        # Events are ordered DESC, so events[0] is the latest
        latest = events[0]
        earliest = events[1]
        assert latest["prev_hash"] != _GENESIS_HASH
        assert earliest["prev_hash"] == _GENESIS_HASH
        # The latest prev_hash should be the computed hash of the earliest entry
        expected = _compute_entry_hash(
            earliest["prev_hash"],
            earliest["event_type"],
            earliest["actor"],
            earliest["target"],
            earliest["details"],
            earliest["ip_address"],
            earliest["timestamp"],
        )
        assert latest["prev_hash"] == expected

    def test_tampered_entry_detected(self, audit):
        """If an entry's prev_hash is modified in the DB, verify_chain detects it."""
        audit.log_event("auth_success", actor="a")
        audit.log_event("auth_failure", actor="b")
        audit.log_event("key_created", actor="c")

        # Tamper with the second entry's prev_hash directly in the DB
        import sqlite3
        conn = sqlite3.connect(str(audit.db_path))
        conn.execute(
            "UPDATE audit_log SET prev_hash = 'tampered_value' WHERE id = 2"
        )
        conn.commit()
        conn.close()

        valid, errors = audit.verify_chain()
        assert valid is False
        assert len(errors) >= 1
        tampered_ids = {e["id"] for e in errors}
        assert 2 in tampered_ids

    def test_empty_log_is_valid(self, audit):
        valid, errors = audit.verify_chain()
        assert valid is True
        assert errors == []

    def test_prev_hash_is_deterministic(self, audit):
        """Same data should produce same hash."""
        h1 = _compute_entry_hash(
            _GENESIS_HASH, "auth_success", "alice", "", "", "", "2026-01-01T00:00:00+00:00"
        )
        h2 = _compute_entry_hash(
            _GENESIS_HASH, "auth_success", "alice", "", "", "", "2026-01-01T00:00:00+00:00"
        )
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex digest

    def test_different_data_produces_different_hash(self, audit):
        h1 = _compute_entry_hash(
            _GENESIS_HASH, "auth_success", "alice", "", "", "", "2026-01-01T00:00:00+00:00"
        )
        h2 = _compute_entry_hash(
            _GENESIS_HASH, "auth_failure", "alice", "", "", "", "2026-01-01T00:00:00+00:00"
        )
        assert h1 != h2


# ---------------------------------------------------------------------------
# Time-range filtering (R15-L4)
# ---------------------------------------------------------------------------


class TestTimeRangeFiltering:
    """Test the since/until parameters on get_events and the API endpoint."""

    def test_get_events_since(self, audit):
        audit.log_event("auth_success", actor="a")
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        events = audit.get_events(since=future)
        assert events == []  # No events in the future

    def test_get_events_until(self, audit):
        audit.log_event("auth_success", actor="a")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        events = audit.get_events(until=past)
        assert events == []  # Event is after the 'until' cutoff

    def test_get_events_since_includes_recent(self, audit):
        audit.log_event("auth_success", actor="a")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        events = audit.get_events(since=past)
        assert len(events) == 1

    def test_api_default_time_range(self, client, admin_creds, api_audit):
        """Endpoint should apply default 30-day window when no range given."""
        api_audit.log_event("auth_success", actor="recent")
        resp = client.get(
            "/api/v1/admin/audit",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        # Recent event should be returned within default 30-day window
        assert data["count"] == 1

    def test_api_explicit_since(self, client, admin_creds, api_audit):
        api_audit.log_event("auth_success", actor="recent")
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        resp = client.get(
            f"/api/v1/admin/audit?since={future}",
            headers=_admin_headers(admin_creds),
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 0
