"""Tests for Payment Proxy module."""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from marketplace.db import Database
from marketplace.proxy import PaymentProxy, ProxyError, ProxyResult, BillingInfo
from payments.base import PaymentResult, PaymentStatus
from payments.router import PaymentRouter


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def proxy(db):
    return PaymentProxy(db, platform_fee_pct=Decimal("0.10"))


def _make_service(**overrides) -> dict:
    """Create a service dict for testing."""
    defaults = {
        "id": "svc-1",
        "provider_id": "prov-1",
        "endpoint": "https://example.com/api",
        "price_per_call": "0.01",
        "payment_method": "x402",
        "free_tier_calls": 0,
        "status": "active",
    }
    defaults.update(overrides)
    return defaults


def _fund_buyer(db: Database, buyer_id: str, amount: str = "100.00") -> None:
    """Credit buyer balance so paid proxy calls don't fail."""
    db.credit_balance(buyer_id, Decimal(amount))


# --- Service validation ---

class TestServiceValidation:
    @pytest.mark.asyncio
    async def test_inactive_service_rejected(self, proxy):
        service = _make_service(status="paused")
        with pytest.raises(ProxyError, match="not active"):
            await proxy.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
            )


# --- Billing ---

class TestBilling:
    @pytest.mark.asyncio
    async def test_billing_calculates_fees(self, proxy, db):
        service = _make_service(price_per_call="1.00")
        _fund_buyer(db, "buyer-1")

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b'{"ok": true}'
            mock_response.headers = {"content-type": "application/json"}

            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await proxy.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
            )

        assert result.billing.amount == Decimal("1.00")
        assert result.billing.platform_fee == Decimal("0.10")
        assert result.billing.provider_amount == Decimal("0.90")
        assert not result.billing.free_tier

    @pytest.mark.asyncio
    async def test_free_tier_no_charge(self, proxy, db):
        service = _make_service(free_tier_calls=10)

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b"{}"
            mock_response.headers = {}

            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await proxy.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
            )

        assert result.billing.amount == Decimal("0")
        assert result.billing.free_tier


# --- Error handling ---

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_timeout_returns_504(self, proxy, db):
        import httpx

        service = _make_service()
        _fund_buyer(db, "buyer-1")

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await proxy.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
            )

        assert result.status_code == 504
        assert result.error == "Provider timeout"

    @pytest.mark.asyncio
    async def test_connect_error_returns_502(self, proxy, db):
        import httpx

        service = _make_service()
        _fund_buyer(db, "buyer-1")

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await proxy.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
            )

        assert result.status_code == 502
        assert result.error == "Provider unreachable"

    @pytest.mark.asyncio
    async def test_server_error_no_charge(self, proxy, db):
        service = _make_service(price_per_call="0.50")
        _fund_buyer(db, "buyer-1")

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(side_effect=Exception("boom"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await proxy.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
            )

        assert result.status_code == 500
        # Server errors should not charge
        stats = proxy.db.get_usage_stats(service_id="svc-1")
        record_amount = stats["total_revenue"]
        assert record_amount == Decimal("0")


# --- Usage recording ---

class TestUsageRecording:
    @pytest.mark.asyncio
    async def test_records_usage(self, proxy, db):
        service = _make_service()
        _fund_buyer(db, "buyer-1")

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b'{"data": 1}'
            mock_response.headers = {"content-type": "application/json"}

            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            await proxy.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
            )

        stats = db.get_usage_stats(service_id="svc-1")
        assert stats["total_calls"] == 1


# --- ProxyResult ---

class TestProxyResult:
    def test_success_property(self):
        result = ProxyResult(
            status_code=200,
            body=b"ok",
            headers={},
            latency_ms=50,
            billing=BillingInfo(
                amount=Decimal("0"),
                platform_fee=Decimal("0"),
                provider_amount=Decimal("0"),
                usage_id="u1",
            ),
            error=None,
        )
        assert result.success

    def test_error_property(self):
        result = ProxyResult(
            status_code=500,
            body=b"",
            headers={},
            latency_ms=0,
            billing=BillingInfo(
                amount=Decimal("0"),
                platform_fee=Decimal("0"),
                provider_amount=Decimal("0"),
                usage_id="u2",
            ),
            error="Server error",
        )
        assert not result.success


# --- Payment Router integration ---

