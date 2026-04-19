"""
Tests for Phase 2 Payment Provider Abstraction Layer.

Covers:
- PaymentProvider ABC enforcement
- PaymentResult immutability and construction
- PaymentStatus enum
- X402Provider wrapping existing marketplace/payment.py
- NOWPaymentsProvider (mock httpx responses)
- PaymentRouter routing logic
- Error handling across all components
"""
from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from marketplace.payment import PaymentConfig

from payments.base import (
    PaymentProvider,
    PaymentProviderError,
    PaymentResult,
    PaymentStatus,
)
from payments.nowpayments_provider import NOWPaymentsProvider, _STATUS_MAP
from payments.router import PaymentRouter
from payments.x402_provider import X402Provider


# ─── PaymentStatus enum ───


class TestPaymentStatus:
    def test_all_values_exist(self):
        assert PaymentStatus.pending.value == "pending"
        assert PaymentStatus.completed.value == "completed"
        assert PaymentStatus.failed.value == "failed"
        assert PaymentStatus.expired.value == "expired"

    def test_enum_count(self):
        assert len(PaymentStatus) == 4


# ─── PaymentResult immutability ───


class TestPaymentResult:
    def test_creation(self):
        result = PaymentResult(
            payment_id="pay_123",
            status=PaymentStatus.pending,
            amount=Decimal("9.99"),
            currency="USD",
            checkout_url="https://pay.example.com/123",
            metadata={"order_id": "ord-1"},
        )
        assert result.payment_id == "pay_123"
        assert result.status == PaymentStatus.pending
        assert result.amount == Decimal("9.99")
        assert result.currency == "USD"
        assert result.checkout_url == "https://pay.example.com/123"
        assert result.metadata == {"order_id": "ord-1"}

    def test_frozen_cannot_mutate_payment_id(self):
        result = PaymentResult(
            payment_id="pay_123",
            status=PaymentStatus.pending,
            amount=Decimal("1.00"),
            currency="USD",
        )
        with pytest.raises(AttributeError):
            result.payment_id = "pay_456"

    def test_frozen_cannot_mutate_status(self):
        result = PaymentResult(
            payment_id="pay_123",
            status=PaymentStatus.pending,
            amount=Decimal("1.00"),
            currency="USD",
        )
        with pytest.raises(AttributeError):
            result.status = PaymentStatus.completed

    def test_frozen_cannot_mutate_amount(self):
        result = PaymentResult(
            payment_id="pay_123",
            status=PaymentStatus.pending,
            amount=Decimal("1.00"),
            currency="USD",
        )
        with pytest.raises(AttributeError):
            result.amount = Decimal("2.00")

    def test_defaults(self):
        result = PaymentResult(
            payment_id="pay_123",
            status=PaymentStatus.pending,
            amount=Decimal("5.00"),
            currency="USDC",
        )
        assert result.checkout_url is None
        assert result.metadata == {}


# ─── PaymentProvider ABC enforcement ───


class TestPaymentProviderABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError, match="abstract"):
            PaymentProvider()

    def test_incomplete_subclass_raises(self):
        class IncompleteProvider(PaymentProvider):
            pass

        with pytest.raises(TypeError, match="abstract"):
            IncompleteProvider()

    def test_partial_implementation_raises(self):
        class PartialProvider(PaymentProvider):
            async def create_payment(self, amount, currency, metadata):
                pass

            @property
            def provider_name(self):
                return "partial"

        with pytest.raises(TypeError, match="abstract"):
            PartialProvider()

    def test_complete_subclass_works(self):
        class CompleteProvider(PaymentProvider):
            async def create_payment(self, amount, currency, metadata):
                return PaymentResult(
                    payment_id="test",
                    status=PaymentStatus.pending,
                    amount=amount,
                    currency=currency,
                )

            async def verify_payment(self, payment_id):
                return PaymentStatus.pending

            async def get_payment(self, payment_id):
                return {"payment_id": payment_id}

            @property
            def provider_name(self):
                return "test"

            @property
            def supported_currencies(self):
                return ["USD"]

        provider = CompleteProvider()
        assert provider.provider_name == "test"
        assert provider.supported_currencies == ["USD"]


# ─── X402Provider ───


