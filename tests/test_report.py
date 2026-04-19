"""Tests for service abuse reporting and auto-delist (marketplace/report.py)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from marketplace.db import Database
from marketplace.identity import IdentityManager
from marketplace.agent_provider import AgentProviderManager
from marketplace.report import ReportManager, ReportError

# Access class-level constants
DELIST_THRESHOLD = ReportManager.DELIST_THRESHOLD
VALID_REASONS = ReportManager.VALID_REASONS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def mgr(db):
    return ReportManager(db)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _create_provider(db) -> tuple[str, str]:
    """Create an agent identity + agent provider. Returns (provider_id, agent_id)."""
    identity_mgr = IdentityManager(db)
    agent = identity_mgr.register(
        display_name="Test Agent",
        owner_id=f"owner-{uuid.uuid4().hex[:8]}",
        identity_type="api_key_only",
    )
    agent_id = agent.agent_id

    provider_mgr = AgentProviderManager(db)
    provider = provider_mgr.register(
        agent_id=agent_id,
        owner_email="test@example.com",
        wallet_address="0x" + "ab" * 20,
        did="did:key:z6Mktest123",
    )
    # Activate the provider so it has status=active
    provider = provider_mgr.activate(provider["id"])
    return provider["id"], agent_id


def _insert_usage_record(db, buyer_id: str, service_id: str, provider_id: str) -> None:
    """Insert a usage record so the buyer passes the anti-sybil check."""
    now = _now_iso()
    db.insert_usage({
        "id": str(uuid.uuid4()),
        "buyer_id": buyer_id,
        "service_id": service_id,
        "provider_id": provider_id,
        "timestamp": now,
        "latency_ms": 100,
        "status_code": 200,
        "amount_usd": 0.01,
        "payment_method": "x402",
        "payment_tx": None,
    })


def _file_report(db, mgr, service_id, reporter_id, reason, provider_id, details=""):
    """Insert usage record for reporter, then file the report."""
    _insert_usage_record(db, reporter_id, service_id, provider_id)
    return mgr.file_report(service_id, reporter_id, reason, details=details)


def _insert_service(db, provider_id: str, status: str = "active") -> str:
    """Insert a service for the given provider. Returns the service_id."""
    service_id = str(uuid.uuid4())
    now = _now_iso()
    db.insert_service({
        "id": service_id,
        "provider_id": provider_id,
        "name": "Test Service",
        "description": "A test service for report testing",
        "endpoint": "https://api.example.com/v1",
        "price_per_call": 0.01,
        "currency": "USDC",
        "payment_method": "x402",
        "free_tier_calls": 0,
        "status": status,
        "category": "test",
        "tags": ["test"],
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    })
    return service_id


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_delist_threshold_is_five(self):
        assert DELIST_THRESHOLD == 5
        assert ReportManager.DELIST_THRESHOLD == 5

    def test_valid_reasons(self):
        assert VALID_REASONS == frozenset({"malicious", "inaccurate", "unavailable", "other"})

    def test_valid_reasons_is_frozen(self):
        with pytest.raises(AttributeError):
            VALID_REASONS.add("spam")  # type: ignore[attr-defined]

    def test_min_usage_to_report(self):
        assert ReportManager.MIN_USAGE_TO_REPORT == 1


# ---------------------------------------------------------------------------
# TestAntiSybil
# ---------------------------------------------------------------------------


class TestAntiSybil:
    def test_no_usage_record_raises(self, db, mgr):
        """Reporter with no usage records cannot file a report."""
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)
        reporter_id = str(uuid.uuid4())

        with pytest.raises(ReportError, match="must have used this service"):
            mgr.file_report(service_id, reporter_id, "malicious")

    def test_with_usage_record_succeeds(self, db, mgr):
        """Reporter with usage record can file a report."""
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)
        reporter_id = str(uuid.uuid4())

        _insert_usage_record(db, reporter_id, service_id, provider_id)
        report = mgr.file_report(service_id, reporter_id, "malicious")
        assert report["reporter_id"] == reporter_id

    def test_usage_on_different_service_not_counted(self, db, mgr):
        """Usage record on service A doesn't count for reporting service B."""
        provider_id, _ = _create_provider(db)
        svc_a = _insert_service(db, provider_id)
        svc_b = _insert_service(db, provider_id)
        reporter_id = str(uuid.uuid4())

        # Usage on svc_a only
        _insert_usage_record(db, reporter_id, svc_a, provider_id)

        with pytest.raises(ReportError, match="must have used this service"):
            mgr.file_report(svc_b, reporter_id, "malicious")


