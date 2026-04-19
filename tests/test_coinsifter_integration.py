"""Integration test — CoinSifter as a paid service on the marketplace.

Tests the complete flow:
1. Service registration (CoinSifter at internal endpoint)
2. Buyer authentication
3. Free tier usage
4. Paid tier proxy forwarding
5. Usage recording
"""
from __future__ import annotations

import os
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

# Allow internal hosts for test
os.environ.setdefault("ACF_INTERNAL_HOSTS", "172.18.0.1,127.0.0.1")

from marketplace.db import Database
from marketplace.registry import ServiceRegistry
from marketplace.auth import APIKeyManager
from marketplace.proxy import PaymentProxy, BillingInfo

import marketplace.proxy as _proxy_mod
import marketplace.registry as _registry_mod


@pytest.fixture(autouse=True)
def _allow_internal_hosts():
    """Ensure internal hosts bypass SSRF for all tests in this module."""
    _proxy_mod._INTERNAL_ALLOWED.update({"172.18.0.1", "127.0.0.1"})
    _registry_mod._INTERNAL_ALLOWED.update({"172.18.0.1", "127.0.0.1"})
    yield


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def registry(db):
    return ServiceRegistry(db)


@pytest.fixture
def key_mgr(db):
    return APIKeyManager(db)


@pytest.fixture
def proxy(db):
    return PaymentProxy(db, platform_fee_pct=Decimal("0.10"))


class TestCoinSifterRegistration:
    """Test CoinSifter can be registered as an internal service."""

    def test_register_coinsifter_scan(self, registry):
        svc = registry.register(
            provider_id="judyailab",
            name="CoinSifter — Crypto Market Scanner",
            description="Scan Binance pairs with 8 technical indicators.",
            endpoint="http://172.18.0.1:8089",
            price_per_call="0.50",
            category="crypto-analysis",
            tags=["crypto", "scanner"],
            payment_method="nowpayments",
            free_tier_calls=5,
        )
        assert svc.id
        assert svc.name == "CoinSifter — Crypto Market Scanner"
        assert svc.pricing.price_per_call == Decimal("0.50")
        assert svc.pricing.free_tier_calls == 5
        assert svc.status == "active"
        assert svc.endpoint == "http://172.18.0.1:8089"

    def test_register_coinsifter_demo(self, registry):
        svc = registry.register(
            provider_id="judyailab",
            name="CoinSifter Demo",
            description="Free demo endpoint.",
            endpoint="http://172.18.0.1:8089",
            price_per_call="0",
            category="crypto-analysis",
            payment_method="nowpayments",
        )
        assert svc.pricing.price_per_call == Decimal("0")

    def test_search_by_category(self, registry):
        registry.register(
            provider_id="judyailab",
            name="CoinSifter Test",
            description="Test",
            endpoint="http://172.18.0.1:8089",
            price_per_call="0.10",
            category="crypto-analysis",
        )
        results = registry.search(category="crypto-analysis")
        assert len(results) >= 1
        assert any("CoinSifter" in s.name for s in results)


class TestCoinSifterProxyFlow:
    """Test the buyer → proxy → CoinSifter flow."""

    def _make_coinsifter_service(self, registry) -> dict:
        svc = registry.register(
            provider_id="judyailab",
            name="CoinSifter Scan",
            description="Scan service",
            endpoint="http://172.18.0.1:8089",
            price_per_call="0.50",
            payment_method="nowpayments",
            free_tier_calls=3,
        )
        return {
            "id": svc.id,
            "provider_id": svc.provider_id,
            "endpoint": svc.endpoint,
            "price_per_call": str(svc.pricing.price_per_call),
            "payment_method": svc.pricing.payment_method,
            "free_tier_calls": svc.pricing.free_tier_calls,
            "status": svc.status,
        }

    @pytest.mark.asyncio
    async def test_free_tier_call(self, proxy, registry):
        """First calls should be free (within free tier)."""
        service = self._make_coinsifter_service(registry)

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = '{"status": "scanning", "progress": 0}'
        mock_response.headers = {"content-type": "application/json"}

        with patch("marketplace.proxy.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await proxy.forward_request(
                service=service,
                buyer_id="test-buyer",
                method="POST",
                path="/api/scan",
                body=b'{"top_n": 50}',
                headers={"content-type": "application/json"},
            )

        assert result.status_code == 200
        assert result.billing.amount == Decimal("0")
        assert result.billing.free_tier is True

    @pytest.mark.asyncio
    async def test_paid_call_after_free_tier(self, proxy, registry, db):
        """After free tier exhausted, calls should be charged."""
        service = self._make_coinsifter_service(registry)

        # Fund buyer balance so paid call succeeds
        db.credit_balance("test-buyer", Decimal("5.00"))

        # Simulate 3 previous free tier calls by inserting usage records
        for i in range(3):
            db.insert_usage({
                "id": f"usage-{i}",
                "service_id": service["id"],
                "buyer_id": "test-buyer",
                "provider_id": "judyailab",
                "timestamp": "2026-03-20T00:00:00Z",
                "latency_ms": 100,
                "status_code": 200,
                "amount": "0",
                "platform_fee": "0",
                "provider_amount": "0",
                "payment_method": "free_tier",
            })

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = '{"results": []}'
        mock_response.headers = {"content-type": "application/json"}

        with patch("marketplace.proxy.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await proxy.forward_request(
                service=service,
                buyer_id="test-buyer",
                method="POST",
                path="/api/scan",
                body=b'{"top_n": 50}',
                headers={"content-type": "application/json"},
            )

        assert result.status_code == 200
        assert result.billing.amount == Decimal("0.50")
        assert result.billing.free_tier is False

    @pytest.mark.asyncio
    async def test_proxy_forwards_scan_path(self, proxy, registry):
        """Verify proxy forwards to correct CoinSifter endpoint path."""
        service = self._make_coinsifter_service(registry)

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = '{"scan_id": "abc123"}'
        mock_response.headers = {}

        with patch("marketplace.proxy.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await proxy.forward_request(
                service=service,
                buyer_id="test-buyer",
                method="POST",
                path="/api/scan",
                body=b'{"top_n": 50}',
            )

            # Verify the request was forwarded to the right URL
            call_args = mock_client.request.call_args
            url = call_args.kwargs.get("url", call_args.args[1] if len(call_args.args) > 1 else "")
            assert "172.18.0.1:8089" in str(url)
            assert "/api/scan" in str(url)


class TestCoinSifterAuth:
    """Test authentication for CoinSifter access."""

    def test_create_buyer_key(self, key_mgr):
        key_id, secret = key_mgr.create_key(
            owner_id="agent-buyer-1",
            role="buyer",
        )
        assert key_id.startswith("acf_")
        assert len(secret) > 20

    def test_authenticate_buyer(self, key_mgr):
        key_id, secret = key_mgr.create_key(
            owner_id="agent-buyer-1",
            role="buyer",
        )
        result = key_mgr.validate(key_id, secret)
        assert result is not None
        assert result["owner_id"] == "agent-buyer-1"

    def test_wrong_secret_rejected(self, key_mgr):
        from marketplace.auth import AuthError
        key_id, _ = key_mgr.create_key(
            owner_id="agent-buyer-1",
            role="buyer",
        )
        with pytest.raises(AuthError):
            key_mgr.validate(key_id, "wrong-secret")
