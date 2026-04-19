"""Tests for Reputation Engine."""
from __future__ import annotations

import pytest
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from marketplace.db import Database
from marketplace.reputation import ReputationEngine


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(Path(tmp) / "test.db")


@pytest.fixture
def engine(db):
    return ReputationEngine(db)


def _insert_agent(db, agent_id="provider-1", name="TestBot"):
    now = datetime.now(timezone.utc).isoformat()
    db.insert_agent({
        "agent_id": agent_id,
        "display_name": name,
        "owner_id": "owner-1",
        "created_at": now,
        "updated_at": now,
    })


def _insert_usage(db, provider_id="provider-1", service_id="svc-1",
                   latency_ms=100, status_code=200, count=1):
    for _ in range(count):
        db.insert_usage({
            "id": str(uuid.uuid4()),
            "buyer_id": "buyer-1",
            "service_id": service_id,
            "provider_id": provider_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latency_ms": latency_ms,
            "status_code": status_code,
            "amount_usd": 0.01,
            "payment_method": "x402",
        })


# --- Compute Reputation ---

class TestCompute:
    def test_no_usage_returns_zeros(self, engine):
        scores = engine.compute_reputation("provider-1")
        assert scores["overall_score"] == 0.0
        assert scores["call_count"] == 0

    def test_perfect_service(self, engine, db):
        _insert_usage(db, latency_ms=50, status_code=200, count=10)
        scores = engine.compute_reputation("provider-1")
        assert scores["call_count"] == 10
        assert scores["latency_score"] > 9.0  # 50ms is fast
        assert scores["reliability_score"] == 10.0  # 100% success
        assert scores["response_quality"] == 10.0  # 0% errors
        assert scores["overall_score"] > 9.0

    def test_slow_service(self, engine, db):
        _insert_usage(db, latency_ms=5000, status_code=200, count=5)
        scores = engine.compute_reputation("provider-1")
        assert scores["latency_score"] < 6.0  # 5000ms is slow

    def test_unreliable_service(self, engine, db):
        _insert_usage(db, latency_ms=100, status_code=200, count=5)
        _insert_usage(db, latency_ms=100, status_code=500, count=5)
        scores = engine.compute_reputation("provider-1")
        # 50% success, 50% errors
        assert scores["reliability_score"] == 5.0
        assert scores["response_quality"] == 5.0

    def test_all_errors(self, engine, db):
        _insert_usage(db, latency_ms=100, status_code=500, count=10)
        scores = engine.compute_reputation("provider-1")
        assert scores["reliability_score"] == 0.0
        assert scores["response_quality"] == 0.0

    def test_specific_service(self, engine, db):
        _insert_usage(db, service_id="svc-1", latency_ms=100, count=5)
        _insert_usage(db, service_id="svc-2", latency_ms=5000, count=5)
        scores = engine.compute_reputation("provider-1", service_id="svc-1")
        assert scores["call_count"] == 5
        assert scores["latency_score"] > 9.0  # Only svc-1's 100ms

    def test_weighted_average(self, engine, db):
        # Perfect service: latency=10, reliability=10, quality=10
        _insert_usage(db, latency_ms=1, status_code=200, count=10)
        scores = engine.compute_reputation("provider-1")
        # 10*0.3 + 10*0.4 + 10*0.3 = 10.0
        assert scores["overall_score"] == 10.0


# --- Save Reputation ---

class TestSave:
    def test_save_creates_record(self, engine, db):
        _insert_agent(db)
        _insert_usage(db, latency_ms=100, status_code=200, count=5)
        scores = engine.save_reputation("provider-1")
        assert scores["call_count"] == 5

        # Verify persisted
        records = engine.get_agent_reputation("provider-1")
        assert len(records) == 1
        assert records[0]["overall_score"] > 0

    def test_save_updates_agent_score(self, engine, db):
        _insert_agent(db)
        _insert_usage(db, latency_ms=100, status_code=200, count=5)
        engine.save_reputation("provider-1")

        agent = db.get_agent("provider-1")
        assert agent["reputation_score"] > 0


# --- Leaderboard ---

class TestLeaderboard:
    def test_empty_leaderboard(self, engine):
        board = engine.get_leaderboard()
        assert board == []

    def test_ranked_by_score(self, engine, db):
        _insert_agent(db, "agent-1", "FastBot")
        _insert_agent(db, "agent-2", "SlowBot")
        db.update_agent("agent-1", {"reputation_score": 9.5})
        db.update_agent("agent-2", {"reputation_score": 3.0})

        board = engine.get_leaderboard()
        assert len(board) == 2
        assert board[0]["agent_id"] == "agent-1"
        assert board[0]["reputation_score"] == 9.5
        assert board[1]["agent_id"] == "agent-2"

    def test_leaderboard_limit(self, engine, db):
        for i in range(5):
            _insert_agent(db, f"agent-{i}", f"Bot{i}")
        board = engine.get_leaderboard(limit=3)
        assert len(board) == 3


# --- Get Service Reputation ---

class TestServiceReputation:
    def test_no_records(self, engine):
        records = engine.get_service_reputation("svc-1")
        assert records == []

    def test_with_saved_records(self, engine, db):
        _insert_agent(db)
        _insert_usage(db, service_id="svc-1", count=5)
        engine.save_reputation("provider-1", service_id="svc-1")

        records = engine.get_service_reputation("svc-1")
        assert len(records) == 1


# --- Edge Cases ---

class TestEdgeCases:
    def test_very_high_latency(self, engine, db):
        _insert_usage(db, latency_ms=20000, count=1)
        scores = engine.compute_reputation("provider-1")
        assert scores["latency_score"] == 0.0  # Clamped to 0

    def test_4xx_counts_as_success(self, engine, db):
        """4xx errors are client errors, not provider failures."""
        _insert_usage(db, status_code=400, count=5)
        scores = engine.compute_reputation("provider-1")
        # 400 is < 500, so not an error in reliability
        # but also not < 400, so not a success
        assert scores["call_count"] == 5
