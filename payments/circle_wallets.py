"""
Circle Programmable Wallets integration for AgenticTrade.

Provides email-based wallet onboarding — new users/agents get a
developer-controlled wallet on Arc without needing to manage private keys
or pay gas fees. Powered by Circle's Wallet-as-a-Service.

Setup:
  1. Get API key from Circle Developer Console
  2. Set CIRCLE_API_KEY and CIRCLE_ENTITY_SECRET in .env
  3. Wallets are created on Arc testnet (blockchain: ARC-TESTNET)

Usage:
  manager = CircleWalletManager()
  wallet = await manager.create_wallet_for_agent("agent_123")
  balance = await manager.get_balance(wallet["wallet_id"])
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("circle_wallets")

CIRCLE_API_KEY = os.environ.get("CIRCLE_API_KEY", "")
CIRCLE_ENTITY_SECRET = os.environ.get("CIRCLE_ENTITY_SECRET", "")
CIRCLE_API_BASE = "https://api.circle.com/v1/w3s"
ARC_TESTNET_USDC = "0x3600000000000000000000000000000000000000"
DEFAULT_BLOCKCHAIN = "ARC-TESTNET"


class CircleWalletError(Exception):
    """Circle Wallet API error."""


class CircleWalletManager:
    """Manage developer-controlled wallets for agents on Arc."""

    def __init__(
        self,
        api_key: str = CIRCLE_API_KEY,
        entity_secret: str = CIRCLE_ENTITY_SECRET,
        blockchain: str = DEFAULT_BLOCKCHAIN,
    ):
        self.api_key = api_key
        self.entity_secret = entity_secret
        self.blockchain = blockchain
        self._wallet_set_id: Optional[str] = os.environ.get("CIRCLE_WALLET_SET_ID")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.entity_secret)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def ensure_wallet_set(self) -> str:
        """Get or create the platform wallet set."""
        if self._wallet_set_id:
            return self._wallet_set_id

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{CIRCLE_API_BASE}/developer/walletSets",
                headers=self._headers(),
                json={"name": "AgenticTrade Platform Wallets"},
            )

        if resp.status_code not in (200, 201):
            raise CircleWalletError(f"Failed to create wallet set: {resp.text[:200]}")

        data = resp.json()
        self._wallet_set_id = data.get("data", {}).get("walletSet", {}).get("id", "")
        return self._wallet_set_id

    async def create_wallet_for_agent(self, agent_id: str) -> dict:
        """Create a new wallet for an agent on Arc.

        Returns dict with wallet_id, address, blockchain.
        The agent doesn't need to manage private keys — we control it.
        """
        if not self.is_configured:
            raise CircleWalletError("CIRCLE_API_KEY and CIRCLE_ENTITY_SECRET required")

        wallet_set_id = await self.ensure_wallet_set()

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{CIRCLE_API_BASE}/developer/wallets",
                headers=self._headers(),
                json={
                    "walletSetId": wallet_set_id,
                    "blockchains": [self.blockchain],
                    "count": 1,
                    "accountType": "EOA",
                    "metadata": [{"name": "agent_id", "value": agent_id}],
                },
            )

        if resp.status_code not in (200, 201):
            raise CircleWalletError(f"Wallet creation failed: {resp.text[:200]}")

        wallets = resp.json().get("data", {}).get("wallets", [])
        if not wallets:
            raise CircleWalletError("No wallet returned from Circle API")

        wallet = wallets[0]
        logger.info(
            "Created Circle wallet for agent %s: %s on %s",
            agent_id, wallet.get("address"), wallet.get("blockchain"),
        )

        return {
            "wallet_id": wallet.get("id", ""),
            "address": wallet.get("address", ""),
            "blockchain": wallet.get("blockchain", ""),
            "agent_id": agent_id,
        }

    async def get_balance(self, wallet_id: str) -> dict:
        """Get USDC balance for a wallet."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{CIRCLE_API_BASE}/wallets/{wallet_id}/balances",
                headers=self._headers(),
            )

        if resp.status_code != 200:
            return {"usdc": "0", "error": resp.text[:100]}

        balances = resp.json().get("data", {}).get("tokenBalances", [])
        usdc_balance = "0"
        for bal in balances:
            if bal.get("token", {}).get("symbol") == "USDC":
                usdc_balance = bal.get("amount", "0")
                break

        return {"usdc": usdc_balance, "wallet_id": wallet_id}

    async def send_usdc(
        self,
        from_wallet_id: str,
        to_address: str,
        amount: str,
    ) -> dict:
        """Send USDC from a managed wallet to an address."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{CIRCLE_API_BASE}/developer/transactions/transfer",
                headers=self._headers(),
                json={
                    "walletId": from_wallet_id,
                    "destinationAddress": to_address,
                    "amounts": [amount],
                    "tokenAddress": ARC_TESTNET_USDC,
                    "blockchain": self.blockchain,
                    "fee": {"type": "level", "config": {"feeLevel": "MEDIUM"}},
                },
            )

        if resp.status_code not in (200, 201):
            raise CircleWalletError(f"Transfer failed: {resp.text[:200]}")

        tx = resp.json().get("data", {})
        return {
            "tx_id": tx.get("id", ""),
            "state": tx.get("state", ""),
            "amount": amount,
            "from": from_wallet_id,
            "to": to_address,
        }