# ---------------------------------------------------------------------------
# TestFileReport
# ---------------------------------------------------------------------------


class TestFileReport:
    def test_valid_report(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)
        reporter_id = str(uuid.uuid4())

        report = _file_report(db, mgr, service_id, reporter_id, "malicious",
                              provider_id, details="Stole my tokens")

        assert report["id"]
        assert report["service_id"] == service_id
        assert report["provider_id"] == provider_id
        assert report["reporter_id"] == reporter_id
        assert report["reason"] == "malicious"
        assert report["details"] == "Stole my tokens"
        assert report["status"] == "open"
        assert report["created_at"]

    def test_all_valid_reasons_accepted(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        for reason in sorted(VALID_REASONS):
            reporter_id = str(uuid.uuid4())
            report = _file_report(db, mgr, service_id, reporter_id, reason, provider_id)
            assert report["reason"] == reason

    def test_invalid_reason_raises(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)
        reporter_id = str(uuid.uuid4())
        _insert_usage_record(db, reporter_id, service_id, provider_id)

        with pytest.raises(ReportError, match="Invalid reason"):
            mgr.file_report(service_id, reporter_id, "spam")

    def test_empty_reason_raises(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)
        reporter_id = str(uuid.uuid4())
        _insert_usage_record(db, reporter_id, service_id, provider_id)

        with pytest.raises(ReportError, match="Invalid reason"):
            mgr.file_report(service_id, reporter_id, "")

    def test_duplicate_reporter_raises(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)
        reporter_id = str(uuid.uuid4())

        _file_report(db, mgr, service_id, reporter_id, "malicious", provider_id)
        with pytest.raises(ReportError, match="already filed"):
            mgr.file_report(service_id, reporter_id, "inaccurate")

    def test_service_not_found_raises(self, db, mgr):
        fake_service_id = str(uuid.uuid4())
        with pytest.raises(ReportError, match="Service not found"):
            mgr.file_report(fake_service_id, str(uuid.uuid4()), "malicious")

    def test_missing_service_id_raises(self, db, mgr):
        with pytest.raises(ReportError, match="service_id and reporter_id are required"):
            mgr.file_report("", str(uuid.uuid4()), "malicious")

    def test_missing_reporter_id_raises(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        with pytest.raises(ReportError, match="service_id and reporter_id are required"):
            mgr.file_report(service_id, "", "malicious")

    def test_default_details_is_empty_string(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        report = _file_report(db, mgr, service_id, str(uuid.uuid4()), "other", provider_id)
        assert report["details"] == ""

    def test_report_ids_are_unique(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        r1 = _file_report(db, mgr, service_id, str(uuid.uuid4()), "malicious", provider_id)
        r2 = _file_report(db, mgr, service_id, str(uuid.uuid4()), "inaccurate", provider_id)
        assert r1["id"] != r2["id"]


# ---------------------------------------------------------------------------
# TestAutoDelist
# ---------------------------------------------------------------------------


class TestAutoDelist:
    def test_below_threshold_no_delist(self, db, mgr):
        """Four reports should NOT trigger delist (threshold=5)."""
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        for _ in range(4):
            _file_report(db, mgr, service_id, str(uuid.uuid4()), "malicious", provider_id)

        service = db.get_service(service_id)
        assert service["status"] == "active"

        provider = db.get_agent_provider(provider_id)
        assert provider["status"] == "active"

    def test_five_reports_triggers_delist(self, db, mgr):
        """Accumulating 5 non-dismissed reports triggers auto-delist."""
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        for _ in range(5):
            _file_report(db, mgr, service_id, str(uuid.uuid4()), "malicious", provider_id)

        # Service should be delisted (status='removed')
        service = db.get_service(service_id)
        assert service["status"] == "removed"

        # Provider should be suspended
        provider = db.get_agent_provider(provider_id)
        assert provider["status"] == "suspended"

    def test_more_than_threshold_still_delist(self, db, mgr):
        """6 reports also keeps service removed and provider suspended."""
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        for _ in range(6):
            _file_report(db, mgr, service_id, str(uuid.uuid4()), "malicious", provider_id)

        service = db.get_service(service_id)
        assert service["status"] == "removed"

        provider = db.get_agent_provider(provider_id)
        assert provider["status"] == "suspended"

    def test_delist_writes_audit_log(self, db, mgr):
        """Auto-delist should write an audit event."""
        from marketplace.audit import AuditLogger

        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        # File 5 reports to trigger delist
        for _ in range(5):
            _file_report(db, mgr, service_id, str(uuid.uuid4()), "malicious", provider_id)

        # Check the audit trail
        audit = AuditLogger(db.db_path)
        events = audit.get_events(event_type="service_deleted")
        assert len(events) >= 1
        assert service_id in events[0]["target"]


# ---------------------------------------------------------------------------
# TestGetReports
# ---------------------------------------------------------------------------


class TestGetReports:
    def test_list_reports(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        r1 = _file_report(db, mgr, service_id, str(uuid.uuid4()), "malicious", provider_id, "detail 1")
        r2 = _file_report(db, mgr, service_id, str(uuid.uuid4()), "inaccurate", provider_id, "detail 2")

        reports = mgr.get_reports(service_id)
        assert len(reports) == 2

        report_ids = {r["id"] for r in reports}
        assert r1["id"] in report_ids
        assert r2["id"] in report_ids

    def test_empty_list(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        reports = mgr.get_reports(service_id)
        assert reports == []

    def test_reports_contain_expected_fields(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        _file_report(db, mgr, service_id, str(uuid.uuid4()), "other", provider_id, "test details")

        reports = mgr.get_reports(service_id)
        assert len(reports) == 1
        r = reports[0]
        assert "id" in r
        assert "service_id" in r
        assert "provider_id" in r
        assert "reporter_id" in r
        assert "reason" in r
        assert "details" in r
        assert "status" in r
        assert "created_at" in r

    def test_reports_ordered_by_created_at_desc(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        _file_report(db, mgr, service_id, str(uuid.uuid4()), "malicious", provider_id)
        _file_report(db, mgr, service_id, str(uuid.uuid4()), "inaccurate", provider_id)

        reports = mgr.get_reports(service_id)
        assert len(reports) == 2
        # Second report should appear first (DESC)
        assert reports[0]["created_at"] >= reports[1]["created_at"]

    def test_reports_scoped_to_service(self, db, mgr):
        """Reports for one service should not appear for another."""
        provider_id, _ = _create_provider(db)
        svc_a = _insert_service(db, provider_id)
        svc_b = _insert_service(db, provider_id)

        _file_report(db, mgr, svc_a, str(uuid.uuid4()), "malicious", provider_id)
        _file_report(db, mgr, svc_b, str(uuid.uuid4()), "inaccurate", provider_id)

        reports_a = mgr.get_reports(svc_a)
        reports_b = mgr.get_reports(svc_b)
        assert len(reports_a) == 1
        assert len(reports_b) == 1
        assert reports_a[0]["service_id"] == svc_a
        assert reports_b[0]["service_id"] == svc_b


# ---------------------------------------------------------------------------
# TestDismissReport
# ---------------------------------------------------------------------------


class TestDismissReport:
    def test_dismiss_open_report(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)
        reporter_id = str(uuid.uuid4())

        report = _file_report(db, mgr, service_id, reporter_id, "malicious", provider_id)
        result = mgr.dismiss_report(report["id"])
        assert result is True

        # Verify status changed
        reports = mgr.get_reports(service_id)
        assert len(reports) == 1
        assert reports[0]["status"] == "dismissed"

    def test_dismiss_already_dismissed_returns_false(self, db, mgr):
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        report = _file_report(db, mgr, service_id, str(uuid.uuid4()), "inaccurate", provider_id)
        mgr.dismiss_report(report["id"])

        # Second dismiss should return False (already dismissed, not open)
        result = mgr.dismiss_report(report["id"])
        assert result is False

    def test_dismiss_nonexistent_returns_false(self, db, mgr):
        fake_id = str(uuid.uuid4())
        result = mgr.dismiss_report(fake_id)
        assert result is False

    def test_dismissed_reporter_can_file_again(self, db, mgr):
        """After a report is dismissed, the same reporter can file a new one."""
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)
        reporter_id = str(uuid.uuid4())

        report = _file_report(db, mgr, service_id, reporter_id, "malicious", provider_id)
        mgr.dismiss_report(report["id"])

        # Same reporter should be able to file again since previous was dismissed
        # (usage record already exists from _file_report above)
        new_report = mgr.file_report(service_id, reporter_id, "unavailable")
        assert new_report["id"] != report["id"]
        assert new_report["reporter_id"] == reporter_id


# ---------------------------------------------------------------------------
# TestDismissedReportsNotCountForDelist
# ---------------------------------------------------------------------------


class TestDismissedReportsNotCountForDelist:
    def test_dismissed_reports_excluded_from_threshold(self, db, mgr):
        """Dismissed reports should not count toward the delist threshold."""
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        # File 2 reports, dismiss both
        r1 = _file_report(db, mgr, service_id, str(uuid.uuid4()), "malicious", provider_id)
        r2 = _file_report(db, mgr, service_id, str(uuid.uuid4()), "inaccurate", provider_id)
        mgr.dismiss_report(r1["id"])
        mgr.dismiss_report(r2["id"])

        # File 2 more open reports (total open = 2, total dismissed = 2)
        _file_report(db, mgr, service_id, str(uuid.uuid4()), "unavailable", provider_id)
        _file_report(db, mgr, service_id, str(uuid.uuid4()), "other", provider_id)

        # Service should still be active (only 2 non-dismissed reports)
        service = db.get_service(service_id)
        assert service["status"] == "active"

        provider = db.get_agent_provider(provider_id)
        assert provider["status"] == "active"

    def test_dismissed_then_threshold_reached(self, db, mgr):
        """Mix of dismissed and open: only open reports count toward threshold."""
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        # File 2 and dismiss them
        r1 = _file_report(db, mgr, service_id, str(uuid.uuid4()), "malicious", provider_id)
        r2 = _file_report(db, mgr, service_id, str(uuid.uuid4()), "inaccurate", provider_id)
        mgr.dismiss_report(r1["id"])
        mgr.dismiss_report(r2["id"])

        # File 5 more open reports to reach threshold
        for _ in range(5):
            _file_report(db, mgr, service_id, str(uuid.uuid4()), "other", provider_id)

        # Now 5 non-dismissed reports exist -> should be delisted
        service = db.get_service(service_id)
        assert service["status"] == "removed"

        provider = db.get_agent_provider(provider_id)
        assert provider["status"] == "suspended"

    def test_count_reports_excludes_dismissed(self, db, mgr):
        """Verify the DB count_reports_for_service method excludes dismissed."""
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        r1 = _file_report(db, mgr, service_id, str(uuid.uuid4()), "malicious", provider_id)
        _file_report(db, mgr, service_id, str(uuid.uuid4()), "inaccurate", provider_id)

        # Before dismissal: count = 2
        assert db.count_reports_for_service(service_id) == 2

        # After dismissing one: count = 1
        mgr.dismiss_report(r1["id"])
        assert db.count_reports_for_service(service_id) == 1

    def test_all_dismissed_count_is_zero(self, db, mgr):
        """If all reports are dismissed, count should be 0."""
        provider_id, _ = _create_provider(db)
        service_id = _insert_service(db, provider_id)

        r1 = _file_report(db, mgr, service_id, str(uuid.uuid4()), "malicious", provider_id)
        r2 = _file_report(db, mgr, service_id, str(uuid.uuid4()), "inaccurate", provider_id)

        mgr.dismiss_report(r1["id"])
        mgr.dismiss_report(r2["id"])

        assert db.count_reports_for_service(service_id) == 0
