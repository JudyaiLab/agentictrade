"""
Abstract base class for payment providers.

All payment integrations implement PaymentProvider to ensure
a consistent interface for creating, verifying, and querying payments.
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


class PaymentStatus(enum.Enum):
    """Status of a payment throughout its lifecycle."""
    pending = "pending"
    completed = "completed"
    failed = "failed"
    expired = "expired"


@dataclass(frozen=True)
class PaymentResult:
    """
    Immutable result of a payment creation or verification.

    Attributes:
        payment_id: Unique identifier from the payment provider.
        status: Current payment status.
        amount: Payment amount in the specified currency.
        currency: ISO 4217 currency code or crypto ticker.
        checkout_url: URL for the buyer to complete payment (if applicable).
        metadata: Provider-specific extra data.
    """
    payment_id: str
    status: PaymentStatus
    amount: Decimal
    currency: str
    checkout_url: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class PaymentProviderError(Exception):
    """Base exception for payment provider errors."""


class PaymentProvider(ABC):
    """
    Abstract base class for payment providers.

    Subclasses must implement create_payment, verify_payment, get_payment,
    and expose provider_name and supported_currencies properties.
    """

    @abstractmethod
    async def create_payment(
        self,
        amount: Decimal,
        currency: str,
        metadata: dict,
    ) -> PaymentResult:
        """
        Create a new payment.

        Args:
            amount: Payment amount.
            currency: Currency code (e.g. "USD", "USDT").
            metadata: Arbitrary key-value pairs (order_id, description, etc.).

        Returns:
            PaymentResult with the provider's payment ID and status.

        Raises:
            PaymentProviderError: If payment creation fails.
        """

    @abstractmethod
    async def verify_payment(self, payment_id: str) -> PaymentStatus:
        """
        Verify the current status of a payment.

        Args:
            payment_id: The provider-assigned payment ID.

        Returns:
            Current PaymentStatus.

        Raises:
            PaymentProviderError: If verification fails.
        """

    @abstractmethod
    async def get_payment(self, payment_id: str) -> dict:
        """
        Retrieve full payment details from the provider.

        Args:
            payment_id: The provider-assigned payment ID.

        Returns:
            Dict of provider-specific payment details.

        Raises:
            PaymentProviderError: If the payment cannot be found.
        """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable name of the payment provider."""

    @property
    @abstractmethod
    def supported_currencies(self) -> list[str]:
        """List of currency codes this provider supports."""
