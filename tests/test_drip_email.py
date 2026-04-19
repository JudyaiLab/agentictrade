"""
Tests for provider drip email system — scheduling, processing, and API endpoints.

Covers:
  - DripEmailScheduler unit tests (welcome sequence, first sale, weekly digest)
  - Duplicate prevention
  - Pending processing with mock sender
  - Dry-run mode
  - API endpoints (drip-process, drip-trigger, drip-status)
  - Template loading
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from marketplace.db import Database
from marketplace.drip_email import (
    DRIP_FIRST_SALE,
    DRIP_ONBOARDING,
    DRIP_WEEKLY_DIGEST,
    DRIP_WELCOME,
    DripEmailScheduler,
    TemplateNotFoundError,
    _get_subject,
    _load_template,
    validate_templates,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "drip_email_test.db")


@pytest.fixture
def scheduler(db):
    return DripEmailScheduler(db)


@pytest.fixture
def client(db):
    with TestClient(app, raise_server_exceptions=False) as c:
        app.state.db = db
        yield c


def _admin_headers():
    return {"x-admin-key": "test-admin-secret"}


# ---------------------------------------------------------------------------
# DripEmailScheduler — Welcome Sequence
# ---------------------------------------------------------------------------

class TestWelcomeSequence:
    def test_schedule_welcome_creates_two_drips(self, scheduler):
        result = scheduler.schedule_welcome_sequence("prov-1", "prov@example.com")
        assert len(result) == 2
        types = {r["drip_type"] for r in result}
        assert types == {DRIP_WELCOME, DRIP_ONBOARDING}

    def test_welcome_scheduled_immediately(self, scheduler):
        result = scheduler.schedule_welcome_sequence("prov-2", "prov2@example.com")
        welcome = [r for r in result if r["drip_type"] == DRIP_WELCOME][0]
        scheduled = datetime.fromisoformat(welcome["scheduled_at"])
        now = datetime.now(timezone.utc)
        # Welcome should be scheduled within 1 second of now (day 0)
        assert abs((scheduled - now).total_seconds()) < 5

    def test_onboarding_scheduled_for_day_1(self, scheduler):
        result = scheduler.schedule_welcome_sequence("prov-3", "prov3@example.com")
        onboarding = [r for r in result if r["drip_type"] == DRIP_ONBOARDING][0]
        scheduled = datetime.fromisoformat(onboarding["scheduled_at"])
        now = datetime.now(timezone.utc)
        diff = (scheduled - now).total_seconds()
        # Should be ~24 hours in the future (with small tolerance)
        assert 86000 < diff < 87000

    def test_duplicate_welcome_sequence_ignored(self, scheduler):
        first = scheduler.schedule_welcome_sequence("prov-4", "prov4@example.com")
        assert len(first) == 2
        second = scheduler.schedule_welcome_sequence("prov-4", "prov4@example.com")
        assert len(second) == 0

    def test_welcome_sequence_invalid_input(self, scheduler):
        with pytest.raises(ValueError):
            scheduler.schedule_welcome_sequence("", "email@example.com")
        with pytest.raises(ValueError):
            scheduler.schedule_welcome_sequence("prov", "")

    def test_all_drips_start_as_pending(self, scheduler):
        result = scheduler.schedule_welcome_sequence("prov-5", "prov5@example.com")
        for r in result:
            assert r["status"] == "pending"


# ---------------------------------------------------------------------------
# DripEmailScheduler — First Sale Trigger
# ---------------------------------------------------------------------------

class TestFirstSaleTrigger:
    def test_trigger_first_sale_creates_record(self, scheduler):
        record = scheduler.trigger_first_sale("prov-sale-1", "sale@example.com")
        assert record is not None
        assert record["drip_type"] == DRIP_FIRST_SALE
        assert record["status"] == "pending"
        assert record["provider_id"] == "prov-sale-1"

    def test_first_sale_not_duplicated(self, scheduler):
        first = scheduler.trigger_first_sale("prov-sale-2", "sale2@example.com")
        assert first is not None
        second = scheduler.trigger_first_sale("prov-sale-2", "sale2@example.com")
        assert second is None

    def test_first_sale_independent_of_welcome(self, scheduler):
        scheduler.schedule_welcome_sequence("prov-sale-3", "sale3@example.com")
        record = scheduler.trigger_first_sale("prov-sale-3", "sale3@example.com")
        assert record is not None
        drips = scheduler.get_provider_drips("prov-sale-3")
        assert len(drips) == 3

    def test_first_sale_invalid_input(self, scheduler):
        with pytest.raises(ValueError):
            scheduler.trigger_first_sale("", "email@example.com")


# ---------------------------------------------------------------------------
# DripEmailScheduler — Weekly Digest
# ---------------------------------------------------------------------------

class TestWeeklyDigest:
    def test_schedule_weekly_digest(self, scheduler):
        record = scheduler.schedule_weekly_digest(
            provider_id="prov-digest-1",
            email="digest@example.com",
            total_calls=150,
            total_revenue_usd=42.50,
            period="Mar 17-23, 2026",
        )
        assert record is not None
        assert record["drip_type"] == DRIP_WEEKLY_DIGEST
        assert "150" in record["metadata"]
        assert "42.5" in record["metadata"]

    def test_weekly_digest_allows_multiple(self, scheduler):
        """Weekly digests should NOT be deduplicated — one per week is valid."""
        r1 = scheduler.schedule_weekly_digest(
            "prov-digest-2", "d2@example.com", 100, 30.0, "Week 1"
        )
        r2 = scheduler.schedule_weekly_digest(
            "prov-digest-2", "d2@example.com", 200, 60.0, "Week 2"
        )
        assert r1 is not None
        assert r2 is not None
        assert r1["id"] != r2["id"]

    def test_weekly_digest_invalid_input(self, scheduler):
        with pytest.raises(ValueError):
            scheduler.schedule_weekly_digest("", "e@x.com", 0, 0.0, "Week")


# ---------------------------------------------------------------------------
# DripEmailScheduler — Processing
# ---------------------------------------------------------------------------

class TestDripProcessing:
    @patch("marketplace.drip_email._send_email", return_value=True)
    def test_process_pending_sends_due_emails(self, mock_send, scheduler):
        scheduler.schedule_welcome_sequence("prov-proc-1", "proc@example.com")
        result = scheduler.process_pending()
        # Welcome is day 0 (immediately due), onboarding is day 1 (not due yet)
        assert result["sent"] == 1
        assert result["failed"] == 0
        mock_send.assert_called_once()

    def test_process_pending_dry_run(self, scheduler):
        scheduler.schedule_welcome_sequence("prov-dry", "dry@example.com")
        result = scheduler.process_pending(dry_run=True)
        # Welcome is due, should be counted as skipped in dry run
        assert result["skipped"] == 1
        assert result["sent"] == 0
        # Verify email not actually marked as sent
        drips = scheduler.get_provider_drips("prov-dry")
        welcome = [d for d in drips if d["drip_type"] == DRIP_WELCOME][0]
        assert welcome["status"] == "pending"

    def test_process_empty_returns_zeros(self, scheduler):
        result = scheduler.process_pending()
        assert result["sent"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 0

    @patch("marketplace.drip_email._send_email", return_value=False)
    def test_process_failed_send_marks_failed(self, mock_send, scheduler):
        scheduler.schedule_welcome_sequence("prov-fail", "fail@example.com")
        result = scheduler.process_pending()
        assert result["failed"] == 1
        assert result["sent"] == 0
        drips = scheduler.get_provider_drips("prov-fail")
        welcome = [d for d in drips if d["drip_type"] == DRIP_WELCOME][0]
        assert welcome["status"] == "failed"

    @patch("marketplace.drip_email._send_email", return_value=True)
    def test_process_marks_sent_with_timestamp(self, mock_send, scheduler):
        scheduler.schedule_welcome_sequence("prov-ts", "ts@example.com")
        scheduler.process_pending()
        drips = scheduler.get_provider_drips("prov-ts")
        welcome = [d for d in drips if d["drip_type"] == DRIP_WELCOME][0]
        assert welcome["status"] == "sent"
        assert welcome["sent_at"] is not None

    @patch("marketplace.drip_email._send_email", return_value=True)
    def test_process_does_not_resend_already_sent(self, mock_send, scheduler):
        scheduler.schedule_welcome_sequence("prov-resend", "resend@example.com")
        scheduler.process_pending()
        assert mock_send.call_count == 1
        # Process again — should not re-send
        result = scheduler.process_pending()
        assert result["sent"] == 0
        assert mock_send.call_count == 1

    @patch("marketplace.drip_email._send_email", return_value=True)
    def test_process_multiple_providers(self, mock_send, scheduler):
        for i in range(3):
            scheduler.schedule_welcome_sequence(f"prov-multi-{i}", f"m{i}@example.com")
        result = scheduler.process_pending()
        # 3 welcome emails (all day 0), onboarding not due (day 1)
        assert result["sent"] == 3
        assert mock_send.call_count == 3

    @patch("marketplace.drip_email._send_email", return_value=True)
    def test_process_first_sale_immediate(self, mock_send, scheduler):
        scheduler.trigger_first_sale("prov-fs", "fs@example.com")
        result = scheduler.process_pending()
        assert result["sent"] == 1
        drips = scheduler.get_provider_drips("prov-fs")
        assert drips[0]["status"] == "sent"


# ---------------------------------------------------------------------------
# DripEmailScheduler — Query Methods
# ---------------------------------------------------------------------------

class TestDripQueries:
    def test_get_provider_drips_returns_all(self, scheduler):
        scheduler.schedule_welcome_sequence("prov-q1", "q1@example.com")
        scheduler.trigger_first_sale("prov-q1", "q1@example.com")
        drips = scheduler.get_provider_drips("prov-q1")
        assert len(drips) == 3

    def test_get_provider_drips_empty(self, scheduler):
        drips = scheduler.get_provider_drips("nonexistent")
        assert drips == []

    def test_get_pending_count(self, scheduler):
        scheduler.schedule_welcome_sequence("prov-cnt-1", "cnt1@example.com")
        scheduler.schedule_welcome_sequence("prov-cnt-2", "cnt2@example.com")
        count = scheduler.get_pending_count()
        # 2 welcome (due now) + 2 onboarding (due tomorrow) = only 2 pending now
        assert count == 2

    def test_get_pending_count_zero(self, scheduler):
        assert scheduler.get_pending_count() == 0


# ---------------------------------------------------------------------------
# Template Loading
# ---------------------------------------------------------------------------

class TestTemplates:
    def test_load_welcome_template(self):
        html = _load_template(DRIP_WELCOME)
        assert html != ""
        assert "Welcome" in html
        assert "AgenticTrade" in html

    def test_load_onboarding_template(self):
        html = _load_template(DRIP_ONBOARDING)
        assert html != ""
        assert "First API" in html or "List Your" in html

    def test_load_first_sale_template(self):
        html = _load_template(DRIP_FIRST_SALE)
        assert html != ""
        assert "First Sale" in html or "Congratulations" in html

    def test_load_weekly_digest_template(self):
        html = _load_template(
            DRIP_WEEKLY_DIGEST,
            total_calls="42",
            total_revenue_usd="12.50",
            period="Mar 17-23, 2026",
        )
        assert html != ""
        assert "42" in html
        assert "12.50" in html
        assert "Mar 17-23, 2026" in html

    def test_load_nonexistent_template_raises(self):
        with pytest.raises(TemplateNotFoundError):
            _load_template("nonexistent_type")

    def test_validate_templates_all_present(self):
        """All standard drip templates should exist in the repo."""
        missing = validate_templates()
        assert missing == []

    def test_validate_templates_reports_missing(self, tmp_path, monkeypatch):
        """validate_templates should report names when template dir is empty."""
        monkeypatch.setattr(
            "marketplace.drip_email._TEMPLATES_DIR", str(tmp_path)
        )
        missing = validate_templates()
        assert len(missing) > 0
        assert DRIP_WELCOME in missing

    def test_get_subject_welcome(self):
        subject = _get_subject(DRIP_WELCOME)
        assert "Welcome" in subject

    def test_get_subject_first_sale(self):
        subject = _get_subject(DRIP_FIRST_SALE)
        assert "First Sale" in subject or "Congratulations" in subject

    def test_get_subject_weekly_digest_with_period(self):
        subject = _get_subject(DRIP_WEEKLY_DIGEST, period="Mar 17-23")
        assert "Mar 17-23" in subject

    def test_get_subject_unknown_type(self):
        subject = _get_subject("unknown")
        assert "AgenticTrade" in subject


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

class TestDripProcessEndpoint:
    def test_drip_process_requires_admin_key(self, client):
        resp = client.post("/api/v1/email/drip-process")
        assert resp.status_code in (401, 503)

    def test_drip_process_wrong_key_rejected(self, client):
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "real"}, clear=False):
            resp = client.post(
                "/api/v1/email/drip-process",
                headers={"x-admin-key": "wrong"},
            )
        assert resp.status_code == 401

    @patch("marketplace.drip_email._send_email", return_value=True)
    def test_drip_process_with_valid_key(self, mock_send, client, db):
        # Schedule a drip first
        sched = DripEmailScheduler(db)
        sched.schedule_welcome_sequence("prov-api-1", "api@example.com")

        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "test-secret"}, clear=False):
            resp = client.post(
                "/api/v1/email/drip-process",
                headers={"x-admin-key": "test-secret"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sent"] == 1
        assert data["failed"] == 0

    def test_drip_process_dry_run(self, client, db):
        sched = DripEmailScheduler(db)
        sched.schedule_welcome_sequence("prov-api-dry", "dry@example.com")

        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "test-secret"}, clear=False):
            resp = client.post(
                "/api/v1/email/drip-process?dry_run=true",
                headers={"x-admin-key": "test-secret"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["skipped"] == 1
        assert data["sent"] == 0


class TestDripTriggerEndpoint:
    def test_trigger_requires_admin_key(self, client):
        resp = client.post("/api/v1/email/drip-trigger", json={
            "provider_id": "prov-1",
            "email": "t@example.com",
            "trigger": "first_sale",
        })
        assert resp.status_code in (401, 503)

    def test_trigger_first_sale(self, client, db):
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "test-secret"}, clear=False):
            resp = client.post(
                "/api/v1/email/drip-trigger",
                json={
                    "provider_id": "prov-trig-1",
                    "email": "trig@example.com",
                    "trigger": "first_sale",
                },
                headers={"x-admin-key": "test-secret"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["triggered"] is True
        assert data["drip_type"] == "first_sale"

    def test_trigger_duplicate_returns_false(self, client, db):
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "test-secret"}, clear=False):
            # First trigger
            client.post(
                "/api/v1/email/drip-trigger",
                json={
                    "provider_id": "prov-trig-dup",
                    "email": "dup@example.com",
                    "trigger": "first_sale",
                },
                headers={"x-admin-key": "test-secret"},
            )
            # Second trigger (duplicate)
            resp = client.post(
                "/api/v1/email/drip-trigger",
                json={
                    "provider_id": "prov-trig-dup",
                    "email": "dup@example.com",
                    "trigger": "first_sale",
                },
                headers={"x-admin-key": "test-secret"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["triggered"] is False

    def test_trigger_unknown_type_rejected(self, client):
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "test-secret"}, clear=False):
            resp = client.post(
                "/api/v1/email/drip-trigger",
                json={
                    "provider_id": "prov-x",
                    "email": "x@example.com",
                    "trigger": "invalid_trigger",
                },
                headers={"x-admin-key": "test-secret"},
            )
        assert resp.status_code == 422

    def test_trigger_invalid_email_rejected(self, client):
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "test-secret"}, clear=False):
            resp = client.post(
                "/api/v1/email/drip-trigger",
                json={
                    "provider_id": "prov-bad",
                    "email": "not-an-email",
                    "trigger": "first_sale",
                },
                headers={"x-admin-key": "test-secret"},
            )
        assert resp.status_code == 422


class TestDripStatusEndpoint:
    def test_status_requires_admin_key(self, client):
        resp = client.get("/api/v1/email/drip-status")
        assert resp.status_code in (401, 503)

    def test_global_pending_count(self, client, db):
        sched = DripEmailScheduler(db)
        sched.schedule_welcome_sequence("prov-stat-1", "stat@example.com")

        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "test-secret"}, clear=False):
            resp = client.get(
                "/api/v1/email/drip-status",
                headers={"x-admin-key": "test-secret"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "pending_count" in data
        assert data["pending_count"] >= 1

    def test_provider_specific_drips(self, client, db):
        sched = DripEmailScheduler(db)
        sched.schedule_welcome_sequence("prov-stat-2", "stat2@example.com")
        sched.trigger_first_sale("prov-stat-2", "stat2@example.com")

        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "test-secret"}, clear=False):
            resp = client.get(
                "/api/v1/email/drip-status?provider_id=prov-stat-2",
                headers={"x-admin-key": "test-secret"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider_id"] == "prov-stat-2"
        assert len(data["drips"]) == 3

    def test_provider_no_drips(self, client, db):
        # Ensure the table exists
        DripEmailScheduler(db)

        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "test-secret"}, clear=False):
            resp = client.get(
                "/api/v1/email/drip-status?provider_id=nonexistent",
                headers={"x-admin-key": "test-secret"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["drips"] == []