class TestX402Provider:
    @pytest.fixture
    def config(self):
        return PaymentConfig(
            wallet_address="0xTestWallet123",
            network="eip155:84532",
            facilitator_url="https://x402.org/facilitator",
            enabled=True,
        )

    @pytest.fixture
    def provider(self, config):
        return X402Provider(config=config)

    def test_provider_name(self, provider):
        assert provider.provider_name == "x402"

    def test_supported_currencies(self, provider):
        assert provider.supported_currencies == ["USDC"]

    def test_config_access(self, provider, config):
        assert provider.config is config

    @pytest.mark.asyncio
    async def test_create_payment_success(self, provider):
        result = await provider.create_payment(
            amount=Decimal("0.05"),
            currency="USDC",
            metadata={"service_id": "svc-1", "buyer_id": "buyer-1"},
        )
        assert result.payment_id.startswith("x402_")
        assert result.status == PaymentStatus.pending
        assert result.amount == Decimal("0.05")
        assert result.currency == "USDC"
        assert result.metadata["network"] == "eip155:84532"
        assert result.metadata["wallet_address"] == "0xTestWallet123"
        assert result.metadata["service_id"] == "svc-1"

    @pytest.mark.asyncio
    async def test_create_payment_case_insensitive_currency(self, provider):
        result = await provider.create_payment(
            amount=Decimal("1.00"),
            currency="usdc",
            metadata={"service_id": "svc-1"},
        )
        assert result.currency == "USDC"

    @pytest.mark.asyncio
    async def test_create_payment_disabled_provider(self):
        config = PaymentConfig(wallet_address="", enabled=False)
        provider = X402Provider(config=config)
        with pytest.raises(PaymentProviderError, match="disabled"):
            await provider.create_payment(
                amount=Decimal("1.00"),
                currency="USDC",
                metadata={},
            )

    @pytest.mark.asyncio
    async def test_create_payment_unsupported_currency(self, provider):
        with pytest.raises(PaymentProviderError, match="only supports"):
            await provider.create_payment(
                amount=Decimal("1.00"),
                currency="BTC",
                metadata={},
            )

    @pytest.mark.asyncio
    async def test_create_payment_zero_amount(self, provider):
        with pytest.raises(PaymentProviderError, match="positive"):
            await provider.create_payment(
                amount=Decimal("0"),
                currency="USDC",
                metadata={},
            )

    @pytest.mark.asyncio
    async def test_create_payment_negative_amount(self, provider):
        with pytest.raises(PaymentProviderError, match="positive"):
            await provider.create_payment(
                amount=Decimal("-5.00"),
                currency="USDC",
                metadata={},
            )

    @pytest.mark.asyncio
    async def test_verify_payment_returns_pending(self, provider):
        status = await provider.verify_payment("x402_abc123")
        assert status == PaymentStatus.pending

    @pytest.mark.asyncio
    async def test_verify_payment_empty_id_raises(self, provider):
        with pytest.raises(PaymentProviderError, match="required"):
            await provider.verify_payment("")

    @pytest.mark.asyncio
    async def test_get_payment(self, provider):
        data = await provider.get_payment("x402_abc123")
        assert data["payment_id"] == "x402_abc123"
        assert data["provider"] == "x402"
        assert data["network"] == "eip155:84532"
        assert data["enabled"] is True

    @pytest.mark.asyncio
    async def test_get_payment_empty_id_raises(self, provider):
        with pytest.raises(PaymentProviderError, match="required"):
            await provider.get_payment("")

    def test_extract_tx_from_headers(self, provider):
        headers = {"x-payment-transaction": "0xdeadbeef"}
        assert provider.extract_tx_from_headers(headers) == "0xdeadbeef"

    def test_extract_tx_from_headers_missing(self, provider):
        assert provider.extract_tx_from_headers({}) is None

    def test_from_env_fallback(self, monkeypatch):
        monkeypatch.setenv("WALLET_ADDRESS", "0xEnvWallet")
        provider = X402Provider()
        assert provider.config.wallet_address == "0xEnvWallet"
        assert provider.config.enabled is True


