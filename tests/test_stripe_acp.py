"""
Tests for Stripe Agent Checkout Protocol (ACP) payment provider.

Covers:
- Provider name and supported currencies
- Create payment with mocked Stripe SDK
- Currency validation (unsupported currencies rejected)
- Amount validation (zero and negative amounts rejected)
- Verify payment
- Get payment
- Graceful handling when Stripe SDK is not installed
- Payment ID prefix format
- Environment variable key loading
- PaymentRouter integration
- Error propagation from Stripe API
- Status mapping
- Custom success/cancel URLs
"""
from __future__ import annotations

import sys
from decimal import Decimal
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from payments.base import (
    PaymentProvider,
    PaymentProviderError,
    PaymentResult,
    PaymentStatus,
)
from payments.router import PaymentRouter
from payments.stripe_acp import (
    STRIPE_AVAILABLE,
    StripeACPProvider,
    _STRIPE_STATUS_MAP,
)


# ─── Helpers ───


def _make_provider(api_key: str = "sk_test_fake_key_123") -> StripeACPProvider:
    """Create a StripeACPProvider with a test API key."""
    return StripeACPProvider(api_key=api_key)


def _mock_stripe_module() -> MagicMock:
    """Build a mock ``stripe`` module with checkout.Session."""
    mock_stripe = MagicMock()
    return mock_stripe


# ─── Provider identity ───


class TestProviderIdentity:
    def test_provider_name(self):
        provider = _make_provider()
        assert provider.provider_name == "stripe_acp"

    def test_supported_currencies(self):
        provider = _make_provider()
        currencies = provider.supported_currencies
        assert "USD" in currencies
        assert "EUR" in currencies
        assert "GBP" in currencies

    def test_supported_currencies_returns_copy(self):
        provider = _make_provider()
        c1 = provider.supported_currencies
        c2 = provider.supported_currencies
        assert c1 == c2
        assert c1 is not c2  # must be a new list each time

    def test_is_payment_provider_subclass(self):
        assert issubclass(StripeACPProvider, PaymentProvider)


# ─── Create payment ───


class TestCreatePayment:
    @pytest.mark.asyncio
    async def test_create_payment_success(self):
        """Mocked Stripe SDK returns a session; provider wraps it."""
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.create.return_value = {
            "id": "cs_test_abc123",
            "url": "https://checkout.stripe.com/pay/cs_test_abc123",
            "status": "open",
        }

        provider = _make_provider()

        with patch.dict(sys.modules, {"stripe": mock_stripe}):
            with patch("payments.stripe_acp.STRIPE_AVAILABLE", True):
                with patch("payments.stripe_acp.stripe", mock_stripe):
                    result = await provider.create_payment(
                        amount=Decimal("29.99"),
                        currency="USD",
                        metadata={"description": "API access"},
                    )

        assert result.payment_id == "stripe_acp_cs_test_abc123"
        assert result.status == PaymentStatus.pending
        assert result.amount == Decimal("29.99")
        assert result.currency == "USD"
        assert result.checkout_url == "https://checkout.stripe.com/pay/cs_test_abc123"
        assert result.metadata["stripe_session_id"] == "cs_test_abc123"
        assert result.metadata["description"] == "API access"
        assert isinstance(result, PaymentResult)

    @pytest.mark.asyncio
    async def test_create_payment_completed_status(self):
        """Stripe returns 'complete' status -> PaymentStatus.completed."""
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.create.return_value = {
            "id": "cs_test_done",
            "url": None,
            "status": "complete",
        }

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            result = await provider.create_payment(
                amount=Decimal("10.00"),
                currency="EUR",
                metadata={"description": "Test"},
            )

        assert result.status == PaymentStatus.completed

    @pytest.mark.asyncio
    async def test_create_payment_case_insensitive_currency(self):
        """Lower-case currency is accepted and uppercased."""
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.create.return_value = {
            "id": "cs_test_ci",
            "url": None,
            "status": "open",
        }

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            result = await provider.create_payment(
                amount=Decimal("5.00"),
                currency="usd",
                metadata={"description": "test"},
            )

        assert result.currency == "USD"

    @pytest.mark.asyncio
    async def test_create_payment_passes_amount_cents_to_stripe(self):
        """Stripe receives amount in cents (smallest unit)."""
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.create.return_value = {
            "id": "cs_test_cents",
            "url": None,
            "status": "open",
        }

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            await provider.create_payment(
                amount=Decimal("42.50"),
                currency="GBP",
                metadata={"description": "widget"},
            )

        call_kwargs = mock_stripe.checkout.Session.create.call_args
        line_items = call_kwargs.kwargs["line_items"]
        assert line_items[0]["price_data"]["unit_amount"] == 4250

    @pytest.mark.asyncio
    async def test_create_payment_id_prefix(self):
        """Payment ID starts with 'stripe_acp_'."""
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.create.return_value = {
            "id": "cs_live_xyz789",
            "url": None,
            "status": "open",
        }

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            result = await provider.create_payment(
                amount=Decimal("1.00"),
                currency="USD",
                metadata={"description": "test"},
            )

        assert result.payment_id.startswith("stripe_acp_")


