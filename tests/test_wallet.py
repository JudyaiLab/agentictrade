"""Tests for wallet management (CDP SDK v2)."""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from marketplace.wallet import WalletConfig, WalletManager, USDC_ADDRESSES, NETWORK_MAP


class TestWalletConfig:
    """Test wallet configuration."""

    def test_from_env_with_credentials(self, monkeypatch):
        monkeypatch.setenv("CDP_API_KEY_ID", "test-key-id")
        monkeypatch.setenv("CDP_API_KEY_SECRET", "test-secret")
        monkeypatch.setenv("CDP_ACCOUNT_NAME", "my-wallet")
        monkeypatch.setenv("CDP_NETWORK", "base-mainnet")

        config = WalletConfig.from_env()
        assert config.enabled is True
        assert config.api_key_id == "test-key-id"
        assert config.api_key_secret == "test-secret"
        assert config.account_name == "my-wallet"
        assert config.network == "base-mainnet"

    def test_from_env_without_credentials(self, monkeypatch):
        monkeypatch.delenv("CDP_API_KEY_ID", raising=False)
        monkeypatch.delenv("CDP_API_KEY_SECRET", raising=False)

        config = WalletConfig.from_env()
        assert config.enabled is False

    def test_default_network(self, monkeypatch):
        monkeypatch.setenv("CDP_API_KEY_ID", "key")
        monkeypatch.setenv("CDP_API_KEY_SECRET", "secret")
        monkeypatch.delenv("CDP_NETWORK", raising=False)

        config = WalletConfig.from_env()
        assert config.network == "base-mainnet"

    def test_default_account_name(self, monkeypatch):
        monkeypatch.setenv("CDP_API_KEY_ID", "key")
        monkeypatch.setenv("CDP_API_KEY_SECRET", "secret")
        monkeypatch.delenv("CDP_ACCOUNT_NAME", raising=False)

        config = WalletConfig.from_env()
        assert config.account_name == "marketplace-settlement"

    def test_immutable(self):
        config = WalletConfig()
        with pytest.raises(AttributeError):
            config.enabled = True

    def test_cdp_network_mapping(self):
        config = WalletConfig(network="eip155:8453")
        assert config.cdp_network == "base-mainnet"

        config2 = WalletConfig(network="base-sepolia")
        assert config2.cdp_network == "base-sepolia"


class TestWalletManager:
    """Test wallet manager operations."""

    def test_disabled_when_no_config(self):
        config = WalletConfig()
        manager = WalletManager(config)
        assert manager.is_ready is False

    def test_disabled_when_cdp_not_installed(self):
        config = WalletConfig(
            api_key_id="key",
            api_key_secret="secret",
            enabled=True,
        )
        with patch.dict("sys.modules", {"cdp": None}):
            manager = WalletManager(config)
            assert manager.is_ready is False

    @pytest.mark.asyncio
    async def test_transfer_logs_when_not_configured(self):
        config = WalletConfig()
        manager = WalletManager(config)

        result = await manager.transfer_usdc("0xProvider", Decimal("10.5"))
        assert result is None  # logged only, no tx hash

    @pytest.mark.asyncio
    async def test_transfer_skips_zero_amount(self):
        config = WalletConfig()
        manager = WalletManager(config)
        manager._cdp_configured = True

        result = await manager.transfer_usdc("0xProvider", Decimal("0"))
        assert result is None

    @pytest.mark.asyncio
    async def test_transfer_skips_negative_amount(self):
        config = WalletConfig()
        manager = WalletManager(config)
        manager._cdp_configured = True

        result = await manager.transfer_usdc("0xProvider", Decimal("-5"))
        assert result is None

    @pytest.mark.asyncio
    async def test_get_balance_returns_none_when_not_configured(self):
        config = WalletConfig()
        manager = WalletManager(config)

        balance = await manager.get_balance()
        assert balance is None

    def test_address_none_when_not_configured(self):
        config = WalletConfig()
        manager = WalletManager(config)
        assert manager.address is None

    @pytest.mark.asyncio
    async def test_create_agent_wallet_returns_none_when_no_client(self):
        config = WalletConfig()
        manager = WalletManager(config)
        result = await manager.create_agent_wallet("agent-001")
        assert result is None


class TestUSDCAddresses:
    """Test USDC address constants."""

    def test_base_mainnet_address(self):
        assert USDC_ADDRESSES["base-mainnet"] == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
        assert USDC_ADDRESSES["eip155:8453"] == USDC_ADDRESSES["base-mainnet"]

    def test_base_sepolia_address(self):
        assert USDC_ADDRESSES["base-sepolia"] == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
        assert USDC_ADDRESSES["eip155:84532"] == USDC_ADDRESSES["base-sepolia"]


class TestNetworkMap:
    """Test network identifier mapping."""

    def test_eip155_to_cdp(self):
        assert NETWORK_MAP["eip155:8453"] == "base-mainnet"
        assert NETWORK_MAP["eip155:84532"] == "base-sepolia"

    def test_passthrough(self):
        assert NETWORK_MAP["base-mainnet"] == "base-mainnet"
        assert NETWORK_MAP["base-sepolia"] == "base-sepolia"
