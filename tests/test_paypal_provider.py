"""
Tests for PayPal payment provider.

Covers:
- Provider name and supported currencies
- Create payment with mocked PayPal API
- Currency validation (unsupported currencies rejected)
- Amount validation (zero and negative amounts rejected)
- Verify payment
- Get payment
- Missing credentials handling
- Payment ID prefix format
- Environment variable key loading
- PaymentRouter integration
- Error propagation from PayPal API
- Status mapping
- OAuth2 token caching
"""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from payments.base import (
    PaymentProvider,
    PaymentProviderError,
    PaymentResult,
    PaymentStatus,
)
from payments.paypal_provider import (
    PayPalProvider,
    _PAYPAL_STATUS_MAP,
)
from payments.router import PaymentRouter


# ─── Helpers ───


def _make_provider(
    client_id: str = "test_client_id",
    client_secret: str = "test_client_secret",
    mode: str = "sandbox",
) -> PayPalProvider:
    """Create a PayPalProvider with test credentials."""
    return PayPalProvider(client_id=client_id, client_secret=client_secret, mode=mode)


def _mock_token_response() -> MagicMock:
    """Build a mock token response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"access_token": "test_token_abc", "expires_in": 3600}
    resp.raise_for_status = MagicMock()
    return resp


def _mock_order_response(order_id: str = "ORDER-123", status: str = "CREATED") -> MagicMock:
    """Build a mock create order response."""
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {
        "id": order_id,
        "status": status,
        "links": [
            {"rel": "self", "href": f"https://api-m.sandbox.paypal.com/v2/checkout/orders/{order_id}"},
            {"rel": "payer-action", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}"},
        ],
    }
    resp.raise_for_status = MagicMock()
    return resp


def _mock_get_order_response(order_id: str = "ORDER-123", status: str = "COMPLETED") -> MagicMock:
    """Build a mock get order response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "id": order_id,
        "status": status,
        "purchase_units": [{"amount": {"currency_code": "USD", "value": "29.99"}}],
    }
    resp.raise_for_status = MagicMock()
    return resp


# ─── Provider identity ───


class TestProviderIdentity:
    def test_provider_name(self):
        provider = _make_provider()
        assert provider.provider_name == "paypal"

    def test_supported_currencies(self):
        provider = _make_provider()
        currencies = provider.supported_currencies
        assert "USD" in currencies
        assert "EUR" in currencies
        assert "GBP" in currencies
        assert "JPY" in currencies

    def test_supported_currencies_returns_copy(self):
        provider = _make_provider()
        c1 = provider.supported_currencies
        c2 = provider.supported_currencies
        assert c1 == c2
        assert c1 is not c2

    def test_is_payment_provider_subclass(self):
        assert issubclass(PayPalProvider, PaymentProvider)


# ─── Create payment ───


class TestCreatePayment:
    @pytest.mark.asyncio
    async def test_create_payment_success(self):
        """Mocked PayPal API returns an order; provider wraps it."""
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            mock_httpx.post.side_effect = [_mock_token_response(), _mock_order_response()]
            result = await provider.create_payment(
                amount=Decimal("29.99"),
                currency="USD",
                metadata={"description": "API access"},
            )

        assert result.payment_id == "paypal_ORDER-123"
        assert result.status == PaymentStatus.pending
        assert result.amount == Decimal("29.99")
        assert result.currency == "USD"
        assert "sandbox.paypal.com" in (result.checkout_url or "")
        assert result.metadata["paypal_order_id"] == "ORDER-123"
        assert result.metadata["description"] == "API access"
        assert isinstance(result, PaymentResult)

    @pytest.mark.asyncio
    async def test_create_payment_completed_status(self):
        """PayPal returns COMPLETED status -> PaymentStatus.completed."""
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            mock_httpx.post.side_effect = [
                _mock_token_response(),
                _mock_order_response(status="COMPLETED"),
            ]
            result = await provider.create_payment(
                amount=Decimal("10.00"),
                currency="EUR",
                metadata={"description": "Test"},
            )

        assert result.status == PaymentStatus.completed

    @pytest.mark.asyncio
    async def test_create_payment_case_insensitive_currency(self):
        """Lower-case currency is accepted and uppercased."""
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            mock_httpx.post.side_effect = [_mock_token_response(), _mock_order_response()]
            result = await provider.create_payment(
                amount=Decimal("5.00"),
                currency="usd",
                metadata={"description": "test"},
            )

        assert result.currency == "USD"

    @pytest.mark.asyncio
    async def test_create_payment_id_prefix(self):
        """Payment ID starts with 'paypal_'."""
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            mock_httpx.post.side_effect = [
                _mock_token_response(),
                _mock_order_response(order_id="ORDER-XYZ"),
            ]
            result = await provider.create_payment(
                amount=Decimal("1.00"),
                currency="USD",
                metadata={"description": "test"},
            )

        assert result.payment_id.startswith("paypal_")

    @pytest.mark.asyncio
    async def test_create_payment_amount_formatted_two_decimals(self):
        """PayPal receives amount as string with 2 decimal places."""
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            mock_httpx.post.side_effect = [_mock_token_response(), _mock_order_response()]
            await provider.create_payment(
                amount=Decimal("42.5"),
                currency="GBP",
                metadata={"description": "widget"},
            )

        # Second call is the order creation
        order_call = mock_httpx.post.call_args_list[1]
        order_body = order_call.kwargs.get("json") or order_call[1].get("json")
        amount_value = order_body["purchase_units"][0]["amount"]["value"]
        assert amount_value == "42.50"