# ─── Validation ───


class TestValidation:
    @pytest.mark.asyncio
    async def test_unsupported_currency_raises(self):
        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", _mock_stripe_module()):
            with pytest.raises(PaymentProviderError, match="Unsupported currency"):
                await provider.create_payment(
                    amount=Decimal("10.00"),
                    currency="BTC",
                    metadata={"description": "test"},
                )

    @pytest.mark.asyncio
    async def test_zero_amount_raises(self):
        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", _mock_stripe_module()):
            with pytest.raises(PaymentProviderError, match="positive"):
                await provider.create_payment(
                    amount=Decimal("0"),
                    currency="USD",
                    metadata={"description": "test"},
                )

    @pytest.mark.asyncio
    async def test_negative_amount_raises(self):
        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", _mock_stripe_module()):
            with pytest.raises(PaymentProviderError, match="positive"):
                await provider.create_payment(
                    amount=Decimal("-5.00"),
                    currency="USD",
                    metadata={"description": "test"},
                )

    @pytest.mark.asyncio
    async def test_no_api_key_raises(self):
        provider = StripeACPProvider(api_key="")

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", _mock_stripe_module()):
            with pytest.raises(PaymentProviderError, match="STRIPE_API_KEY not configured"):
                await provider.create_payment(
                    amount=Decimal("10.00"),
                    currency="USD",
                    metadata={"description": "test"},
                )


# ─── Stripe SDK not installed ───


class TestStripeNotInstalled:
    @pytest.mark.asyncio
    async def test_create_payment_without_sdk_raises(self):
        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", False):
            with pytest.raises(PaymentProviderError, match="stripe SDK is not installed"):
                await provider.create_payment(
                    amount=Decimal("10.00"),
                    currency="USD",
                    metadata={"description": "test"},
                )

    @pytest.mark.asyncio
    async def test_verify_payment_without_sdk_raises(self):
        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", False):
            with pytest.raises(PaymentProviderError, match="stripe SDK is not installed"):
                await provider.verify_payment("stripe_acp_cs_test_123")

    @pytest.mark.asyncio
    async def test_get_payment_without_sdk_raises(self):
        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", False):
            with pytest.raises(PaymentProviderError, match="stripe SDK is not installed"):
                await provider.get_payment("stripe_acp_cs_test_123")


# ─── Verify payment ───


class TestVerifyPayment:
    @pytest.mark.asyncio
    async def test_verify_payment_success(self):
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.retrieve.return_value = {
            "id": "cs_test_abc",
            "status": "complete",
        }

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            status = await provider.verify_payment("stripe_acp_cs_test_abc")

        assert status == PaymentStatus.completed
        mock_stripe.checkout.Session.retrieve.assert_called_once_with(
            "cs_test_abc", api_key="sk_test_fake_key_123",
        )

    @pytest.mark.asyncio
    async def test_verify_payment_empty_id_raises(self):
        provider = _make_provider()

        with pytest.raises(PaymentProviderError, match="required"):
            await provider.verify_payment("")

    @pytest.mark.asyncio
    async def test_verify_payment_strips_prefix(self):
        """The stripe_acp_ prefix is removed before calling Stripe."""
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.retrieve.return_value = {
            "id": "cs_test_strip",
            "status": "open",
        }

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            await provider.verify_payment("stripe_acp_cs_test_strip")

        mock_stripe.checkout.Session.retrieve.assert_called_once_with(
            "cs_test_strip", api_key="sk_test_fake_key_123",
        )

    @pytest.mark.asyncio
    async def test_verify_payment_api_error(self):
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.retrieve.side_effect = Exception("Not found")

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            with pytest.raises(PaymentProviderError, match="Stripe verify error"):
                await provider.verify_payment("stripe_acp_cs_test_bad")


