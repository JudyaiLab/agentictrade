"""Tests for AgentKit payment provider."""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import ANY, AsyncMock, MagicMock

from payments.agentkit_provider import AgentKitProvider, _completed_payments
from payments.base import PaymentProviderError, PaymentStatus


class TestAgentKitProviderProperties:
    """Test provider metadata."""

    def test_provider_name(self):
        provider = AgentKitProvider(wallet_manager=None)
        assert provider.provider_name == "agentkit"

    def test_supported_currencies(self):
        provider = AgentKitProvider(wallet_manager=None)
        assert provider.supported_currencies == ["USDC"]

    def test_wallet_address_none_without_manager(self):
        provider = AgentKitProvider(wallet_manager=None)
        assert provider.wallet_address is None

    def test_wallet_address_from_manager(self):
        mock_wallet = MagicMock()
        mock_wallet.address = "0xABC123"
        mock_wallet.is_ready = True
        provider = AgentKitProvider(wallet_manager=mock_wallet)
        assert provider.wallet_address == "0xABC123"


class TestAgentKitCreatePayment:
    """Test payment creation."""

    @pytest.mark.asyncio
    async def test_rejects_unsupported_currency(self):
        provider = AgentKitProvider(wallet_manager=None)
        with pytest.raises(PaymentProviderError, match="only supports"):
            await provider.create_payment(
                Decimal("1.0"), "ETH", {"to_address": "0x123"}
            )

    @pytest.mark.asyncio
    async def test_rejects_zero_amount(self):
        provider = AgentKitProvider(wallet_manager=None)
        with pytest.raises(PaymentProviderError, match="positive"):
            await provider.create_payment(
                Decimal("0"), "USDC", {"to_address": "0x123"}
            )

    @pytest.mark.asyncio
    async def test_rejects_negative_amount(self):
        provider = AgentKitProvider(wallet_manager=None)
        with pytest.raises(PaymentProviderError, match="positive"):
            await provider.create_payment(
                Decimal("-5"), "USDC", {"to_address": "0x123"}
            )

    @pytest.mark.asyncio
    async def test_rejects_missing_to_address(self):
        mock_wallet = MagicMock()
        mock_wallet.is_ready = True
        provider = AgentKitProvider(wallet_manager=mock_wallet)
        with pytest.raises(PaymentProviderError, match="to_address"):
            await provider.create_payment(
                Decimal("1.0"), "USDC", {"description": "test"}
            )

    @pytest.mark.asyncio
    async def test_rejects_when_wallet_not_ready(self):
        mock_wallet = MagicMock()
        mock_wallet.is_ready = False
        provider = AgentKitProvider(wallet_manager=mock_wallet)
        with pytest.raises(PaymentProviderError, match="not configured"):
            await provider.create_payment(
                Decimal("1.0"), "USDC", {"to_address": "0x123"}
            )

    @pytest.mark.asyncio
    async def test_successful_transfer(self):
        mock_wallet = MagicMock()
        mock_wallet.is_ready = True
        mock_wallet.address = "0xSENDER"
        mock_wallet.config = MagicMock()
        mock_wallet.config.cdp_network = "base-sepolia"
        mock_wallet.transfer_usdc = AsyncMock(return_value="0xTX_HASH_123")

        provider = AgentKitProvider(wallet_manager=mock_wallet)
        result = await provider.create_payment(
            Decimal("5.50"),
            "USDC",
            {"to_address": "0xRECEIVER", "agent_id": "agent-001"},
        )

        assert result.status == PaymentStatus.completed
        assert result.payment_id.startswith("agentkit_")
        assert result.amount == Decimal("5.50")
        assert result.currency == "USDC"
        assert result.metadata["tx_hash"] == "0xTX_HASH_123"
        assert result.metadata["to_address"] == "0xRECEIVER"
        assert result.metadata["from_address"] == "0xSENDER"
        assert result.metadata["agent_id"] == "agent-001"

        mock_wallet.transfer_usdc.assert_awaited_once_with(
            to_address="0xRECEIVER",
            amount=Decimal("5.50"),
            idempotency_key=ANY,
        )

    @pytest.mark.asyncio
    async def test_failed_transfer(self):
        mock_wallet = MagicMock()
        mock_wallet.is_ready = True
        mock_wallet.address = "0xSENDER"
        mock_wallet.config = MagicMock()
        mock_wallet.config.cdp_network = "base-sepolia"
        mock_wallet.transfer_usdc = AsyncMock(return_value=None)

        provider = AgentKitProvider(wallet_manager=mock_wallet)
        result = await provider.create_payment(
            Decimal("100.0"),
            "USDC",
            {"to_address": "0xRECEIVER"},
        )

        assert result.status == PaymentStatus.failed
        assert result.payment_id.startswith("agentkit_")
        assert "reason" in result.metadata

    @pytest.mark.asyncio
    async def test_currency_case_insensitive(self):
        mock_wallet = MagicMock()
        mock_wallet.is_ready = True
        mock_wallet.address = "0xSENDER"
        mock_wallet.config = MagicMock()
        mock_wallet.config.cdp_network = "base-sepolia"
        mock_wallet.transfer_usdc = AsyncMock(return_value="0xTX")

        provider = AgentKitProvider(wallet_manager=mock_wallet)
        result = await provider.create_payment(
            Decimal("1.0"), "usdc", {"to_address": "0x123"}
        )
        assert result.currency == "USDC"