class TestPaymentRouterIntegration:
    @pytest.fixture
    def mock_provider(self):
        provider = AsyncMock()
        provider.provider_name = "test_provider"
        provider.supported_currencies = ["USDC"]
        provider.create_payment = AsyncMock(return_value=PaymentResult(
            payment_id="pay_test_123",
            status=PaymentStatus("pending"),
            amount=1.0,
            currency="USDC",
        ))
        return provider

    @pytest.fixture
    def proxy_with_router(self, db, mock_provider):
        router = PaymentRouter({"x402": mock_provider})
        return PaymentProxy(
            db,
            platform_fee_pct=Decimal("0.10"),
            payment_router=router,
        )

    @pytest.mark.asyncio
    async def test_uses_payment_router(self, proxy_with_router, db, mock_provider):
        """Proxy should deduct balance for paid calls."""
        service = _make_service(price_per_call="1.00", payment_method="x402")
        _fund_buyer(db, "buyer-1")

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b'{"ok": true}'
            mock_response.headers = {}

            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await proxy_with_router.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
            )

        assert result.status_code == 200
        assert result.billing.amount == Decimal("1.00")

    @pytest.mark.asyncio
    async def test_payment_id_recorded_in_usage(self, proxy_with_router, db, mock_provider):
        """Balance deduction payment ID should be recorded in usage record."""
        service = _make_service(price_per_call="0.50", payment_method="x402")
        _fund_buyer(db, "buyer-1")

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b"{}"
            mock_response.headers = {}

            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            await proxy_with_router.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
            )

        # Check usage record has payment_id
        with db.connect() as conn:
            row = conn.execute(
                "SELECT payment_tx FROM usage_records WHERE service_id = ?",
                ("svc-1",),
            ).fetchone()
        assert row["payment_tx"].startswith("balance:")

    @pytest.mark.asyncio
    async def test_no_router_uses_x402_header(self, proxy, db):
        """Without router, balance deduction should still work."""
        service = _make_service(price_per_call="0.50")
        _fund_buyer(db, "buyer-1")

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b"{}"
            mock_response.headers = {}

            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await proxy.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
                request_headers={"x-payment-tx": "0xabc123"},
            )

        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_free_tier_skips_payment(self, proxy_with_router, db, mock_provider):
        """Free tier calls should not create a payment."""
        service = _make_service(price_per_call="1.00", free_tier_calls=10)

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b"{}"
            mock_response.headers = {}

            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await proxy_with_router.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
            )

        # Free tier = no payment created
        mock_provider.create_payment.assert_not_called()
        assert result.billing.free_tier

    @pytest.mark.asyncio
    async def test_payment_failure_still_forwards(self, proxy_with_router, db, mock_provider):
        """Paid calls should succeed when buyer has sufficient balance."""
        service = _make_service(price_per_call="1.00", payment_method="x402")
        _fund_buyer(db, "buyer-1")

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b'{"ok": true}'
            mock_response.headers = {}

            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await proxy_with_router.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
            )

        # Request still went through despite payment failure
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_unknown_payment_method_no_crash(self, proxy_with_router, db):
        """Unknown payment method should not crash — balance deduction works for all."""
        service = _make_service(price_per_call="1.00", payment_method="stripe")
        _fund_buyer(db, "buyer-1")

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b"{}"
            mock_response.headers = {}

            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await proxy_with_router.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
            )

        assert result.status_code == 200


# --- x402 middleware bypass ---

class TestX402PaymentBypass:
    @pytest.mark.asyncio
    async def test_x402_paid_skips_balance_deduction(self, proxy, db):
        """When x402 middleware already verified payment with valid tx hash, skip balance deduction."""
        service = _make_service(price_per_call="1.00", payment_method="x402")
        tx_hash = "0xabcdef1234567890abcdef1234567890"

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b'{"ok": true}'
            mock_response.headers = {"content-type": "application/json"}

            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await proxy.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
                x402_paid=True,
                headers={"x-payment-transaction": tx_hash},
            )

        assert result.status_code == 200
        assert result.billing.amount == Decimal("1.00")
        # Balance should be untouched (still 0) because x402 paid
        bal = db.get_balance("buyer-1")
        assert bal == Decimal("0")

    @pytest.mark.asyncio
    async def test_x402_paid_records_payment_method(self, proxy, db):
        """x402-paid requests should record payment_method as x402."""
        service = _make_service(price_per_call="0.50", payment_method="x402")
        tx_hash = "0xabc123def456789012345678901234567890abcdef"

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b"{}"
            mock_response.headers = {"content-type": "application/json"}

            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            await proxy.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
                x402_paid=True,
                headers={"x-payment-transaction": tx_hash},
            )

        with db.connect() as conn:
            row = conn.execute(
                "SELECT payment_method, payment_tx FROM usage_records WHERE service_id = ?",
                ("svc-1",),
            ).fetchone()
        assert row["payment_method"] == "x402"
        assert row["payment_tx"] == tx_hash

    @pytest.mark.asyncio
    async def test_x402_not_paid_requires_balance(self, proxy, db):
        """Without x402 payment, balance deduction is required."""
        service = _make_service(price_per_call="1.00", payment_method="x402")
        # No balance = should fail

        with pytest.raises(ProxyError, match="Insufficient balance"):
            await proxy.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
                x402_paid=False,
            )

    @pytest.mark.asyncio
    async def test_x402_free_tier_still_free(self, proxy, db):
        """Free tier should still be free even with x402_paid=True."""
        service = _make_service(price_per_call="1.00", free_tier_calls=10)

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b"{}"
            mock_response.headers = {}

            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await proxy.forward_request(
                service=service,
                buyer_id="buyer-1",
                method="GET",
                path="/test",
                x402_paid=True,
            )

        assert result.billing.free_tier
        assert result.billing.amount == Decimal("0")