# ─── Get payment ───


class TestGetPayment:
    @pytest.mark.asyncio
    async def test_get_payment_success(self):
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.retrieve.return_value = {
            "id": "cs_test_get",
            "status": "complete",
            "amount_total": 2999,
            "currency": "usd",
        }

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            data = await provider.get_payment("stripe_acp_cs_test_get")

        assert data["payment_id"] == "stripe_acp_cs_test_get"
        assert data["provider"] == "stripe_acp"
        assert data["status"] == "complete"
        assert data["amount_total"] == 2999

    @pytest.mark.asyncio
    async def test_get_payment_empty_id_raises(self):
        provider = _make_provider()

        with pytest.raises(PaymentProviderError, match="required"):
            await provider.get_payment("")

    @pytest.mark.asyncio
    async def test_get_payment_api_error(self):
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.retrieve.side_effect = Exception("API down")

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            with pytest.raises(PaymentProviderError, match="Stripe get_payment error"):
                await provider.get_payment("stripe_acp_cs_bad")


# ─── Env var loading ───


class TestEnvVarLoading:
    def test_loads_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("STRIPE_API_KEY", "sk_test_env_key_999")
        provider = StripeACPProvider()
        assert provider._api_key == "sk_test_env_key_999"

    def test_constructor_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("STRIPE_API_KEY", "sk_test_env_key")
        provider = StripeACPProvider(api_key="sk_test_explicit_key")
        assert provider._api_key == "sk_test_explicit_key"

    def test_no_key_results_in_empty_string(self, monkeypatch):
        monkeypatch.delenv("STRIPE_API_KEY", raising=False)
        provider = StripeACPProvider()
        assert provider._api_key == ""


# ─── Status map ───


class TestStatusMap:
    def test_open_maps_to_pending(self):
        assert _STRIPE_STATUS_MAP["open"] == PaymentStatus.pending

    def test_complete_maps_to_completed(self):
        assert _STRIPE_STATUS_MAP["complete"] == PaymentStatus.completed

    def test_expired_maps_to_expired(self):
        assert _STRIPE_STATUS_MAP["expired"] == PaymentStatus.expired

    def test_canceled_maps_to_failed(self):
        assert _STRIPE_STATUS_MAP["canceled"] == PaymentStatus.failed

    def test_succeeded_maps_to_completed(self):
        assert _STRIPE_STATUS_MAP["succeeded"] == PaymentStatus.completed

    def test_processing_maps_to_pending(self):
        assert _STRIPE_STATUS_MAP["processing"] == PaymentStatus.pending


# ─── PaymentRouter integration ───


class TestRouterIntegration:
    def test_stripe_acp_in_router(self):
        provider = _make_provider()
        router = PaymentRouter({"stripe_acp": provider})
        assert "stripe_acp" in router
        resolved = router.route("stripe_acp")
        assert resolved is provider
        assert resolved.provider_name == "stripe_acp"

    def test_router_case_insensitive(self):
        provider = _make_provider()
        router = PaymentRouter({"stripe_acp": provider})
        resolved = router.route("STRIPE_ACP")
        assert resolved is provider

    def test_router_lists_stripe_acp(self):
        provider = _make_provider()
        router = PaymentRouter({"stripe_acp": provider, "x402": _make_provider()})
        providers = router.list_providers()
        assert "stripe_acp" in providers


# ─── Stripe API error propagation ───