class TestAgentKitVerifyPayment:
    """Test payment verification."""

    @pytest.mark.asyncio
    async def test_rejects_empty_id(self):
        provider = AgentKitProvider(wallet_manager=None)
        with pytest.raises(PaymentProviderError, match="required"):
            await provider.verify_payment("")

    @pytest.mark.asyncio
    async def test_rejects_invalid_prefix(self):
        provider = AgentKitProvider(wallet_manager=None)
        with pytest.raises(PaymentProviderError, match="Invalid"):
            await provider.verify_payment("stripe_acp_123")

    @pytest.mark.asyncio
    async def test_returns_pending_without_onchain_evidence(self):
        """verify_payment returns pending when no tx_hash is recorded (R15-H1 fix)."""
        provider = AgentKitProvider(wallet_manager=None)
        # Ensure there's no record for this payment ID
        _completed_payments.pop("agentkit_abc123def456", None)
        status = await provider.verify_payment("agentkit_abc123def456")
        assert status == PaymentStatus.pending

    @pytest.mark.asyncio
    async def test_returns_completed_with_onchain_evidence(self):
        """verify_payment returns completed when a tx_hash IS recorded (R15-H1 fix)."""
        provider = AgentKitProvider(wallet_manager=None)
        _completed_payments["agentkit_evidence_test"] = "0xRealTxHash"
        try:
            status = await provider.verify_payment("agentkit_evidence_test")
            assert status == PaymentStatus.completed
        finally:
            _completed_payments.pop("agentkit_evidence_test", None)


class TestAgentKitGetPayment:
    """Test payment retrieval."""

    @pytest.mark.asyncio
    async def test_rejects_empty_id(self):
        provider = AgentKitProvider(wallet_manager=None)
        with pytest.raises(PaymentProviderError, match="required"):
            await provider.get_payment("")

    @pytest.mark.asyncio
    async def test_returns_details(self):
        mock_wallet = MagicMock()
        mock_wallet.address = "0xWALLET"
        mock_wallet.is_ready = True
        mock_wallet.config = MagicMock()
        mock_wallet.config.cdp_network = "base-mainnet"

        provider = AgentKitProvider(wallet_manager=mock_wallet)
        details = await provider.get_payment("agentkit_test123")

        assert details["payment_id"] == "agentkit_test123"
        assert details["provider"] == "agentkit"
        assert details["wallet_address"] == "0xWALLET"
        assert details["network"] == "base-mainnet"
        assert details["wallet_ready"] is True


