"""
x402 Payment Provider — wraps existing marketplace/payment.py into PaymentProvider interface.

x402 is a protocol for HTTP-native micropayments on Base network using USDC.
This provider bridges the existing x402 middleware configuration into the
abstract PaymentProvider interface.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Optional

from marketplace.payment import PaymentConfig, extract_payment_tx

from .base import (
    PaymentProvider,
    PaymentProviderError,
    PaymentResult,
    PaymentStatus,
)

logger = logging.getLogger("payments.x402")


class X402Provider(PaymentProvider):
    """
    PaymentProvider implementation for x402 protocol.

    x402 payments are verified by middleware at the HTTP layer.
    This provider wraps the existing PaymentConfig and provides
    a compatible interface for the payment router.
    """

    _SUPPORTED_CURRENCIES = ["USDC"]

    def __init__(self, config: Optional[PaymentConfig] = None):
        """
        Initialize with an x402 PaymentConfig.

        Args:
            config: x402 config. If None, loads from environment.
        """
        self._config = config if config is not None else PaymentConfig.from_env()
        if not self._config.enabled:
            logger.warning("x402 PaymentConfig is disabled (no wallet configured)")

    @property
    def config(self) -> PaymentConfig:
        """Access the underlying x402 config."""
        return self._config

    @property
    def provider_name(self) -> str:
        return "x402"

    @property
    def supported_currencies(self) -> list[str]:
        return list(self._SUPPORTED_CURRENCIES)

    async def create_payment(
        self,
        amount: Decimal,
        currency: str,
        metadata: dict,
    ) -> PaymentResult:
        """
        Create an x402 payment record.

        x402 payments are initiated client-side via the 402 Payment Required
        flow. This method records intent and returns a payment ID for tracking.
        The actual payment verification happens in x402 middleware.

        Args:
            amount: Payment amount in USDC.
            currency: Must be "USDC".
            metadata: Should include service_id, buyer_id, etc.

        Returns:
            PaymentResult with a generated payment_id.

        Raises:
            PaymentProviderError: If provider is disabled or currency unsupported.
        """
        if not self._config.enabled:
            raise PaymentProviderError("x402 provider is disabled (no wallet configured)")

        currency_upper = currency.upper()
        if currency_upper not in self._SUPPORTED_CURRENCIES:
            raise PaymentProviderError(
                f"x402 only supports {self._SUPPORTED_CURRENCIES}, got '{currency}'"
            )

        if amount <= 0:
            raise PaymentProviderError(f"Amount must be positive, got {amount}")

        payment_id = f"x402_{uuid.uuid4().hex[:16]}"

        return PaymentResult(
            payment_id=payment_id,
            status=PaymentStatus.pending,
            amount=amount,
            currency=currency_upper,
            checkout_url=None,
            metadata={
                "network": self._config.network,
                "facilitator_url": self._config.facilitator_url,
                "wallet_address": self._config.wallet_address,
                **metadata,
            },
        )

    async def verify_payment(self, payment_id: str) -> PaymentStatus:
        """
        Verify an x402 payment by checking for transaction header.

        In the x402 flow, payment verification is handled by the middleware.
        This method checks if a payment_id has an associated transaction.

        Args:
            payment_id: The x402 payment ID.

        Returns:
            PaymentStatus based on available information.

        Raises:
            PaymentProviderError: If payment_id is empty.
        """
        if not payment_id:
            raise PaymentProviderError("payment_id is required")

        # x402 verification is middleware-driven.
        # Without access to the middleware state, return pending.
        # Real verification happens via extract_payment_tx on response headers.
        return PaymentStatus.pending

    async def get_payment(self, payment_id: str) -> dict:
        """
        Get x402 payment details.

        Args:
            payment_id: The x402 payment ID.

        Returns:
            Dict with payment details and provider config.

        Raises:
            PaymentProviderError: If payment_id is empty.
        """
        if not payment_id:
            raise PaymentProviderError("payment_id is required")

        return {
            "payment_id": payment_id,
            "provider": self.provider_name,
            "network": self._config.network,
            "facilitator_url": self._config.facilitator_url,
            "wallet_address": self._config.wallet_address,
            "enabled": self._config.enabled,
        }

    def extract_tx_from_headers(self, headers: dict) -> Optional[str]:
        """
        Extract x402 payment transaction hash from response headers.

        Convenience wrapper around marketplace.payment.extract_payment_tx.

        Args:
            headers: HTTP response headers dict.

        Returns:
            Transaction hash string, or None if not present.
        """
        return extract_payment_tx(headers)
