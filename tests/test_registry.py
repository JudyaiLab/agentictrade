"""Tests for Service Registry."""
import pytest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from marketplace.db import Database
from marketplace.registry import ServiceRegistry, RegistryError


@pytest.fixture
def db():
    with TemporaryDirectory() as tmp:
        yield Database(Path(tmp) / "test.db")


@pytest.fixture
def registry(db):
    return ServiceRegistry(db)


@pytest.fixture
def sample_service(registry):
    return registry.register(
        provider_id="provider-1",
        name="Test AI API",
        description="A test service",
        endpoint="https://api.example.com/v1",
        price_per_call="0.005",
        category="ai",
        tags=["test", "ai"],
    )


class TestRegister:
    def test_register_success(self, registry):
        service = registry.register(
            provider_id="p1",
            name="My API",
            description="desc",
            endpoint="https://api.example.com",
            price_per_call="0.01",
        )
        assert service.name == "My API"
        assert service.pricing.price_per_call == Decimal("0.01")
        assert service.status == "active"

    def test_register_with_all_options(self, registry):
        service = registry.register(
            provider_id="p1",
            name="Full API",
            description="Full options",
            endpoint="https://api.example.com",
            price_per_call="0.05",
            category="data",
            tags=["crypto", "market"],
            payment_method="both",
            free_tier_calls=100,
        )
        assert service.category == "data"
        assert service.tags == ("crypto", "market")
        assert service.pricing.payment_method == "both"
        assert service.pricing.free_tier_calls == 100

    def test_register_missing_name(self, registry):
        with pytest.raises(RegistryError, match="name is required"):
            registry.register(
                provider_id="p1",
                name="",
                description="",
                endpoint="https://api.example.com",
                price_per_call="0.01",
            )

    def test_register_missing_provider(self, registry):
        with pytest.raises(RegistryError, match="Provider ID is required"):
            registry.register(
                provider_id="",
                name="Test",
                description="",
                endpoint="https://api.example.com",
                price_per_call="0.01",
            )

    def test_register_invalid_endpoint_localhost(self, registry):
        with pytest.raises(RegistryError, match="private address"):
            registry.register(
                provider_id="p1",
                name="Test",
                description="",
                endpoint="http://localhost:8000",
                price_per_call="0.01",
            )

    def test_register_invalid_endpoint_no_scheme(self, registry):
        with pytest.raises(RegistryError, match="HTTPS"):
            registry.register(
                provider_id="p1",
                name="Test",
                description="",
                endpoint="ftp://api.example.com",
                price_per_call="0.01",
            )

    def test_register_negative_price(self, registry):
        with pytest.raises(RegistryError, match="negative"):
            registry.register(
                provider_id="p1",
                name="Test",
                description="",
                endpoint="https://api.example.com",
                price_per_call="-1",
            )

    def test_register_excessive_price(self, registry):
        with pytest.raises(RegistryError, match="exceed"):
            registry.register(
                provider_id="p1",
                name="Test",
                description="",
                endpoint="https://api.example.com",
                price_per_call="200",
            )

    def test_register_invalid_payment_method(self, registry):
        with pytest.raises(RegistryError, match="Payment method"):
            registry.register(
                provider_id="p1",
                name="Test",
                description="",
                endpoint="https://api.example.com",
                price_per_call="0.01",
                payment_method="bitcoin",
            )


class TestGet:
    def test_get_existing(self, registry, sample_service):
        found = registry.get(sample_service.id)
        assert found is not None
        assert found.name == "Test AI API"
        assert found.pricing.price_per_call == Decimal("0.005")

    def test_get_nonexistent(self, registry):
        assert registry.get("nonexistent-id") is None


class TestSearch:
    def test_search_all(self, registry, sample_service):
        results = registry.search()
        assert len(results) == 1
        assert results[0].id == sample_service.id

    def test_search_by_query(self, registry, sample_service):
        registry.register(
            provider_id="p2",
            name="Translation API",
            description="Translate text",
            endpoint="https://translate.example.com",
            price_per_call="0.02",
        )
        results = registry.search(query="Translation")
        assert len(results) == 1
        assert results[0].name == "Translation API"

    def test_search_by_category(self, registry, sample_service):
        results = registry.search(category="ai")
        assert len(results) == 1
        results = registry.search(category="data")
        assert len(results) == 0

    def test_search_pagination(self, registry):
        for i in range(5):
            registry.register(
                provider_id="p1",
                name=f"API {i}",
                description="",
                endpoint=f"https://api{i}.example.com",
                price_per_call="0.01",
            )
        page1 = registry.search(limit=3, offset=0)
        page2 = registry.search(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 2


class TestUpdate:
    def test_update_name(self, registry, sample_service):
        updated = registry.update(
            sample_service.id, "provider-1", name="New Name"
        )
        assert updated is not None
        assert updated.name == "New Name"

    def test_update_price(self, registry, sample_service):
        updated = registry.update(
            sample_service.id, "provider-1", price_per_call="0.10"
        )
        assert updated is not None
        assert updated.pricing.price_per_call == Decimal("0.1")

    def test_update_wrong_owner(self, registry, sample_service):
        with pytest.raises(RegistryError, match="owner"):
            registry.update(sample_service.id, "wrong-provider", name="Hack")

    def test_update_nonexistent(self, registry):
        result = registry.update("no-id", "p1", name="Test")
        assert result is None

    def test_update_invalid_status(self, registry, sample_service):
        with pytest.raises(RegistryError, match="Status"):
            registry.update(
                sample_service.id, "provider-1", status="deleted"
            )


class TestRemove:
    def test_remove_success(self, registry, sample_service):
        assert registry.remove(sample_service.id, "provider-1") is True
        # Should not appear in active search
        results = registry.search(status="active")
        assert len(results) == 0

    def test_remove_wrong_owner(self, registry, sample_service):
        with pytest.raises(RegistryError, match="owner"):
            registry.remove(sample_service.id, "wrong-provider")

    def test_remove_nonexistent(self, registry):
        assert registry.remove("no-id", "p1") is False
