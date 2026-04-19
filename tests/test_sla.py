"""Tests for SLA enforcement system."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from marketplace.db import Database
from marketplace.sla import (
    SLA_TIERS,
    SLAManager,
    SLAStatus,
)
from marketplace.health_monitor import HealthMonitor


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def sla_mgr(db):
    return SLAManager(db)


def _create_service(db, provider_id="prov-1", service_id=None) -> str:
    """Helper: insert an active service and return its ID."""
    svc_id = service_id or f"svc_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    db.insert_service({
        "id": svc_id,
        "provider_id": provider_id,
        "name": "Test SLA Service",
        "description": "Service for SLA testing",
        "endpoint": "https://api.test.com/v1",
        "price_per_call": 0.01,
        "currency": "USDC",
        "payment_method": "x402",
        "free_tier_calls": 0,
        "status": "active",
        "category": "ai",
        "tags": [],
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    })
    return svc_id


def _insert_health_checks(
    db, service_id, provider_id="prov-1",
    count=10, reachable=True, latency_ms=100, status_code=200,
):
    """Insert mock health check data."""
    monitor = HealthMonitor(db)
    now = datetime.now(timezone.utc)
    for i in range(count):
        check_time = now - timedelta(hours=i)
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO health_checks
                   (id, service_id, provider_id, reachable, latency_ms,
                    status_code, error, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    service_id,
                    provider_id,
                    1 if reachable else 0,
                    latency_ms,
                    status_code,
                    "" if reachable else "connection_refused",
                    check_time.isoformat(),
                ),
            )


# ── SLA Tier Configuration ──


class TestSLATiers:
    def test_three_tiers_exist(self):
        assert "basic" in SLA_TIERS
        assert "standard" in SLA_TIERS
        assert "premium" in SLA_TIERS

    def test_basic_tier_values(self):
        t = SLA_TIERS["basic"]
        assert t.uptime_pct_min == 95.0
        assert t.latency_p95_max_ms == 5000
        assert t.error_rate_max_pct == 10.0

    def test_standard_tier_values(self):
        t = SLA_TIERS["standard"]
        assert t.uptime_pct_min == 99.0
        assert t.latency_p95_max_ms == 2000
        assert t.error_rate_max_pct == 5.0

    def test_premium_tier_values(self):
        t = SLA_TIERS["premium"]
        assert t.uptime_pct_min == 99.9
        assert t.latency_p95_max_ms == 500
        assert t.error_rate_max_pct == 1.0


# ── Set/Get SLA ──


class TestSetGetSLA:
    def test_set_sla_basic(self, db, sla_mgr):
        svc_id = _create_service(db)
        result = sla_mgr.set_service_sla(svc_id, "basic")
        assert result["service_id"] == svc_id
        assert result["sla_tier"] == "basic"
        assert result["requirements"]["uptime_pct_min"] == 95.0

    def test_set_sla_standard(self, db, sla_mgr):
        svc_id = _create_service(db)
        result = sla_mgr.set_service_sla(svc_id, "standard")
        assert result["sla_tier"] == "standard"

    def test_set_sla_premium(self, db, sla_mgr):
        svc_id = _create_service(db)
        result = sla_mgr.set_service_sla(svc_id, "premium")
        assert result["sla_tier"] == "premium"

    def test_set_invalid_tier(self, db, sla_mgr):
        svc_id = _create_service(db)
        with pytest.raises(ValueError, match="Invalid SLA tier"):
            sla_mgr.set_service_sla(svc_id, "ultra")

    def test_get_sla(self, db, sla_mgr):
        svc_id = _create_service(db)
        sla_mgr.set_service_sla(svc_id, "standard")
        config = sla_mgr.get_service_sla(svc_id)
        assert config["sla_tier"] == "standard"
        assert config["requirements"]["uptime_pct_min"] == 99.0

    def test_get_sla_not_set(self, db, sla_mgr):
        config = sla_mgr.get_service_sla("nonexistent")
        assert config is None

    def test_update_sla_tier(self, db, sla_mgr):
        svc_id = _create_service(db)
        sla_mgr.set_service_sla(svc_id, "basic")
        sla_mgr.set_service_sla(svc_id, "premium")
        config = sla_mgr.get_service_sla(svc_id)
        assert config["sla_tier"] == "premium"


# ── SLA Compliance ──


class TestSLACompliance:
    def test_compliant_basic(self, db, sla_mgr):
        """Service with 100% uptime and low latency is compliant."""
        svc_id = _create_service(db)
        sla_mgr.set_service_sla(svc_id, "basic")
        _insert_health_checks(db, svc_id, reachable=True, latency_ms=100)

        status = sla_mgr.check_compliance(svc_id)
        assert status is not None
        assert status.compliant is True
        assert status.uptime_met is True
        assert status.latency_met is True
        assert status.error_rate_met is True

    def test_uptime_breach(self, db, sla_mgr):
        """Service with low uptime breaches SLA."""
        svc_id = _create_service(db)
        sla_mgr.set_service_sla(svc_id, "standard")  # 99% required

        # 8 reachable, 2 down = 80% uptime
        _insert_health_checks(db, svc_id, count=8, reachable=True, latency_ms=100)
        _insert_health_checks(db, svc_id, count=2, reachable=False, latency_ms=0, status_code=0)

        status = sla_mgr.check_compliance(svc_id)
        assert status.compliant is False
        assert status.uptime_met is False
        assert status.uptime_pct == 80.0

    def test_latency_breach(self, db, sla_mgr):
        """Service with high latency breaches SLA."""
        svc_id = _create_service(db)
        sla_mgr.set_service_sla(svc_id, "premium")  # 500ms max

        _insert_health_checks(db, svc_id, reachable=True, latency_ms=1000)

        status = sla_mgr.check_compliance(svc_id)
        assert status.compliant is False
        assert status.latency_met is False

    def test_no_health_data(self, db, sla_mgr):
        """Service with no health data returns None."""
        svc_id = _create_service(db)
        status = sla_mgr.check_compliance(svc_id)
        assert status is None

    def test_default_tier_when_not_set(self, db, sla_mgr):
        """Uses basic tier when no SLA is explicitly set."""
        svc_id = _create_service(db)
        _insert_health_checks(db, svc_id, reachable=True, latency_ms=100)

        status = sla_mgr.check_compliance(svc_id)
        assert status.sla_tier == "basic"


# ── SLA Breaches ──


class TestSLABreaches:
    def test_breach_recorded(self, db, sla_mgr):
        """SLA breach is recorded in database."""
        svc_id = _create_service(db)
        sla_mgr.set_service_sla(svc_id, "standard")

        # Create low-uptime data to trigger breach
        _insert_health_checks(db, svc_id, count=5, reachable=True, latency_ms=100)
        _insert_health_checks(db, svc_id, count=5, reachable=False, latency_ms=0, status_code=0)

        sla_mgr.check_compliance(svc_id)
        breaches = sla_mgr.get_breaches(svc_id)
        assert len(breaches) >= 1

        uptime_breach = [b for b in breaches if b["breach_type"] == "uptime"]
        assert len(uptime_breach) == 1
        assert uptime_breach[0]["expected_value"] == 99.0

    def test_no_breaches_when_compliant(self, db, sla_mgr):
        """No breaches recorded when service is compliant."""
        svc_id = _create_service(db)
        sla_mgr.set_service_sla(svc_id, "basic")
        _insert_health_checks(db, svc_id, reachable=True, latency_ms=100)

        sla_mgr.check_compliance(svc_id)
        breaches = sla_mgr.get_breaches(svc_id)
        assert len(breaches) == 0

    def test_breach_count_in_status(self, db, sla_mgr):
        """Breach count is included in compliance status."""
        svc_id = _create_service(db)
        sla_mgr.set_service_sla(svc_id, "premium")
        _insert_health_checks(db, svc_id, count=5, reachable=False, latency_ms=0, status_code=0)
        _insert_health_checks(db, svc_id, count=5, reachable=True, latency_ms=100)

        status = sla_mgr.check_compliance(svc_id)
        assert status.breach_count >= 1


# ── Provider Summary ──


class TestProviderSLASummary:
    def test_summary_single_service(self, db, sla_mgr):
        svc_id = _create_service(db, provider_id="prov-sum-1")
        sla_mgr.set_service_sla(svc_id, "basic")
        _insert_health_checks(db, svc_id, provider_id="prov-sum-1", reachable=True, latency_ms=100)

        summary = sla_mgr.get_provider_sla_summary("prov-sum-1")
        assert summary["total_services"] == 1
        assert summary["compliant_count"] == 1
        assert summary["compliance_rate"] == 100.0

    def test_summary_no_services(self, db, sla_mgr):
        summary = sla_mgr.get_provider_sla_summary("no-such-provider")
        assert summary["total_services"] == 0
        assert summary["compliance_rate"] == 0