class TestAPIErrorPropagation:
    @pytest.mark.asyncio
    async def test_create_payment_stripe_error_wrapped(self):
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.create.side_effect = Exception(
            "Invalid API Key provided"
        )

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            with pytest.raises(PaymentProviderError, match="Stripe API error"):
                await provider.create_payment(
                    amount=Decimal("10.00"),
                    currency="USD",
                    metadata={"description": "test"},
                )


# ─── Thread-safe API key (R15-H2 fix) ───


class TestThreadSafeApiKey:
    """Verify Stripe provider passes api_key per-request, not globally."""

    @pytest.mark.asyncio
    async def test_create_payment_passes_api_key_per_request(self):
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.create.return_value = {
            "id": "cs_test_thr", "url": None, "status": "open",
        }

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            await provider.create_payment(
                amount=Decimal("10.00"),
                currency="USD",
                metadata={"description": "test"},
            )

        call_kwargs = mock_stripe.checkout.Session.create.call_args
        assert call_kwargs.kwargs["api_key"] == "sk_test_fake_key_123"

    @pytest.mark.asyncio
    async def test_verify_payment_passes_api_key_per_request(self):
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.retrieve.return_value = {
            "status": "complete",
        }

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            await provider.verify_payment("stripe_acp_cs_test_thr")

        mock_stripe.checkout.Session.retrieve.assert_called_once_with(
            "cs_test_thr", api_key="sk_test_fake_key_123",
        )

    @pytest.mark.asyncio
    async def test_get_payment_passes_api_key_per_request(self):
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.retrieve.return_value = {
            "id": "cs_test_get_thr", "status": "open",
        }

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            await provider.get_payment("stripe_acp_cs_test_get_thr")

        mock_stripe.checkout.Session.retrieve.assert_called_once_with(
            "cs_test_get_thr", api_key="sk_test_fake_key_123",
        )


# ─── Amount conversion precision (R16-M1 fix) ───


class TestAmountConversion:
    """Verify sub-cent amounts are not truncated."""

    @pytest.mark.asyncio
    async def test_sub_cent_amount_rounded_correctly(self):
        """Decimal('19.99') * 100 = 1999 cents (not 1998 from float)."""
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.create.return_value = {
            "id": "cs_test_precise", "url": None, "status": "open",
        }

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            await provider.create_payment(
                amount=Decimal("19.99"),
                currency="USD",
                metadata={"description": "precise"},
            )

        call_kwargs = mock_stripe.checkout.Session.create.call_args
        line_items = call_kwargs.kwargs["line_items"]
        assert line_items[0]["price_data"]["unit_amount"] == 1999

    @pytest.mark.asyncio
    async def test_large_amount_no_float_drift(self):
        """Large amounts stay exact: $99999.99 -> 9999999 cents."""
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.create.return_value = {
            "id": "cs_test_large", "url": None, "status": "open",
        }

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            await provider.create_payment(
                amount=Decimal("99999.99"),
                currency="USD",
                metadata={"description": "large"},
            )

        call_kwargs = mock_stripe.checkout.Session.create.call_args
        line_items = call_kwargs.kwargs["line_items"]
        assert line_items[0]["price_data"]["unit_amount"] == 9999999


# ─── Idempotency key (R16-M3 fix) ───


class TestIdempotencyKey:
    """Verify idempotency_key is passed to Stripe."""

    @pytest.mark.asyncio
    async def test_create_payment_includes_idempotency_key(self):
        mock_stripe = _mock_stripe_module()
        mock_stripe.checkout.Session.create.return_value = {
            "id": "cs_test_idemp", "url": None, "status": "open",
        }

        provider = _make_provider()

        with patch("payments.stripe_acp.STRIPE_AVAILABLE", True), \
             patch("payments.stripe_acp.stripe", mock_stripe):
            await provider.create_payment(
                amount=Decimal("10.00"),
                currency="USD",
                metadata={"description": "idempotent"},
            )

        call_kwargs = mock_stripe.checkout.Session.create.call_args
        assert "idempotency_key" in call_kwargs.kwargs
        assert len(call_kwargs.kwargs["idempotency_key"]) > 10  # UUID string
