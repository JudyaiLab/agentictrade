"""
AgentKit Payment Provider — direct agent-to-agent USDC transfers.

Uses CDP SDK v2 (EvmServerAccount) for instant USDC settlements
without x402 middleware overhead. Best for direct agent-to-agent payments
where both parties are registered in the marketplace.

Requires: pip install cdp-sdk>=1.30
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Optional

from .base import (
    PaymentProvider,
    PaymentProviderError,
    PaymentResult,
    PaymentStatus,
)

logger = logging.getLogger("payments.agentkit")

# Module-level in-memory fallback (used when no DB is provided).
# NOTE: this dict is intentionally kept for backward compatibility with tests
# that import and manipulate it directly.
_completed_payments: dict[str, str] = {}  # payment_id -> tx_hash

_AGENTKIT_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS agentkit_completed_payments ("
    "payment_id TEXT PRIMARY KEY, "
    "tx_hash TEXT NOT NULL, "
    "created_at TEXT NOT NULL"
    ")"
)


class AgentKitProvider(PaymentProvider):
    """
    PaymentProvider for direct agent-to-agent USDC transfers via CDP SDK.

    Unlike x402 (middleware-driven) or PayPal (fiat checkout),
    this provider executes immediate on-chain USDC transfers from the
    marketplace settlement wallet to a target address.

    Use cases:
    - Direct agent-to-agent payments within the marketplace
    - Bulk settlement payouts
    - Programmatic transfers from agent wallets

    Persistence: pass a ``db`` (Database instance) to persist completed-payment
    records across restarts. When ``db`` is None the module-level in-memory
    dict is used as a fallback (suitable for tests).
    """

    _SUPPORTED_CURRENCIES = ["USDC"]

    def __init__(
        self,
        wallet_manager: Optional["WalletManager"] = None,
        db=None,
    ):
        """
        Initialize with a WalletManager instance.

        Args:
            wallet_manager: WalletManager from marketplace.wallet.
                           If None, provider operates in dry-run mode.
            db: Optional Database instance for persisting completed payments
                across restarts. Falls back to in-memory dict when None.
        """
        self._wallet = wallet_manager
        self._db = db
        if db is not None:
            self._ensure_payments_table()
        if wallet_manager is None or not wallet_manager.is_ready:
            logger.warning("AgentKit provider initialized without active wallet")

    def _ensure_payments_table(self) -> None:
        """Create the persistent completed-payments table if it does not exist."""
        with self._db.connect() as conn:
            conn.execute(_AGENTKIT_TABLE_SQL)

    def _record_payment(self, payment_id: str, tx_hash: str) -> None:
        """Persist a completed payment (DB or in-memory fallback)."""
        if self._db is not None:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            with self._db.connect() as conn:
                conn.execute(
                    "INSERT INTO agentkit_completed_payments "
                    "(payment_id, tx_hash, created_at) VALUES (?, ?, ?) "
                    "ON CONFLICT (payment_id) DO UPDATE SET tx_hash = excluded.tx_hash",
                    (payment_id, tx_hash, now),
                )
        else:
            _completed_payments[payment_id] = tx_hash

    def _lookup_payment(self, payment_id: str) -> Optional[str]:
        """Return the tx_hash for *payment_id*, or None if not found."""
        if self._db is not None:
            with self._db.connect() as conn:
                row = conn.execute(
                    "SELECT tx_hash FROM agentkit_completed_payments "
                    "WHERE payment_id = ?",
                    (payment_id,),
                ).fetchone()
            return row["tx_hash"] if row else None
        return _completed_payments.get(payment_id)

    @property
    def provider_name(self) -> str:
        return "agentkit"

    @property
    def supported_currencies(self) -> list[str]:
        return list(self._SUPPORTED_CURRENCIES)

    @property
    def wallet_address(self) -> Optional[str]:
        """Get the settlement wallet address."""
        if self._wallet is None:
            return None
        return self._wallet.address

    async def create_payment(
        self,
        amount: Decimal,
        currency: str,
        metadata: dict,
    ) -> PaymentResult:
        """
        Create and execute a direct USDC transfer.

        Unlike x402 which creates a payment intent, this immediately
        executes the on-chain transfer. The returned PaymentResult
        will have status=completed if the transfer succeeds.

        Args:
            amount: USDC amount to transfer.
            currency: Must be "USDC".
            metadata: Must include "to_address" (recipient wallet).
                      Optional: "agent_id", "service_id", "description".

        Returns:
            PaymentResult with tx hash in metadata on success.

        Raises:
            PaymentProviderError: On validation or transfer failure.
        """
        currency_upper = currency.upper()
        if currency_upper not in self._SUPPORTED_CURRENCIES:
            raise PaymentProviderError(
                f"AgentKit only supports {self._SUPPORTED_CURRENCIES}, got '{currency}'"
            )

        if amount <= 0:
            raise PaymentProviderError(f"Amount must be positive, got {amount}")

        to_address = metadata.get("to_address")
        if not to_address:
            raise PaymentProviderError("metadata must include 'to_address'")

        if self._wallet is None or not self._wallet.is_ready:
            raise PaymentProviderError(
                "CDP wallet not configured. Set CDP_API_KEY_ID and CDP_API_KEY_SECRET."
            )

        payment_id = f"agentkit_{uuid.uuid4().hex[:16]}"

        # Generate an idempotency key to prevent duplicate transfers on retry.
        idempotency_key = metadata.get(
            "idempotency_key", str(uuid.uuid4())
        )

        tx_hash = await self._wallet.transfer_usdc(
            to_address=to_address,
            amount=amount,
            idempotency_key=idempotency_key,
        )

        if tx_hash:
            # Record the payment for on-chain verification lookups.
            self._record_payment(payment_id, tx_hash)

            return PaymentResult(
                payment_id=payment_id,
                status=PaymentStatus.completed,
                amount=amount,
                currency=currency_upper,
                checkout_url=None,
                metadata={
                    "tx_hash": tx_hash,
                    "to_address": to_address,
                    "network": self._wallet.config.cdp_network,
                    "from_address": self._wallet.address or "",
                    **{k: v for k, v in metadata.items() if k != "to_address"},
                },
            )

        return PaymentResult(
            payment_id=payment_id,
            status=PaymentStatus.failed,
            amount=amount,
            currency=currency_upper,
            checkout_url=None,
            metadata={
                "to_address": to_address,
                "reason": "USDC transfer failed — check logs",
                **{k: v for k, v in metadata.items() if k != "to_address"},
            },
        )

    async def verify_payment(self, payment_id: str) -> PaymentStatus:
        """
        Verify an AgentKit payment by checking for on-chain evidence.

        Only returns ``completed`` when a transaction hash exists in the
        payment record (proving the transfer was executed on-chain).
        If no on-chain evidence is available, returns ``pending`` and
        logs a warning so callers never assume success without proof.

        Args:
            payment_id: The agentkit_ prefixed payment ID.

        Returns:
            PaymentStatus.completed if tx_hash exists, pending otherwise.

        Raises:
            PaymentProviderError: If payment_id is empty or malformed.
        """
        if not payment_id:
            raise PaymentProviderError("payment_id is required")

        if not payment_id.startswith("agentkit_"):
            raise PaymentProviderError(
                f"Invalid AgentKit payment ID format: {payment_id}"
            )

        # Check for on-chain evidence (tx_hash recorded at creation time).
        tx_hash = self._lookup_payment(payment_id)
        if tx_hash:
            return PaymentStatus.completed

        # No on-chain record found — cannot confirm the transfer.
        logger.warning(
            "verify_payment(%s): no on-chain tx_hash found; "
            "returning pending instead of completed",
            payment_id,
        )
        return PaymentStatus.pending

    async def get_payment(self, payment_id: str) -> dict:
        """
        Get AgentKit payment details.

        Args:
            payment_id: The agentkit_ prefixed payment ID.

        Returns:
            Dict with payment and wallet details.

        Raises:
            PaymentProviderError: If payment_id is empty.
        """
        if not payment_id:
            raise PaymentProviderError("payment_id is required")

        return {
            "payment_id": payment_id,
            "provider": self.provider_name,
            "wallet_address": self._wallet.address if self._wallet else None,
            "network": self._wallet.config.cdp_network if self._wallet else None,
            "wallet_ready": self._wallet.is_ready if self._wallet else False,
        }

    async def create_agent_wallet(self, agent_id: str) -> Optional[str]:
        """
        Create a wallet for a new agent.

        Convenience method that delegates to WalletManager.

        Args:
            agent_id: Unique identifier for the agent.

        Returns:
            Wallet address string, or None if CDP is not configured.
        """
        if self._wallet is None:
            return None
        return await self._wallet.create_agent_wallet(agent_id)