# ─── NOWPaymentsProvider ───


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    """Create a mock httpx.Response (json/text are sync on real httpx)."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = json.dumps(json_data or {})
    return resp


class TestNOWPaymentsProvider:
    @pytest.fixture
    def mock_client(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        return client

    @pytest.fixture
    def provider(self, mock_client):
        return NOWPaymentsProvider(
            api_key="test-api-key",
            ipn_secret="test-ipn-secret",
            http_client=mock_client,
        )

    def test_provider_name(self, provider):
        assert provider.provider_name == "nowpayments"

    def test_supported_currencies(self, provider):
        currencies = provider.supported_currencies
        assert "USD" in currencies
        assert "USDT" in currencies
        assert "BTC" in currencies

    @pytest.mark.asyncio
    async def test_create_payment_success(self, provider, mock_client):
        mock_client.request.return_value = _mock_response(200, {
            "payment_id": 12345,
            "payment_status": "waiting",
            "pay_address": "TRX123abc",
            "pay_amount": 9.99,
            "invoice_url": "https://nowpayments.io/payment/?iid=12345",
        })

        result = await provider.create_payment(
            amount=Decimal("9.99"),
            currency="USD",
            metadata={"order_id": "ord-001", "description": "Test service"},
        )

        assert result.payment_id == "12345"
        assert result.status == PaymentStatus.pending
        assert result.amount == Decimal("9.99")
        assert result.currency == "USD"
        assert result.checkout_url == "https://nowpayments.io/payment/?iid=12345"
        assert result.metadata["pay_address"] == "TRX123abc"
        assert result.metadata["order_id"] == "ord-001"

        # Verify API call
        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["method"] == "POST"
        assert "/payment" in call_kwargs.kwargs["url"]
        body = call_kwargs.kwargs["json"]
        assert body["price_amount"] == "9.99"
        assert body["price_currency"] == "usd"
        assert body["pay_currency"] == "usdttrc20"
        assert body["order_id"] == "ord-001"
        assert body["order_description"] == "Test service"

    @pytest.mark.asyncio
    async def test_create_payment_custom_pay_currency(self, provider, mock_client):
        mock_client.request.return_value = _mock_response(200, {
            "payment_id": 99,
            "payment_status": "waiting",
        })

        await provider.create_payment(
            amount=Decimal("50.00"),
            currency="USD",
            metadata={"order_id": "ord-002", "pay_currency": "btc"},
        )

        body = mock_client.request.call_args.kwargs["json"]
        assert body["pay_currency"] == "btc"

    @pytest.mark.asyncio
    async def test_create_payment_missing_order_id(self, provider):
        with pytest.raises(PaymentProviderError, match="order_id"):
            await provider.create_payment(
                amount=Decimal("1.00"),
                currency="USD",
                metadata={},
            )

    @pytest.mark.asyncio
    async def test_create_payment_zero_amount(self, provider):
        with pytest.raises(PaymentProviderError, match="positive"):
            await provider.create_payment(
                amount=Decimal("0"),
                currency="USD",
                metadata={"order_id": "ord-1"},
            )

    @pytest.mark.asyncio
    async def test_create_payment_api_error(self, provider, mock_client):
        mock_client.request.return_value = _mock_response(
            401, {"message": "Unauthorized"}
        )

        with pytest.raises(PaymentProviderError, match="401"):
            await provider.create_payment(
                amount=Decimal("10.00"),
                currency="USD",
                metadata={"order_id": "ord-1"},
            )

    @pytest.mark.asyncio
    async def test_create_payment_timeout(self, provider, mock_client):
        mock_client.request.side_effect = httpx.TimeoutException("timeout")

        with pytest.raises(PaymentProviderError, match="timeout"):
            await provider.create_payment(
                amount=Decimal("10.00"),
                currency="USD",
                metadata={"order_id": "ord-1"},
            )

    @pytest.mark.asyncio
    async def test_create_payment_connect_error(self, provider, mock_client):
        mock_client.request.side_effect = httpx.ConnectError("refused")

        with pytest.raises(PaymentProviderError, match="unreachable"):
            await provider.create_payment(
                amount=Decimal("10.00"),
                currency="USD",
                metadata={"order_id": "ord-1"},
            )

    @pytest.mark.asyncio
    async def test_verify_payment_finished(self, provider, mock_client):
        mock_client.request.return_value = _mock_response(200, {
            "payment_id": 12345,
            "payment_status": "finished",
        })

        status = await provider.verify_payment("12345")
        assert status == PaymentStatus.completed

    @pytest.mark.asyncio
    async def test_verify_payment_waiting(self, provider, mock_client):
        mock_client.request.return_value = _mock_response(200, {
            "payment_id": 12345,
            "payment_status": "waiting",
        })

        status = await provider.verify_payment("12345")
        assert status == PaymentStatus.pending

    @pytest.mark.asyncio
    async def test_verify_payment_expired(self, provider, mock_client):
        mock_client.request.return_value = _mock_response(200, {
            "payment_id": 12345,
            "payment_status": "expired",
        })

        status = await provider.verify_payment("12345")
        assert status == PaymentStatus.expired

    @pytest.mark.asyncio
    async def test_verify_payment_failed(self, provider, mock_client):
        mock_client.request.return_value = _mock_response(200, {
            "payment_id": 12345,
            "payment_status": "failed",
        })

        status = await provider.verify_payment("12345")
        assert status == PaymentStatus.failed

    @pytest.mark.asyncio
    async def test_verify_payment_empty_id_raises(self, provider):
        with pytest.raises(PaymentProviderError, match="required"):
            await provider.verify_payment("")

    @pytest.mark.asyncio
    async def test_get_payment(self, provider, mock_client):
        expected = {
            "payment_id": 12345,
            "payment_status": "finished",
            "pay_amount": 9.99,
            "pay_currency": "usdttrc20",
        }
        mock_client.request.return_value = _mock_response(200, expected)

        data = await provider.get_payment("12345")
        assert data["payment_id"] == 12345
        assert data["payment_status"] == "finished"

    @pytest.mark.asyncio
    async def test_get_payment_empty_id_raises(self, provider):
        with pytest.raises(PaymentProviderError, match="required"):
            await provider.get_payment("")

    @pytest.mark.asyncio
    async def test_no_api_key_raises(self, mock_client, monkeypatch):
        monkeypatch.delenv("NOWPAYMENTS_API_KEY", raising=False)
        provider = NOWPaymentsProvider(api_key="", http_client=mock_client)
        with pytest.raises(PaymentProviderError, match="not configured"):
            await provider.create_payment(
                amount=Decimal("1.00"),
                currency="USD",
                metadata={"order_id": "ord-1"},
            )

    def test_status_map_coverage(self):
        """All NOWPayments statuses map to a PaymentStatus."""
        expected_statuses = [
            "waiting", "confirming", "confirmed", "sending",
            "partially_paid", "finished", "failed", "refunded", "expired",
        ]
        for status_str in expected_statuses:
            assert status_str in _STATUS_MAP

    def test_ipn_signature_verification_valid(self, provider):
        payload = {"payment_id": 123, "payment_status": "finished"}
        sorted_body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        signature = hmac.new(
            b"test-ipn-secret",
            sorted_body.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        body_bytes = json.dumps(payload).encode("utf-8")
        assert provider.verify_ipn_signature(body_bytes, signature) is True

    def test_ipn_signature_verification_invalid(self, provider):
        body_bytes = json.dumps({"payment_id": 123}).encode("utf-8")
        assert provider.verify_ipn_signature(body_bytes, "bad_sig") is False

    def test_ipn_signature_no_secret(self, mock_client):
        provider = NOWPaymentsProvider(
            api_key="key", ipn_secret="", http_client=mock_client,
        )
        assert provider.verify_ipn_signature(b'{"a":1}', "sig") is False

    def test_ipn_signature_empty_signature(self, provider):
        assert provider.verify_ipn_signature(b'{"a":1}', "") is False

    def test_ipn_signature_invalid_json(self, provider):
        assert provider.verify_ipn_signature(b"not json", "sig") is False

    def test_env_fallback(self, monkeypatch, mock_client):
        monkeypatch.setenv("NOWPAYMENTS_API_KEY", "env-key-123")
        monkeypatch.setenv("NOWPAYMENTS_IPN_SECRET", "env-secret-456")
        provider = NOWPaymentsProvider(http_client=mock_client)
        assert provider._api_key == "env-key-123"
        assert provider._ipn_secret == "env-secret-456"

    @pytest.mark.asyncio
    async def test_amount_sent_as_string_not_float(self, provider, mock_client):
        """R16-M2 fix: price_amount must be a string to avoid float precision loss."""
        mock_client.request.return_value = _mock_response(200, {
            "payment_id": 55555,
            "payment_status": "waiting",
        })

        await provider.create_payment(
            amount=Decimal("123456.789012"),
            currency="USD",
            metadata={"order_id": "ord-precision"},
        )

        body = mock_client.request.call_args.kwargs["json"]
        assert body["price_amount"] == "123456.789012"
        assert isinstance(body["price_amount"], str)

    @pytest.mark.asyncio
    async def test_idempotency_key_sent(self, provider, mock_client):
        """R16-M3 fix: idempotency key is included in the request body."""
        mock_client.request.return_value = _mock_response(200, {
            "payment_id": 66666,
            "payment_status": "waiting",
        })

        await provider.create_payment(
            amount=Decimal("10.00"),
            currency="USD",
            metadata={"order_id": "ord-idemp"},
        )

        body = mock_client.request.call_args.kwargs["json"]
        assert "case" in body
        assert len(body["case"]) > 10  # UUID string

    @pytest.mark.asyncio
    async def test_custom_idempotency_key(self, provider, mock_client):
        """User-supplied idempotency_key overrides auto-generated one."""
        mock_client.request.return_value = _mock_response(200, {
            "payment_id": 77777,
            "payment_status": "waiting",
        })

        await provider.create_payment(
            amount=Decimal("10.00"),
            currency="USD",
            metadata={"order_id": "ord-custom", "idempotency_key": "my-key-123"},
        )

        body = mock_client.request.call_args.kwargs["json"]
        assert body["case"] == "my-key-123"


# ─── PaymentRouter ───


class _DummyProvider(PaymentProvider):
    """Minimal PaymentProvider for router tests."""

    def __init__(self, name: str, currencies: list[str] | None = None):
        self._name = name
        self._currencies = currencies or ["USD"]

    async def create_payment(self, amount, currency, metadata):
        return PaymentResult(
            payment_id="dummy",
            status=PaymentStatus.pending,
            amount=amount,
            currency=currency,
        )

    async def verify_payment(self, payment_id):
        return PaymentStatus.pending

    async def get_payment(self, payment_id):
        return {"payment_id": payment_id}

    @property
    def provider_name(self):
        return self._name

    @property
    def supported_currencies(self):
        return self._currencies


class TestPaymentRouter:
    @pytest.fixture
    def router(self):
        return PaymentRouter({
            "x402": _DummyProvider("x402", ["USDC"]),
            "nowpayments": _DummyProvider("nowpayments", ["USD", "USDT", "BTC"]),
        })

    def test_route_existing_provider(self, router):
        provider = router.route("x402")
        assert provider is not None
        assert provider.provider_name == "x402"

    def test_route_case_insensitive(self, router):
        provider = router.route("X402")
        assert provider is not None
        assert provider.provider_name == "x402"

        provider2 = router.route("NOWPayments")
        assert provider2 is not None
        assert provider2.provider_name == "nowpayments"

    def test_route_nonexistent_returns_none(self, router):
        assert router.route("stripe") is None

    def test_route_empty_string_returns_none(self, router):
        assert router.route("") is None

    def test_list_providers(self, router):
        providers = router.list_providers()
        assert providers == ["nowpayments", "x402"]

    def test_list_providers_empty_router(self):
        router = PaymentRouter({})
        assert router.list_providers() == []

    def test_len(self, router):
        assert len(router) == 2

    def test_contains(self, router):
        assert "x402" in router
        assert "X402" in router
        assert "stripe" not in router

    def test_get_provider_alias(self, router):
        provider = router.get_provider("nowpayments")
        assert provider is not None
        assert provider.provider_name == "nowpayments"

    def test_single_provider(self):
        router = PaymentRouter({
            "x402": _DummyProvider("x402"),
        })
        assert len(router) == 1
        assert router.route("x402") is not None
        assert router.route("other") is None


# ─── StripeACPProvider ───


import payments.stripe_acp as stripe_acp_module
from payments.stripe_acp import StripeACPProvider, _STRIPE_STATUS_MAP


def _stripe_available():
    """Context manager to pretend stripe SDK is available."""
    return patch.object(stripe_acp_module, "STRIPE_AVAILABLE", True)


class TestStripeACPProvider:
    """Tests for Stripe ACP payment provider."""

    @pytest.fixture
    def provider(self):
        return StripeACPProvider(api_key="sk_test_fake_key")

    def test_provider_name(self, provider):
        assert provider.provider_name == "stripe_acp"

    def test_supported_currencies(self, provider):
        assert set(provider.supported_currencies) == {"USD", "EUR", "GBP"}

    @pytest.mark.asyncio
    async def test_create_payment_unsupported_currency(self, provider):
        with _stripe_available():
            with pytest.raises(PaymentProviderError, match="Unsupported currency"):
                await provider.create_payment(
                    amount=Decimal("10.00"), currency="BTC", metadata={},
                )

    @pytest.mark.asyncio
    async def test_create_payment_zero_amount(self, provider):
        with _stripe_available():
            with pytest.raises(PaymentProviderError, match="must be positive"):
                await provider.create_payment(
                    amount=Decimal("0"), currency="USD", metadata={},
                )

    @pytest.mark.asyncio
    async def test_create_payment_negative_amount(self, provider):
        with _stripe_available():
            with pytest.raises(PaymentProviderError, match="must be positive"):
                await provider.create_payment(
                    amount=Decimal("-5"), currency="USD", metadata={},
                )

    @pytest.mark.asyncio
    async def test_create_payment_no_api_key(self):
        provider = StripeACPProvider(api_key="")
        with _stripe_available():
            with pytest.raises(PaymentProviderError, match="not configured"):
                await provider.create_payment(
                    amount=Decimal("10.00"), currency="USD", metadata={},
                )

    @pytest.mark.asyncio
    async def test_create_payment_no_stripe_sdk(self, provider):
        """When stripe is not installed, require_stripe raises."""
        with patch.object(stripe_acp_module, "STRIPE_AVAILABLE", False):
            with pytest.raises(PaymentProviderError, match="not installed"):
                await provider.create_payment(
                    amount=Decimal("10.00"), currency="USD", metadata={},
                )

    @pytest.mark.asyncio
    async def test_create_payment_success(self, provider):
        mock_session = {
            "id": "cs_test_abc123",
            "url": "https://checkout.stripe.com/pay/cs_test_abc123",
            "status": "open",
        }
        with _stripe_available(), patch("payments.stripe_acp.stripe") as mock_stripe:
            mock_stripe.checkout.Session.create.return_value = mock_session
            result = await provider.create_payment(
                amount=Decimal("25.00"),
                currency="usd",
                metadata={"description": "Test payment"},
            )
        assert result.payment_id == "stripe_acp_cs_test_abc123"
        assert result.status == PaymentStatus.pending
        assert result.amount == Decimal("25.00")
        assert result.currency == "USD"
        assert result.checkout_url == "https://checkout.stripe.com/pay/cs_test_abc123"
        assert result.metadata["stripe_session_id"] == "cs_test_abc123"

    @pytest.mark.asyncio
    async def test_create_payment_stripe_api_error(self, provider):
        with _stripe_available(), patch("payments.stripe_acp.stripe") as mock_stripe:
            mock_stripe.checkout.Session.create.side_effect = Exception("API down")
            with pytest.raises(PaymentProviderError, match="Stripe API error"):
                await provider.create_payment(
                    amount=Decimal("10.00"), currency="USD", metadata={},
                )

    @pytest.mark.asyncio
    async def test_create_payment_custom_urls(self, provider):
        mock_session = {"id": "cs_test_xyz", "url": "https://checkout.stripe.com/xyz", "status": "open"}
        with _stripe_available(), patch("payments.stripe_acp.stripe") as mock_stripe:
            mock_stripe.checkout.Session.create.return_value = mock_session
            result = await provider.create_payment(
                amount=Decimal("50.00"),
                currency="EUR",
                metadata={
                    "success_url": "https://mysite.com/ok",
                    "cancel_url": "https://mysite.com/cancel",
                },
            )
        assert result.currency == "EUR"
        call_kwargs = mock_stripe.checkout.Session.create.call_args
        assert call_kwargs[1]["success_url"] == "https://mysite.com/ok"

    @pytest.mark.asyncio
    async def test_verify_payment_success(self, provider):
        mock_session = {"status": "complete"}
        with _stripe_available(), patch("payments.stripe_acp.stripe") as mock_stripe:
            mock_stripe.checkout.Session.retrieve.return_value = mock_session
            status = await provider.verify_payment("stripe_acp_cs_test_abc")
        assert status == PaymentStatus.completed

    @pytest.mark.asyncio
    async def test_verify_payment_pending(self, provider):
        mock_session = {"status": "open"}
        with _stripe_available(), patch("payments.stripe_acp.stripe") as mock_stripe:
            mock_stripe.checkout.Session.retrieve.return_value = mock_session
            status = await provider.verify_payment("stripe_acp_cs_test_abc")
        assert status == PaymentStatus.pending

    @pytest.mark.asyncio
    async def test_verify_payment_expired(self, provider):
        mock_session = {"status": "expired"}
        with _stripe_available(), patch("payments.stripe_acp.stripe") as mock_stripe:
            mock_stripe.checkout.Session.retrieve.return_value = mock_session
            status = await provider.verify_payment("stripe_acp_cs_test_abc")
        assert status == PaymentStatus.expired

    @pytest.mark.asyncio
    async def test_verify_payment_empty_id(self, provider):
        with pytest.raises(PaymentProviderError, match="required"):
            await provider.verify_payment("")

    @pytest.mark.asyncio
    async def test_verify_payment_api_error(self, provider):
        with _stripe_available(), patch("payments.stripe_acp.stripe") as mock_stripe:
            mock_stripe.checkout.Session.retrieve.side_effect = Exception("Network error")
            with pytest.raises(PaymentProviderError, match="Stripe verify error"):
                await provider.verify_payment("stripe_acp_cs_test_abc")

    @pytest.mark.asyncio
    async def test_get_payment_success(self, provider):
        mock_session = {"id": "cs_test_abc", "status": "complete", "amount_total": 2500}
        with _stripe_available(), patch("payments.stripe_acp.stripe") as mock_stripe:
            mock_stripe.checkout.Session.retrieve.return_value = mock_session
            details = await provider.get_payment("stripe_acp_cs_test_abc")
        assert details["payment_id"] == "stripe_acp_cs_test_abc"
        assert details["provider"] == "stripe_acp"
        assert details["amount_total"] == 2500

    @pytest.mark.asyncio
    async def test_get_payment_empty_id(self, provider):
        with pytest.raises(PaymentProviderError, match="required"):
            await provider.get_payment("")

    def test_stripe_status_map_completeness(self):
        assert _STRIPE_STATUS_MAP["open"] == PaymentStatus.pending
        assert _STRIPE_STATUS_MAP["complete"] == PaymentStatus.completed
        assert _STRIPE_STATUS_MAP["expired"] == PaymentStatus.expired
        assert _STRIPE_STATUS_MAP["canceled"] == PaymentStatus.failed
        assert _STRIPE_STATUS_MAP["succeeded"] == PaymentStatus.completed

    @pytest.mark.asyncio
    async def test_create_payment_case_insensitive_currency(self, provider):
        mock_session = {"id": "cs_test_gbp", "url": "https://pay.stripe.com/gbp", "status": "open"}
        with _stripe_available(), patch("payments.stripe_acp.stripe") as mock_stripe:
            mock_stripe.checkout.Session.create.return_value = mock_session
            result = await provider.create_payment(
                amount=Decimal("15.00"), currency="gbp", metadata={},
            )
        assert result.currency == "GBP"


# ─── AgentKitProvider ───


from payments.agentkit_provider import AgentKitProvider


class _MockWalletManager:
    """Mock WalletManager for testing AgentKit provider."""

    def __init__(self, ready=True, address="0xTestAddr123", transfer_result="0xTxHash456"):
        self.is_ready = ready
        self.address = address
        self._transfer_result = transfer_result
        self.config = MagicMock()
        self.config.cdp_network = "base-sepolia"

    async def transfer_usdc(self, to_address: str, amount: Decimal, idempotency_key: str | None = None) -> str | None:
        return self._transfer_result

    async def create_agent_wallet(self, agent_id: str) -> str | None:
        return f"0xWallet_{agent_id[:8]}"


class TestAgentKitProvider:
    """Tests for AgentKit direct USDC transfer provider."""

    @pytest.fixture
    def wallet(self):
        return _MockWalletManager()

    @pytest.fixture
    def provider(self, wallet):
        return AgentKitProvider(wallet_manager=wallet)

    def test_provider_name(self, provider):
        assert provider.provider_name == "agentkit"

    def test_supported_currencies(self, provider):
        assert provider.supported_currencies == ["USDC"]

    def test_wallet_address(self, provider):
        assert provider.wallet_address == "0xTestAddr123"

    def test_wallet_address_none_when_no_wallet(self):
        provider = AgentKitProvider(wallet_manager=None)
        assert provider.wallet_address is None

    @pytest.mark.asyncio
    async def test_create_payment_success(self, provider):
        result = await provider.create_payment(
            amount=Decimal("5.00"),
            currency="USDC",
            metadata={"to_address": "0xRecipient999"},
        )
        assert result.payment_id.startswith("agentkit_")
        assert result.status == PaymentStatus.completed
        assert result.amount == Decimal("5.00")
        assert result.currency == "USDC"
        assert result.metadata["tx_hash"] == "0xTxHash456"
        assert result.metadata["to_address"] == "0xRecipient999"
        assert result.metadata["network"] == "base-sepolia"

    @pytest.mark.asyncio
    async def test_create_payment_case_insensitive(self, provider):
        result = await provider.create_payment(
            amount=Decimal("1.00"),
            currency="usdc",
            metadata={"to_address": "0xAddr"},
        )
        assert result.status == PaymentStatus.completed

    @pytest.mark.asyncio
    async def test_create_payment_unsupported_currency(self, provider):
        with pytest.raises(PaymentProviderError, match="only supports"):
            await provider.create_payment(
                amount=Decimal("10.00"),
                currency="ETH",
                metadata={"to_address": "0xAddr"},
            )

    @pytest.mark.asyncio
    async def test_create_payment_zero_amount(self, provider):
        with pytest.raises(PaymentProviderError, match="must be positive"):
            await provider.create_payment(
                amount=Decimal("0"),
                currency="USDC",
                metadata={"to_address": "0xAddr"},
            )

    @pytest.mark.asyncio
    async def test_create_payment_negative_amount(self, provider):
        with pytest.raises(PaymentProviderError, match="must be positive"):
            await provider.create_payment(
                amount=Decimal("-1"),
                currency="USDC",
                metadata={"to_address": "0xAddr"},
            )

    @pytest.mark.asyncio
    async def test_create_payment_missing_to_address(self, provider):
        with pytest.raises(PaymentProviderError, match="to_address"):
            await provider.create_payment(
                amount=Decimal("5.00"),
                currency="USDC",
                metadata={},
            )

    @pytest.mark.asyncio
    async def test_create_payment_no_wallet(self):
        provider = AgentKitProvider(wallet_manager=None)
        with pytest.raises(PaymentProviderError, match="not configured"):
            await provider.create_payment(
                amount=Decimal("5.00"),
                currency="USDC",
                metadata={"to_address": "0xAddr"},
            )

    @pytest.mark.asyncio
    async def test_create_payment_wallet_not_ready(self):
        wallet = _MockWalletManager(ready=False)
        provider = AgentKitProvider(wallet_manager=wallet)
        with pytest.raises(PaymentProviderError, match="not configured"):
            await provider.create_payment(
                amount=Decimal("5.00"),
                currency="USDC",
                metadata={"to_address": "0xAddr"},
            )

    @pytest.mark.asyncio
    async def test_create_payment_transfer_fails(self):
        wallet = _MockWalletManager(transfer_result=None)
        provider = AgentKitProvider(wallet_manager=wallet)
        result = await provider.create_payment(
            amount=Decimal("5.00"),
            currency="USDC",
            metadata={"to_address": "0xAddr"},
        )
        assert result.status == PaymentStatus.failed
        assert "failed" in result.metadata["reason"].lower()

    @pytest.mark.asyncio
    async def test_verify_payment_without_evidence_returns_pending(self, provider):
        """Without on-chain tx_hash, verify_payment returns pending (R15-H1 fix)."""
        from payments.agentkit_provider import _completed_payments
        _completed_payments.pop("agentkit_abc123", None)
        status = await provider.verify_payment("agentkit_abc123")
        assert status == PaymentStatus.pending

    @pytest.mark.asyncio
    async def test_verify_payment_empty_id(self, provider):
        with pytest.raises(PaymentProviderError, match="required"):
            await provider.verify_payment("")

    @pytest.mark.asyncio
    async def test_verify_payment_invalid_prefix(self, provider):
        with pytest.raises(PaymentProviderError, match="Invalid"):
            await provider.verify_payment("stripe_acp_123")

    @pytest.mark.asyncio
    async def test_get_payment_details(self, provider):
        details = await provider.get_payment("agentkit_test123")
        assert details["payment_id"] == "agentkit_test123"
        assert details["provider"] == "agentkit"
        assert details["wallet_address"] == "0xTestAddr123"
        assert details["network"] == "base-sepolia"
        assert details["wallet_ready"] is True

    @pytest.mark.asyncio
    async def test_get_payment_empty_id(self, provider):
        with pytest.raises(PaymentProviderError, match="required"):
            await provider.get_payment("")

    @pytest.mark.asyncio
    async def test_get_payment_no_wallet(self):
        provider = AgentKitProvider(wallet_manager=None)
        details = await provider.get_payment("agentkit_test123")
        assert details["wallet_address"] is None
        assert details["wallet_ready"] is False

    @pytest.mark.asyncio
    async def test_create_agent_wallet(self, provider):
        address = await provider.create_agent_wallet("agent-007")
        assert address is not None
        assert "agent-00" in address

    @pytest.mark.asyncio
    async def test_create_agent_wallet_no_manager(self):
        provider = AgentKitProvider(wallet_manager=None)
        result = await provider.create_agent_wallet("agent-007")
        assert result is None