# ─── Validation ───


class TestValidation:
    @pytest.mark.asyncio
    async def test_unsupported_currency_raises(self):
        provider = _make_provider()

        with pytest.raises(PaymentProviderError, match="Unsupported currency"):
            await provider.create_payment(
                amount=Decimal("10.00"),
                currency="BTC",
                metadata={"description": "test"},
            )

    @pytest.mark.asyncio
    async def test_zero_amount_raises(self):
        provider = _make_provider()

        with pytest.raises(PaymentProviderError, match="positive"):
            await provider.create_payment(
                amount=Decimal("0"),
                currency="USD",
                metadata={"description": "test"},
            )

    @pytest.mark.asyncio
    async def test_negative_amount_raises(self):
        provider = _make_provider()

        with pytest.raises(PaymentProviderError, match="positive"):
            await provider.create_payment(
                amount=Decimal("-5.00"),
                currency="USD",
                metadata={"description": "test"},
            )

    @pytest.mark.asyncio
    async def test_no_credentials_raises(self):
        provider = PayPalProvider(client_id="", client_secret="")

        with pytest.raises(PaymentProviderError, match="PAYPAL_CLIENT_ID"):
            await provider.create_payment(
                amount=Decimal("10.00"),
                currency="USD",
                metadata={"description": "test"},
            )


# ─── Verify payment ───


class TestVerifyPayment:
    @pytest.mark.asyncio
    async def test_verify_payment_success(self):
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = _mock_token_response()
            mock_httpx.get.return_value = _mock_get_order_response(status="COMPLETED")
            status = await provider.verify_payment("paypal_ORDER-123")

        assert status == PaymentStatus.completed

    @pytest.mark.asyncio
    async def test_verify_payment_pending(self):
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = _mock_token_response()
            mock_httpx.get.return_value = _mock_get_order_response(status="CREATED")
            status = await provider.verify_payment("paypal_ORDER-456")

        assert status == PaymentStatus.pending

    @pytest.mark.asyncio
    async def test_verify_payment_empty_id_raises(self):
        provider = _make_provider()

        with pytest.raises(PaymentProviderError, match="required"):
            await provider.verify_payment("")

    @pytest.mark.asyncio
    async def test_verify_payment_strips_prefix(self):
        """The paypal_ prefix is removed before calling PayPal."""
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = _mock_token_response()
            mock_httpx.get.return_value = _mock_get_order_response()
            await provider.verify_payment("paypal_ORDER-STRIP")

        get_call = mock_httpx.get.call_args
        assert "ORDER-STRIP" in str(get_call)

    @pytest.mark.asyncio
    async def test_verify_payment_api_error(self):
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = _mock_token_response()
            mock_httpx.get.side_effect = Exception("Not found")
            with pytest.raises(PaymentProviderError, match="PayPal verify error"):
                await provider.verify_payment("paypal_ORDER-BAD")


# ─── Get payment ───


