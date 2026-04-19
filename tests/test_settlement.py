"""Tests for Settlement engine."""
from __future__ import annotations

import uuid
import pytest
from datetime import datetime, timezone
from decimal import Decimal

from marketplace.db import Database
from marketplace.settlement import SettlementEngine, SettlementError


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def engine(db):
    return SettlementEngine(db, platform_fee_pct=Decimal("0.10"))


def _insert_usage(db, provider_id, amount, timestamp, status_code=200):
    """Helper to insert a usage record."""
    db.insert_usage({
        "id": str(uuid.uuid4()),
        "buyer_id": "buyer-1",
        "service_id": "svc-1",
        "provider_id": provider_id,
        "timestamp": timestamp,
        "latency_ms": 50,
        "status_code": status_code,
        "amount_usd": float(amount),
        "payment_method": "x402",
        "payment_tx": None,
    })


# --- Calculate settlement ---

class TestCalculateSettlement:
    def test_empty_period(self, engine):
        result = engine.calculate_settlement(
            "prov-1", "2026-01-01", "2026-02-01"
        )
        assert result["total_amount"] == Decimal("0")
        assert result["net_amount"] == Decimal("0")
        assert result["call_count"] == 0

    def test_single_call(self, engine, db):
        _insert_usage(db, "prov-1", 1.00, "2026-01-15T10:00:00")
        result = engine.calculate_settlement(
            "prov-1", "2026-01-01", "2026-02-01"
        )
        assert result["total_amount"] == Decimal("1")
        assert result["platform_fee"] == Decimal("0.1")
        assert result["net_amount"] == Decimal("0.9")
        assert result["call_count"] == 1

    def test_multiple_calls(self, engine, db):
        _insert_usage(db, "prov-1", 0.50, "2026-01-10T10:00:00")
        _insert_usage(db, "prov-1", 0.30, "2026-01-15T10:00:00")
        _insert_usage(db, "prov-1", 0.20, "2026-01-20T10:00:00")
        result = engine.calculate_settlement(
            "prov-1", "2026-01-01", "2026-02-01"
        )
        assert result["total_amount"] == Decimal("1")
        assert result["call_count"] == 3

    def test_excludes_server_errors(self, engine, db):
        _insert_usage(db, "prov-1", 0.50, "2026-01-10T10:00:00", status_code=200)
        _insert_usage(db, "prov-1", 0.50, "2026-01-15T10:00:00", status_code=500)
        result = engine.calculate_settlement(
            "prov-1", "2026-01-01", "2026-02-01"
        )
        # Only the 200 call counts
        assert result["total_amount"] == Decimal("0.5")
        assert result["call_count"] == 1

    def test_respects_period_boundaries(self, engine, db):
        _insert_usage(db, "prov-1", 1.00, "2025-12-31T23:59:59")  # Before
        _insert_usage(db, "prov-1", 2.00, "2026-01-15T10:00:00")  # In range
        _insert_usage(db, "prov-1", 3.00, "2026-02-01T00:00:00")  # After (exclusive)
        result = engine.calculate_settlement(
            "prov-1", "2026-01-01", "2026-02-01"
        )
        assert result["total_amount"] == Decimal("2")

    def test_isolates_providers(self, engine, db):
        _insert_usage(db, "prov-1", 1.00, "2026-01-15T10:00:00")
        _insert_usage(db, "prov-2", 5.00, "2026-01-15T10:00:00")
        result = engine.calculate_settlement(
            "prov-1", "2026-01-01", "2026-02-01"
        )
        assert result["total_amount"] == Decimal("1")

    def test_empty_provider_id_rejected(self, engine):
        with pytest.raises(SettlementError, match="provider_id"):
            engine.calculate_settlement("", "2026-01-01", "2026-02-01")


# --- Create settlement ---

class TestCreateSettlement:
    def test_creates_and_persists(self, engine, db):
        _insert_usage(db, "prov-1", 10.00, "2026-01-15T10:00:00")
        result = engine.create_settlement(
            "prov-1", "2026-01-01", "2026-02-01"
        )
        assert "id" in result
        assert result["status"] == "pending"
        assert result["net_amount"] == Decimal("9")

    def test_settlement_appears_in_list(self, engine, db):
        _insert_usage(db, "prov-1", 5.00, "2026-01-15T10:00:00")
        engine.create_settlement("prov-1", "2026-01-01", "2026-02-01")
        settlements = engine.list_settlements(provider_id="prov-1")
        assert len(settlements) == 1
        assert settlements[0]["net_amount"] == Decimal("4.5")


# --- List settlements ---

class TestListSettlements:
    def test_list_empty(self, engine):
        assert engine.list_settlements() == []

    def test_filter_by_status(self, engine, db):
        _insert_usage(db, "prov-1", 1.00, "2026-01-15T10:00:00")
        engine.create_settlement("prov-1", "2026-01-01", "2026-02-01")
        assert len(engine.list_settlements(status="pending")) == 1
        assert len(engine.list_settlements(status="completed")) == 0

    def test_filter_by_provider(self, engine, db):
        _insert_usage(db, "prov-1", 1.00, "2026-01-15T10:00:00")
        _insert_usage(db, "prov-2", 2.00, "2026-01-15T10:00:00")
        engine.create_settlement("prov-1", "2026-01-01", "2026-02-01")
        engine.create_settlement("prov-2", "2026-01-01", "2026-02-01")
        assert len(engine.list_settlements(provider_id="prov-1")) == 1


# --- Mark paid ---

class TestMarkPaid:
    def test_mark_paid(self, engine, db):
        _insert_usage(db, "prov-1", 10.00, "2026-01-15T10:00:00")
        settlement = engine.create_settlement(
            "prov-1", "2026-01-01", "2026-02-01"
        )
        success = engine.mark_paid(settlement["id"], "0xABC123")
        assert success
        settlements = engine.list_settlements(status="completed")
        assert len(settlements) == 1
        assert settlements[0]["payment_tx"] == "0xABC123"

    def test_cannot_pay_twice(self, engine, db):
        _insert_usage(db, "prov-1", 5.00, "2026-01-15T10:00:00")
        settlement = engine.create_settlement(
            "prov-1", "2026-01-01", "2026-02-01"
        )
        engine.mark_paid(settlement["id"], "0xABC")
        # Second payment attempt should fail
        assert not engine.mark_paid(settlement["id"], "0xDEF")

    def test_pay_nonexistent(self, engine):
        assert not engine.mark_paid("nonexistent-id", "0xABC")
