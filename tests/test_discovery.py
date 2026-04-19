"""Tests for Enhanced Discovery Engine."""
from __future__ import annotations

import pytest
import tempfile
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from marketplace.db import Database
from marketplace.registry import ServiceRegistry
from marketplace.discovery import DiscoveryEngine


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(Path(tmp) / "test.db")


@pytest.fixture
def registry(db):
    return ServiceRegistry(db)


@pytest.fixture
def discovery(db, registry):
    return DiscoveryEngine(db, registry)


def _register_service(registry, name="TestAPI", category="ai",
                       price="1.00", tags=None, payment_method="x402",
                       free_tier=0):
    return registry.register(
        provider_id="provider-1",
        name=name,
        description=f"{name} service",
        endpoint="https://api.example.com/v1",
        price_per_call=price,
        category=category,
        tags=tags or ["test"],
        payment_method=payment_method,
        free_tier_calls=free_tier,
    )


# --- Search ---

class TestSearch:
    def test_basic_search(self, discovery, registry):
        _register_service(registry, "AlphaAPI")
        _register_service(registry, "BetaAPI")
        result = discovery.search(query="Alpha")
        assert result["total"] == 1
        assert result["services"][0].name == "AlphaAPI"

    def test_search_by_category(self, discovery, registry):
        _register_service(registry, "AI-1", category="ai")
        _register_service(registry, "Data-1", category="data")
        result = discovery.search(category="ai")
        assert result["total"] == 1

    def test_search_by_tags(self, discovery, registry):
        _register_service(registry, "Tagged", tags=["ml", "nlp"])
        _register_service(registry, "Other", tags=["web"])
        result = discovery.search(tags=["ml"])
        assert result["total"] == 1
        assert result["services"][0].name == "Tagged"

    def test_search_by_price_range(self, discovery, registry):
        _register_service(registry, "Cheap", price="0.50")
        _register_service(registry, "Mid", price="5.00")
        _register_service(registry, "Expensive", price="50.00")
        result = discovery.search(min_price="1.00", max_price="10.00")
        assert result["total"] == 1
        assert result["services"][0].name == "Mid"

    def test_search_by_payment_method(self, discovery, registry):
        _register_service(registry, "X402Only", payment_method="x402")
        _register_service(registry, "StripeOnly", payment_method="stripe")
        _register_service(registry, "Both", payment_method="both")
        result = discovery.search(payment_method="stripe")
        assert result["total"] == 2  # StripeOnly + Both

    def test_search_free_tier_only(self, discovery, registry):
        _register_service(registry, "Free", free_tier=100)
        _register_service(registry, "Paid", free_tier=0)
        result = discovery.search(has_free_tier=True)
        assert result["total"] == 1
        assert result["services"][0].name == "Free"

    def test_search_sort_by_price(self, discovery, registry):
        _register_service(registry, "Expensive", price="10.00")
        _register_service(registry, "Cheap", price="0.10")
        _register_service(registry, "Mid", price="5.00")
        result = discovery.search(sort_by="price")
        names = [s.name for s in result["services"]]
        assert names == ["Cheap", "Mid", "Expensive"]

    def test_search_sort_by_name(self, discovery, registry):
        _register_service(registry, "Charlie")
        _register_service(registry, "Alpha")
        _register_service(registry, "Bravo")
        result = discovery.search(sort_by="name")
        names = [s.name for s in result["services"]]
        assert names == ["Alpha", "Bravo", "Charlie"]

    def test_search_pagination(self, discovery, registry):
        for i in range(5):
            _register_service(registry, f"API-{i}")
        result = discovery.search(limit=2, offset=0)
        assert result["total"] == 5
        assert len(result["services"]) == 2

        result2 = discovery.search(limit=2, offset=2)
        assert len(result2["services"]) == 2

    def test_search_no_results(self, discovery):
        result = discovery.search(query="nonexistent")
        assert result["total"] == 0
        assert result["services"] == []


# --- Categories ---

class TestCategories:
    def test_empty_categories(self, discovery):
        cats = discovery.get_categories()
        assert cats == []

    def test_multiple_categories(self, discovery, registry):
        _register_service(registry, "AI-1", category="ai")
        _register_service(registry, "AI-2", category="ai")
        _register_service(registry, "Data-1", category="data")
        cats = discovery.get_categories()
        assert len(cats) == 2
        assert cats[0] == {"category": "ai", "count": 2}
        assert cats[1] == {"category": "data", "count": 1}

    def test_uncategorized(self, discovery, registry):
        _register_service(registry, "NoCategory", category="")
        cats = discovery.get_categories()
        assert cats[0]["category"] == "uncategorized"