class TestGetPayment:
    @pytest.mark.asyncio
    async def test_get_payment_success(self):
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = _mock_token_response()
            mock_httpx.get.return_value = _mock_get_order_response()
            data = await provider.get_payment("paypal_ORDER-GET")

        assert data["payment_id"] == "paypal_ORDER-GET"
        assert data["provider"] == "paypal"
        assert data["status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_get_payment_empty_id_raises(self):
        provider = _make_provider()

        with pytest.raises(PaymentProviderError, match="required"):
            await provider.get_payment("")

    @pytest.mark.asyncio
    async def test_get_payment_api_error(self):
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            mock_httpx.post.return_value = _mock_token_response()
            mock_httpx.get.side_effect = Exception("API down")
            with pytest.raises(PaymentProviderError, match="PayPal get_payment error"):
                await provider.get_payment("paypal_ORDER-ERR")


# ─── Env var loading ───


class TestEnvVarLoading:
    def test_loads_credentials_from_env(self, monkeypatch):
        monkeypatch.setenv("PAYPAL_CLIENT_ID", "env_client_id")
        monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "env_client_secret")
        provider = PayPalProvider()
        assert provider._client_id == "env_client_id"
        assert provider._client_secret == "env_client_secret"

    def test_constructor_overrides_env(self, monkeypatch):
        monkeypatch.setenv("PAYPAL_CLIENT_ID", "env_id")
        provider = PayPalProvider(client_id="explicit_id", client_secret="explicit_secret")
        assert provider._client_id == "explicit_id"

    def test_no_credentials_results_in_empty_string(self, monkeypatch):
        monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
        monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
        provider = PayPalProvider()
        assert provider._client_id == ""
        assert provider._client_secret == ""

    def test_mode_defaults_to_sandbox(self):
        provider = _make_provider(mode="invalid")
        assert provider._mode == "sandbox"
        assert "sandbox" in provider._base_url

    def test_live_mode(self):
        provider = _make_provider(mode="live")
        assert provider._mode == "live"
        assert "sandbox" not in provider._base_url


# ─── Status map ───


class TestStatusMap:
    def test_created_maps_to_pending(self):
        assert _PAYPAL_STATUS_MAP["CREATED"] == PaymentStatus.pending

    def test_completed_maps_to_completed(self):
        assert _PAYPAL_STATUS_MAP["COMPLETED"] == PaymentStatus.completed

    def test_voided_maps_to_failed(self):
        assert _PAYPAL_STATUS_MAP["VOIDED"] == PaymentStatus.failed

    def test_approved_maps_to_pending(self):
        assert _PAYPAL_STATUS_MAP["APPROVED"] == PaymentStatus.pending


# ─── PaymentRouter integration ───


class TestRouterIntegration:
    def test_paypal_in_router(self):
        provider = _make_provider()
        router = PaymentRouter({"paypal": provider})
        assert "paypal" in router
        resolved = router.route("paypal")
        assert resolved is provider
        assert resolved.provider_name == "paypal"

    def test_router_case_insensitive(self):
        provider = _make_provider()
        router = PaymentRouter({"paypal": provider})
        resolved = router.route("PAYPAL")
        assert resolved is provider

    def test_router_lists_paypal(self):
        provider = _make_provider()
        router = PaymentRouter({"paypal": provider, "x402": _make_provider()})
        providers = router.list_providers()
        assert "paypal" in providers


# ─── OAuth2 token caching ───


class TestTokenCaching:
    @pytest.mark.asyncio
    async def test_token_cached_between_calls(self):
        """Second call reuses cached token, no extra auth request."""
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            mock_httpx.post.side_effect = [
                _mock_token_response(),
                _mock_order_response(order_id="ORDER-1"),
                # No second token response — should use cache
                _mock_order_response(order_id="ORDER-2"),
            ]
            await provider.create_payment(
                amount=Decimal("10.00"), currency="USD", metadata={"description": "first"},
            )
            await provider.create_payment(
                amount=Decimal("20.00"), currency="USD", metadata={"description": "second"},
            )

        # 1 token + 2 orders = 3 post calls (not 2 tokens + 2 orders = 4)
        assert mock_httpx.post.call_count == 3


# ─── API error propagation ───


class TestAPIErrorPropagation:
    @pytest.mark.asyncio
    async def test_create_payment_api_error_wrapped(self):
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            mock_httpx.post.side_effect = Exception("Connection refused")
            with pytest.raises(PaymentProviderError, match="PayPal"):
                await provider.create_payment(
                    amount=Decimal("10.00"),
                    currency="USD",
                    metadata={"description": "test"},
                )

    @pytest.mark.asyncio
    async def test_oauth_error_wrapped(self):
        """Auth failure is wrapped as PaymentProviderError."""
        provider = _make_provider()

        with patch("payments.paypal_provider.httpx") as mock_httpx:
            error_resp = MagicMock()
            error_resp.raise_for_status.side_effect = Exception("401 Unauthorized")
            mock_httpx.post.return_value = error_resp
            with pytest.raises(PaymentProviderError, match="PayPal"):
                await provider.create_payment(
                    amount=Decimal("10.00"),
                    currency="USD",
                    metadata={"description": "test"},
                )
