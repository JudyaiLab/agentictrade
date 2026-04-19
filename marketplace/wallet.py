"""
Wallet management for settlement payouts.

Uses CDP SDK v2 (cdp-sdk >=1.30) for USDC transfers on Base network.
Provides both server-side settlement wallets and buyer agent wallets.

CDP SDK v2 API:
  - CdpClient for initialization
  - EvmServerAccount for server-managed wallets
  - EvmServerAccount.transfer(to, amount, token, network) for ERC-20 transfers
  - EvmServerAccount.list_token_balances(network) for balance queries
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

logger = logging.getLogger("wallet")

# USDC contract addresses by network identifier
USDC_ADDRESSES = {
    "base-mainnet": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "base-sepolia": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    "eip155:8453": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "eip155:84532": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
}

# Map x402-style network IDs to CDP network names
NETWORK_MAP = {
    "eip155:8453": "base-mainnet",
    "eip155:84532": "base-sepolia",
    "base-mainnet": "base-mainnet",
    "base-sepolia": "base-sepolia",
}


@dataclass(frozen=True)
class WalletConfig:
    """CDP wallet configuration for settlements."""
    api_key_id: str = ""
    api_key_secret: str = ""
    account_name: str = ""
    network: str = "base-mainnet"
    enabled: bool = False

    @classmethod
    def from_env(cls) -> "WalletConfig":
        """Load wallet config from environment."""
        key_id = os.environ.get("CDP_API_KEY_ID", "")
        key_secret = os.environ.get("CDP_API_KEY_SECRET", "")
        account_name = os.environ.get("CDP_ACCOUNT_NAME", "marketplace-settlement")

        if not all([key_id, key_secret]):
            logger.info("CDP credentials not set — wallet settlements disabled")
            return cls()

        return cls(
            api_key_id=key_id,
            api_key_secret=key_secret,
            account_name=account_name,
            network=os.environ.get("CDP_NETWORK", "base-mainnet"),
            enabled=True,
        )

    @property
    def cdp_network(self) -> str:
        """Convert network identifier to CDP network name."""
        return NETWORK_MAP.get(self.network, self.network)


class WalletManager:
    """
    Manages USDC transfers for provider settlements.

    Uses CDP SDK v2 (CdpClient + EvmServerAccount) for ERC-20 transfers on Base.
    Falls back to logging-only mode when CDP is not configured.
    """

    def __init__(self, config: WalletConfig):
        self.config = config
        self._client = None
        self._account = None
        self._cdp_configured = False

        if config.enabled:
            self._init_cdp()

    def _init_cdp(self) -> None:
        """Initialize CDP SDK v2 client and server account."""
        try:
            from cdp import CdpClient

            self._client = CdpClient(
                api_key_id=self.config.api_key_id,
                api_key_secret=self.config.api_key_secret,
            )
            # CDP SDK v2 get_or_create_account is async.
            # Module init runs inside uvicorn's event loop, so use a
            # dedicated thread with its own loop to await the coroutine.
            import threading

            result = [None, None]  # [account, exception]

            def _resolve():
                try:
                    result[0] = asyncio.run(
                        self._client.evm.get_or_create_account(
                            name=self.config.account_name,
                        )
                    )
                except Exception as exc:
                    result[1] = exc

            t = threading.Thread(target=_resolve)
            t.start()
            t.join(timeout=30)
            if result[1] is not None:
                raise result[1]
            self._account = result[0]
            self._cdp_configured = True
            logger.info(
                "CDP SDK v2 initialized: account=%s address=%s network=%s",
                self.config.account_name,
                self._account.address,
                self.config.cdp_network,
            )
        except ImportError:
            logger.warning(
                "cdp-sdk not installed — settlements will be logged only. "
                "Install with: pip install cdp-sdk>=1.30"
            )
        except Exception as e:
            logger.error("CDP SDK initialization failed: %s", e)

    @property
    def address(self) -> Optional[str]:
        """Get the wallet address, or None if not configured."""
        if self._account is None:
            return None
        return str(self._account.address)

    async def transfer_usdc(
        self,
        to_address: str,
        amount: Decimal,
        idempotency_key: Optional[str] = None,
    ) -> Optional[str]:
        """
        Transfer USDC to a provider wallet.

        Uses CDP EvmServerAccount.transfer() which handles gas automatically
        on Base network (gasless USDC transfers).

        Args:
            idempotency_key: Optional unique key to prevent duplicate transfers
                on retry. Logged for audit tracing.

        Returns transaction hash on success, None on failure.
        """
        if not self._cdp_configured or self._account is None:
            logger.info(
                "Settlement logged (no CDP): %.6f USDC → %s",
                amount, to_address,
            )
            return None

        if amount <= 0:
            logger.warning("Skipping zero/negative amount transfer: %s", amount)
            return None

        usdc_address = USDC_ADDRESSES.get(self.config.cdp_network)
        if not usdc_address:
            logger.error("No USDC address for network: %s", self.config.cdp_network)
            return None

        try:
            # CDP v2: amount is in atomic units (6 decimals for USDC)
            atomic_amount = int(amount * Decimal("1000000"))

            result = self._account.transfer(
                to=to_address,
                amount=atomic_amount,
                token=usdc_address,
                network=self.config.cdp_network,
            )

            tx_hash = str(result.transaction_hash) if hasattr(result, 'transaction_hash') else str(result)
            logger.info(
                "USDC transfer complete: %.6f → %s (tx: %s, idem_key: %s)",
                amount, to_address, tx_hash,
                idempotency_key or "none",
            )
            return tx_hash

        except Exception as e:
            logger.error(
                "USDC transfer failed: %.6f → %s — %s",
                amount, to_address, e,
            )
            return None

    async def get_balance(self) -> Optional[Decimal]:
        """Get USDC balance of the marketplace wallet."""
        if not self._cdp_configured or self._account is None:
            return None

        try:
            balances = self._account.list_token_balances(
                network=self.config.cdp_network
            )

            usdc_address = USDC_ADDRESSES.get(self.config.cdp_network, "").lower()
            for token_balance in balances.token_balances:
                contract = getattr(token_balance, 'contract_address', '')
                if contract and contract.lower() == usdc_address:
                    raw = getattr(token_balance, 'amount', 0)
                    return Decimal(str(raw)) / Decimal("1000000")

            return Decimal("0")

        except Exception as e:
            logger.error("Failed to get wallet balance: %s", e)
            return None

    async def create_agent_wallet(self, agent_id: str) -> Optional[str]:
        """
        Create or retrieve a wallet for an agent.

        Returns the wallet address, or None on failure.
        """
        if self._client is None:
            logger.warning("CDP client not configured — cannot create agent wallet")
            return None

        try:
            account_name = f"agent-{agent_id}"
            account = self._client.evm.get_or_create_account(name=account_name)
            address = str(account.address)
            logger.info("Agent wallet ready: %s → %s", agent_id, address)
            return address

        except Exception as e:
            logger.error("Failed to create agent wallet for %s: %s", agent_id, e)
            return None

    @property
    def is_ready(self) -> bool:
        """Check if wallet is configured and ready for transfers."""
        return self._cdp_configured

    def close(self) -> None:
        """Close the CDP client connection."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
