"""Tests for x402 payment integration."""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from marketplace.payment import (
    PaymentConfig,
    build_x402_routes,
    extract_payment_tx,
)


class TestPaymentConfig:
    """Test payment configuration loading."""

    def test_from_env_with_wallet(self, monkeypatch):
        monkeypatch.setenv("WALLET_ADDRESS", "0xABC123")
        monkeypatch.setenv("NETWORK", "eip155:8453")
        monkeypatch.setenv("FACILITATOR_URL", "https://example.com/facilitator")

        config = PaymentConfig.from_env()
        assert config.enabled is True
        assert config.wallet_address == "0xABC123"
        assert config.network == "eip155:8453"
        assert config.facilitator_url == "https://example.com/facilitator"

    def test_from_env_without_wallet(self, monkeypatch):
        monkeypatch.delenv("WALLET_ADDRESS", raising=False)

        config = PaymentConfig.from_env()
        assert config.enabled is False
        assert config.wallet_address == ""

    def test_from_env_defaults(self, monkeypatch):
        monkeypatch.setenv("WALLET_ADDRESS", "0xDEF456")
        monkeypatch.delenv("NETWORK", raising=False)
        monkeypatch.delenv("FACILITATOR_URL", raising=False)

        config = PaymentConfig.from_env()
        assert config.network == "eip155:8453"  # Base Mainnet default
        assert config.facilitator_url == "https://x402.org/facilitator"

    def test_immutable(self):
        config = PaymentConfig(wallet_address="0x123")
        with pytest.raises(AttributeError):
            config.wallet_address = "0x456"


class TestBuildX402Routes:
    """Test route building for x402 middleware."""

    def _mock_x402_modules(self):
        """Create mock x402 modules for testing."""
        mock_http = MagicMock()
        mock_types = MagicMock()
        return {
            "x402": MagicMock(),
            "x402.http": mock_http,
            "x402.http.types": mock_types,
        }

    def test_builds_routes_for_active_x402_services(self):
        services = [
            {
                "id": "svc-1",
                "name": "Test API",
                "description": "A test service",
                "payment_method": "x402",
                "status": "active",
                "price_per_call": "0.05",
            },
            {
                "id": "svc-2",
                "name": "Another API",
                "description": "Another service",
                "payment_method": "both",
                "status": "active",
                "price_per_call": "0.10",
            },
        ]
        mods = self._mock_x402_modules()
        with patch.dict("sys.modules", mods):
            # Re-import to pick up mocked modules
            import importlib
            import marketplace.payment as mp
            importlib.reload(mp)
            routes = mp.build_x402_routes(services, "0xWALLET", "eip155:84532")
            assert len(routes) == 2
            assert "ANY /api/v1/proxy/svc-1" in routes
            assert "ANY /api/v1/proxy/svc-2" in routes

    def test_skips_inactive_services(self):
        services = [
            {
                "id": "svc-1",
                "payment_method": "x402",
                "status": "paused",
                "price_per_call": "0.05",
            },
        ]
        mods = self._mock_x402_modules()
        with patch.dict("sys.modules", mods):
            import importlib
            import marketplace.payment as mp
            importlib.reload(mp)
            routes = mp.build_x402_routes(services, "0xWALLET", "eip155:84532")
            assert len(routes) == 0

    def test_skips_stripe_only_services(self):
        services = [
            {
                "id": "svc-1",
                "payment_method": "stripe",
                "status": "active",
                "price_per_call": "0.05",
            },
        ]
        mods = self._mock_x402_modules()
        with patch.dict("sys.modules", mods):
            import importlib
            import marketplace.payment as mp
            importlib.reload(mp)
            routes = mp.build_x402_routes(services, "0xWALLET", "eip155:84532")
            assert len(routes) == 0

    def test_skips_zero_price_services(self):
        services = [
            {
                "id": "svc-1",
                "payment_method": "x402",
                "status": "active",
                "price_per_call": "0",
            },
        ]
        mods = self._mock_x402_modules()
        with patch.dict("sys.modules", mods):
            import importlib
            import marketplace.payment as mp
            importlib.reload(mp)
            routes = mp.build_x402_routes(services, "0xWALLET", "eip155:84532")
            assert len(routes) == 0

    def test_returns_empty_when_x402_not_installed(self):
        services = [
            {
                "id": "svc-1",
                "payment_method": "x402",
                "status": "active",
                "price_per_call": "0.05",
            },
        ]
        # When x402 is not installed, build_x402_routes returns {}
        with patch.dict("sys.modules", {"x402": None, "x402.http": None, "x402.http.types": None}):
            routes = build_x402_routes(services, "0xWALLET", "eip155:84532")
            assert routes == {}


class TestExtractPaymentTx:
    """Test x402 transaction hash extraction from headers."""

    def test_extracts_tx_lowercase(self):
        headers = {"x-payment-transaction": "0xabc123def456"}
        assert extract_payment_tx(headers) == "0xabc123def456"

    def test_extracts_tx_titlecase(self):
        headers = {"X-Payment-Transaction": "0xabc123def456"}
        assert extract_payment_tx(headers) == "0xabc123def456"

    def test_returns_none_when_missing(self):
        headers = {"content-type": "application/json"}
        assert extract_payment_tx(headers) is None

    def test_returns_none_for_empty_header(self):
        headers = {"x-payment-transaction": ""}
        assert extract_payment_tx(headers) is None

    def test_handles_empty_dict(self):
        assert extract_payment_tx({}) is None
