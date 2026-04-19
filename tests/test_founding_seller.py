"""Tests for Founding Seller badge system."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from marketplace.db import Database
from marketplace.registry import ServiceRegistry
from marketplace.commission import CommissionEngine


@pytest.fixture
def db(tmp_path):
    return Database(db_path=tmp_path / "test.db")


@pytest.fixture
def registry(db):
    return ServiceRegistry(db)


@pytest.fixture
def commission(db):
    return CommissionEngine(db)


def _register_service(registry: ServiceRegistry, provider_id: str) -> dict:
    """Helper to register a service with minimum required fields."""
    return registry.register(
        provider_id=provider_id,
        name=f"Service by {provider_id}",
        description="Test service",
        endpoint="https://example.com/api",
        price_per_call="0.50",
        category="test",
    )


class TestFoundingSellerAutoAward:
    """Founding Seller badge is auto-awarded on first service registration."""

    def test_first_provider_gets_badge(self, registry):
        _register_service(registry, "provider-1")
        seller = registry.get_founding_seller("provider-1")
        assert seller is not None
        assert seller["sequence_number"] == 1
        assert seller["badge_tier"] == "founding"
        assert seller["commission_rate"] == 0.08

    def test_second_provider_gets_sequence_2(self, registry):
        _register_service(registry, "provider-1")
        _register_service(registry, "provider-2")
        s1 = registry.get_founding_seller("provider-1")
        s2 = registry.get_founding_seller("provider-2")
        assert s1["sequence_number"] == 1
        assert s2["sequence_number"] == 2

    def test_same_provider_multiple_services_no_duplicate(self, registry):
        _register_service(registry, "provider-1")
        _register_service(registry, "provider-1")
        assert registry.founding_seller_count() == 1

    def test_spots_remaining_decreases(self, registry):
        assert registry.founding_seller_spots_remaining() == 50
        _register_service(registry, "provider-1")
        assert registry.founding_seller_spots_remaining() == 49


class TestFoundingSellerLimit:
    """Only 50 founding seller spots available."""

    def test_51st_provider_not_awarded(self, registry):
        for i in range(50):
            _register_service(registry, f"provider-{i}")
        assert registry.founding_seller_count() == 50
        assert registry.founding_seller_spots_remaining() == 0

        _register_service(registry, "provider-51")
        assert registry.get_founding_seller("provider-51") is None
        assert registry.founding_seller_count() == 50


class TestFoundingSellerList:
    """Listing founding sellers."""

    def test_list_ordered_by_sequence(self, registry):
        for i in range(5):
            _register_service(registry, f"p-{i}")
        sellers = registry.list_founding_sellers()
        assert len(sellers) == 5
        sequences = [s["sequence_number"] for s in sellers]
        assert sequences == [1, 2, 3, 4, 5]

    def test_empty_list(self, registry):
        assert registry.list_founding_sellers() == []


class TestFoundingSellerCommission:
    """Founding Sellers get reduced commission (8% cap)."""

    def test_founding_seller_rate_capped_at_8pct(self, registry, commission, db):
        _register_service(registry, "provider-1")

        # After free trial (month 4+), standard is 10%, founding cap is 8%
        from datetime import timedelta
        reg_date = datetime.now(timezone.utc) - timedelta(days=120)
        # Override registration date for testing
        with db.connect() as conn:
            conn.execute(
                "UPDATE services SET created_at = ? WHERE provider_id = ?",
                (reg_date.isoformat(), "provider-1"),
            )

        rate = commission.get_commission_rate("provider-1")
        assert rate == Decimal("0.08")

    def test_non_founding_gets_standard_10pct(self, db, commission):
        # Manually insert a service without going through registry
        # (bypasses founding seller auto-award)
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        old = now - timedelta(days=120)
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO services
                   (id, provider_id, name, description, endpoint,
                    price_per_call, currency, payment_method, free_tier_calls,
                    status, category, tags, metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), "non-founding", "Test", "", "https://example.com/api",
                    0.5, "USDC", "x402", 0, "active", "", "[]", "{}", old.isoformat(), now.isoformat(),
                ),
            )
        rate = commission.get_commission_rate("non-founding")
        assert rate == Decimal("0.10")

    def test_founding_seller_commission_info(self, registry, commission):
        _register_service(registry, "provider-1")
        info = commission.get_provider_commission_info("provider-1")
        assert info["founding_seller"] is not None
        assert info["founding_seller"]["sequence_number"] == 1
        assert info["current_tier"] == "founding_seller"