class TestAgentKitCreateAgentWallet:
    """Test agent wallet creation."""

    @pytest.mark.asyncio
    async def test_returns_none_without_wallet(self):
        provider = AgentKitProvider(wallet_manager=None)
        result = await provider.create_agent_wallet("agent-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_delegates_to_wallet_manager(self):
        mock_wallet = MagicMock()
        mock_wallet.is_ready = True
        mock_wallet.create_agent_wallet = AsyncMock(return_value="0xNEW_WALLET")

        provider = AgentKitProvider(wallet_manager=mock_wallet)
        result = await provider.create_agent_wallet("agent-001")

        assert result == "0xNEW_WALLET"
        mock_wallet.create_agent_wallet.assert_awaited_once_with("agent-001")


class TestAgentKitIdempotencyKey:
    """Test idempotency key support (R16-M3 fix)."""

    @pytest.mark.asyncio
    async def test_custom_idempotency_key_passed(self):
        """User-supplied idempotency key is forwarded to wallet."""
        mock_wallet = MagicMock()
        mock_wallet.is_ready = True
        mock_wallet.address = "0xSENDER"
        mock_wallet.config = MagicMock()
        mock_wallet.config.cdp_network = "base-sepolia"
        mock_wallet.transfer_usdc = AsyncMock(return_value="0xTX")

        provider = AgentKitProvider(wallet_manager=mock_wallet)
        await provider.create_payment(
            Decimal("1.0"),
            "USDC",
            {"to_address": "0x123", "idempotency_key": "my-custom-key"},
        )

        mock_wallet.transfer_usdc.assert_awaited_once_with(
            to_address="0x123",
            amount=Decimal("1.0"),
            idempotency_key="my-custom-key",
        )

    @pytest.mark.asyncio
    async def test_auto_generated_idempotency_key(self):
        """When no key is provided, a UUID is auto-generated."""
        mock_wallet = MagicMock()
        mock_wallet.is_ready = True
        mock_wallet.address = "0xSENDER"
        mock_wallet.config = MagicMock()
        mock_wallet.config.cdp_network = "base-sepolia"
        mock_wallet.transfer_usdc = AsyncMock(return_value="0xTX")

        provider = AgentKitProvider(wallet_manager=mock_wallet)
        await provider.create_payment(
            Decimal("2.0"),
            "USDC",
            {"to_address": "0x456"},
        )

        call_kwargs = mock_wallet.transfer_usdc.call_args
        assert "idempotency_key" in call_kwargs.kwargs
        assert len(call_kwargs.kwargs["idempotency_key"]) > 10  # UUID string


class TestAgentKitOnChainVerification:
    """Test on-chain tx_hash verification (R15-H1 fix)."""

    @pytest.mark.asyncio
    async def test_create_payment_records_tx_hash(self):
        """Successful create_payment stores tx_hash for later verification."""
        mock_wallet = MagicMock()
        mock_wallet.is_ready = True
        mock_wallet.address = "0xSENDER"
        mock_wallet.config = MagicMock()
        mock_wallet.config.cdp_network = "base-sepolia"
        mock_wallet.transfer_usdc = AsyncMock(return_value="0xRECORDED_TX")

        provider = AgentKitProvider(wallet_manager=mock_wallet)
        result = await provider.create_payment(
            Decimal("3.0"),
            "USDC",
            {"to_address": "0xRECIPIENT"},
        )

        # The tx_hash should be recorded in the module-level dict
        assert _completed_payments.get(result.payment_id) == "0xRECORDED_TX"

        # Verify_payment should now return completed
        status = await provider.verify_payment(result.payment_id)
        assert status == PaymentStatus.completed

        # Cleanup
        _completed_payments.pop(result.payment_id, None)

    @pytest.mark.asyncio
    async def test_failed_transfer_not_recorded(self):
        """Failed transfers should NOT be recorded as completed."""
        mock_wallet = MagicMock()
        mock_wallet.is_ready = True
        mock_wallet.address = "0xSENDER"
        mock_wallet.config = MagicMock()
        mock_wallet.config.cdp_network = "base-sepolia"
        mock_wallet.transfer_usdc = AsyncMock(return_value=None)

        provider = AgentKitProvider(wallet_manager=mock_wallet)
        result = await provider.create_payment(
            Decimal("3.0"),
            "USDC",
            {"to_address": "0xRECIPIENT"},
        )

        assert result.status == PaymentStatus.failed
        assert result.payment_id not in _completed_payments