# --- Trending ---

class TestTrending:
    def test_empty_trending(self, discovery):
        trending = discovery.get_trending()
        assert trending == []

    def test_trending_by_usage(self, discovery, db, registry):
        svc = _register_service(registry, "PopularAPI")
        now = datetime.now(timezone.utc).isoformat()
        for _ in range(5):
            db.insert_usage({
                "id": str(uuid.uuid4()),
                "buyer_id": "buyer-1",
                "service_id": svc.id,
                "provider_id": "provider-1",
                "timestamp": now,
                "latency_ms": 100,
                "status_code": 200,
                "amount_usd": 0.01,
            })
        trending = discovery.get_trending()
        assert len(trending) == 1
        assert trending[0]["call_count"] == 5


# --- Recommendations ---

class TestRecommendations:
    def test_no_history_returns_newest(self, discovery, registry):
        _register_service(registry, "NewAPI")
        recs = discovery.get_recommendations("new-agent")
        assert len(recs) >= 1

    def test_recommendations_based_on_usage(self, discovery, db, registry):
        svc1 = _register_service(registry, "AI-Service-1", category="ai")
        svc2 = _register_service(registry, "AI-Service-2", category="ai")

        now = datetime.now(timezone.utc).isoformat()
        # Agent used svc1
        db.insert_usage({
            "id": str(uuid.uuid4()),
            "buyer_id": "agent-1",
            "service_id": svc1.id,
            "provider_id": "provider-1",
            "timestamp": now,
            "latency_ms": 100,
            "status_code": 200,
            "amount_usd": 0.01,
        })

        recs = discovery.get_recommendations("agent-1")
        # Should recommend svc2 (same category, not yet used)
        rec_ids = [r.id for r in recs]
        assert svc2.id in rec_ids
        assert svc1.id not in rec_ids


# --- Quality Signals ---

class TestQualitySignals:
    """Tests for quality signal helpers used in /discover endpoint."""

    def test_empty_quality(self):
        from api.routes.discovery import _empty_quality
        q = _empty_quality()
        assert q["health_score"] is None
        assert q["uptime_pct"] is None
        assert q["sla_tier"] == "basic"
        assert q["quality_tier"] == "Standard"

    def test_build_quality_map_no_services(self, db):
        from api.routes.discovery import _build_quality_map
        result = _build_quality_map(db, [])
        assert result == {}

    def test_build_quality_map_no_health_data(self, db, registry):
        from api.routes.discovery import _build_quality_map
        svc = _register_service(registry, "NoHealthData")
        result = _build_quality_map(db, [svc.id])
        assert svc.id in result
        assert result[svc.id]["health_score"] is None
        assert result[svc.id]["sla_tier"] == "basic"

    def test_build_quality_map_with_health_data(self, db, registry):
        from api.routes.discovery import _build_quality_map
        from marketplace.health_monitor import HealthMonitor
        svc = _register_service(registry, "HealthyAPI")

        # Ensure health_checks table exists
        HealthMonitor(db)
        now = datetime.now(timezone.utc).isoformat()
        with db.connect() as conn:
            for i in range(5):
                conn.execute(
                    "INSERT INTO health_checks "
                    "(id, service_id, provider_id, reachable, latency_ms, status_code, checked_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), svc.id, "provider-1", 1, 50, 200, now),
                )

        result = _build_quality_map(db, [svc.id])
        assert svc.id in result
        assert result[svc.id]["health_score"] is not None
        assert result[svc.id]["health_score"] > 0
        assert result[svc.id]["uptime_pct"] == 100.0

    def test_quality_tier_thresholds(self, db, registry):
        """Verify Premium/Verified/Standard tier assignment."""
        from api.routes.discovery import _build_quality_map
        from marketplace.health_monitor import HealthMonitor
        svc = _register_service(registry, "PremiumAPI")

        # Ensure health_checks table exists
        HealthMonitor(db)
        now = datetime.now(timezone.utc).isoformat()
        with db.connect() as conn:
            for i in range(20):
                conn.execute(
                    "INSERT INTO health_checks "
                    "(id, service_id, provider_id, reachable, latency_ms, status_code, checked_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), svc.id, "provider-1", 1, 10, 200, now),
                )

        result = _build_quality_map(db, [svc.id])
        assert result[svc.id]["quality_tier"] == "Premium"
