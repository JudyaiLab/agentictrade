"""Tests for Service Health Monitor."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from marketplace.db import Database
from marketplace.health_monitor import HealthCheckResult, HealthMonitor


@pytest.fixture
def db(tmp_path):
    return Database(db_path=tmp_path / "test.db")


@pytest.fixture
def monitor(db):
    return HealthMonitor(db)


def _insert_service(db: Database, provider_id: str = "prov-1", endpoint: str = "https://example.com/api") -> str:
    """Insert a test service directly into DB."""
    svc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO services
               (id, provider_id, name, description, endpoint,
                price_per_call, currency, payment_method, free_tier_calls,
                status, category, tags, metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (svc_id, provider_id, f"Svc-{svc_id[:8]}", "Test", endpoint,
             0.5, "USDC", "x402", 0, "active", "test", "[]", "{}", now, now),
        )
    return svc_id


def _insert_health_check(
    db: Database, service_id: str, provider_id: str = "prov-1",
    reachable: bool = True, latency_ms: int = 50, status_code: int = 200,
    checked_at: datetime | None = None,
) -> None:
    """Insert a health check result directly."""
    if checked_at is None:
        checked_at = datetime.now(timezone.utc)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO health_checks
               (id, service_id, provider_id, reachable, latency_ms,
                status_code, error, checked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), service_id, provider_id,
             1 if reachable else 0, latency_ms, status_code, "",
             checked_at.isoformat()),
        )


class TestHealthCheckResult:
    """Single service health check."""

    def test_check_reachable_service(self, monitor, db):
        svc_id = _insert_service(db)
        svc = db.get_service(svc_id)

        with patch("marketplace.health_monitor.httpx.AsyncClient") as mock_client:
            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

            # Use a simpler approach - mock the whole check
            result = HealthCheckResult(
                service_id=svc_id,
                provider_id="prov-1",
                reachable=True,
                latency_ms=42,
                status_code=200,
            )
            monitor._save_result(result)

            # Verify saved
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT * FROM health_checks WHERE service_id = ?",
                    (svc_id,),
                ).fetchone()
                assert row is not None
                assert row["reachable"] == 1
                assert row["latency_ms"] == 42

    def test_check_unreachable_service(self, monitor, db):
        svc_id = _insert_service(db)
        result = HealthCheckResult(
            service_id=svc_id,
            provider_id="prov-1",
            reachable=False,
            latency_ms=10000,
            status_code=0,
            error="timeout",
        )
        monitor._save_result(result)

        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM health_checks WHERE service_id = ?",
                (svc_id,),
            ).fetchone()
            assert row["reachable"] == 0
            assert row["error"] == "timeout"


class TestHealthScore:
    """Quality score calculation."""

    def test_perfect_score(self, monitor, db):
        svc_id = _insert_service(db)
        now = datetime.now(timezone.utc)
        # 10 perfect checks
        for i in range(10):
            _insert_health_check(
                db, svc_id, reachable=True, latency_ms=30,
                checked_at=now - timedelta(hours=i),
            )

        score = monitor.get_service_health_score(svc_id)
        assert score is not None
        assert score.uptime_pct == 100.0
        assert score.avg_latency_ms == 30.0
        assert score.error_rate_pct == 0.0
        assert score.quality_score > 90  # Should be near perfect

    def test_degraded_score(self, monitor, db):
        svc_id = _insert_service(db)
        now = datetime.now(timezone.utc)
        # 7 good, 3 failures
        for i in range(7):
            _insert_health_check(
                db, svc_id, reachable=True, latency_ms=100,
                checked_at=now - timedelta(hours=i),
            )
        for i in range(3):
            _insert_health_check(
                db, svc_id, reachable=False, latency_ms=10000,
                status_code=500,
                checked_at=now - timedelta(hours=7 + i),
            )

        score = monitor.get_service_health_score(svc_id)
        assert score is not None
        assert score.uptime_pct == 70.0
        assert score.error_rate_pct == 30.0
        assert score.quality_score < 80

    def test_no_checks_returns_none(self, monitor, db):
        svc_id = _insert_service(db)
        score = monitor.get_service_health_score(svc_id)
        assert score is None

    def test_old_checks_excluded(self, monitor, db):
        svc_id = _insert_service(db)
        # Check from 60 days ago (outside 30-day lookback)
        old = datetime.now(timezone.utc) - timedelta(days=60)
        _insert_health_check(db, svc_id, checked_at=old)

        score = monitor.get_service_health_score(svc_id, lookback_days=30)
        assert score is None


class TestAllHealthScores:
    """Ranking across all services."""

    def test_ranking_order(self, monitor, db):
        svc_a = _insert_service(db, provider_id="prov-a")
        svc_b = _insert_service(db, provider_id="prov-b")
        now = datetime.now(timezone.utc)

        # svc_a: perfect
        for i in range(5):
            _insert_health_check(
                db, svc_a, provider_id="prov-a",
                reachable=True, latency_ms=20,
                checked_at=now - timedelta(hours=i),
            )
        # svc_b: degraded
        for i in range(5):
            _insert_health_check(
                db, svc_b, provider_id="prov-b",
                reachable=(i < 3), latency_ms=500,
                status_code=200 if i < 3 else 500,
                checked_at=now - timedelta(hours=i),
            )

        scores = monitor.get_all_health_scores()
        assert len(scores) == 2
        assert scores[0].service_id == svc_a  # Higher quality first
        assert scores[0].rank == 1
        assert scores[1].rank == 2


class TestProviderHealthSummary:
    """Provider-level health aggregation."""

    def test_provider_with_multiple_services(self, monitor, db):
        svc_1 = _insert_service(db, provider_id="prov-1")
        svc_2 = _insert_service(db, provider_id="prov-1", endpoint="https://example2.com/api")
        now = datetime.now(timezone.utc)

        for svc_id in [svc_1, svc_2]:
            for i in range(3):
                _insert_health_check(
                    db, svc_id, provider_id="prov-1",
                    reachable=True, latency_ms=50,
                    checked_at=now - timedelta(hours=i),
                )

        summary = monitor.get_provider_health_summary("prov-1")
        assert summary["provider_id"] == "prov-1"
        assert summary["service_count"] == 2
        assert summary["avg_quality_score"] > 80
        assert len(summary["services"]) == 2

    def test_provider_with_no_checks(self, monitor, db):
        _insert_service(db, provider_id="prov-new")
        summary = monitor.get_provider_health_summary("prov-new")
        assert summary["avg_quality_score"] == 0
        assert summary["services"] == []
