"""
Stripe Agent Checkout Protocol (ACP) provider.

Enables fiat payment support for the marketplace via Stripe's
Agent Checkout Protocol, designed for AI agent-to-agent payments.

Requires: pip install stripe>=8.0
"""
from __future__ import annotations

import logging
import os
import uuid
from decimal import Decimal
from typing import Any, Optional

from .base import (
    PaymentProvider,
    PaymentProviderError,
    PaymentResult,
    PaymentStatus,
)

logger = logging.getLogger("payments.stripe_acp")

try:
    import stripe

    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False
    stripe = None  # type: ignore[assignment]

# Stripe status string -> our PaymentStatus mapping
_STRIPE_STATUS_MAP: dict[str, PaymentStatus] = {
    "open": PaymentStatus.pending,
    "complete": PaymentStatus.completed,
    "expired": PaymentStatus.expired,
    "requires_payment_method": PaymentStatus.pending,
    "requires_confirmation": PaymentStatus.pending,
    "requires_action": PaymentStatus.pending,
    "processing": PaymentStatus.pending,
    "succeeded": PaymentStatus.completed,
    "canceled": PaymentStatus.failed,
}


class StripeACPProvider(PaymentProvider):
    """
    PaymentProvider implementation for Stripe Agent Checkout Protocol (ACP).

    Uses Stripe Checkout Sessions to create fiat payment flows for
    agent-to-agent commerce.  When the ``stripe`` SDK is not installed,
    all payment operations raise ``PaymentProviderError`` with a
    helpful installation message.
    """

    _SUPPORTED_CURRENCIES = ["USD", "EUR", "GBP"]

    def __init__(
        self,
        api_key: Optional[str] = None,
        success_url: str = "https://example.com/success",
        cancel_url: str = "https://example.com/cancel",
    ):
        """
        Initialize StripeACP provider.

        Args:
            api_key: Stripe secret key.  Falls back to ``STRIPE_API_KEY`` env var.
            success_url: URL to redirect to after successful payment.
            cancel_url: URL to redirect to after cancelled payment.
        """
        self._api_key = api_key or os.environ.get("STRIPE_SECRET_KEY", "") or os.environ.get("STRIPE_API_KEY", "")
        self._success_url = success_url
        self._cancel_url = cancel_url

        if not self._api_key:
            logger.warning("STRIPE_SECRET_KEY not set - provider will fail on API calls")

    # ── properties ──

    @property
    def provider_name(self) -> str:
        return "stripe_acp"

    @property
    def supported_currencies(self) -> list[str]:
        return list(self._SUPPORTED_CURRENCIES)

    # ── helpers ──

    def _require_stripe(self) -> None:
        """Raise if the stripe SDK is not installed."""
        if not STRIPE_AVAILABLE:
            raise PaymentProviderError(
                "stripe SDK is not installed. "
                "Install it with: pip install stripe>=8.0"
            )

    def _require_api_key(self) -> None:
        """Raise if no API key is configured."""
        if not self._api_key:
            raise PaymentProviderError("STRIPE_API_KEY not configured")

    def _validate_currency(self, currency: str) -> str:
        """Validate and normalise a currency code. Returns upper-cased currency."""
        upper = currency.upper()
        if upper not in self._SUPPORTED_CURRENCIES:
            raise PaymentProviderError(
                f"Unsupported currency '{currency}'. "
                f"Supported: {self._SUPPORTED_CURRENCIES}"
            )
        return upper

    @staticmethod
    def _validate_amount(amount: Decimal) -> None:
        """Raise if amount is not positive."""
        if amount <= 0:
            raise PaymentProviderError(f"Amount must be positive, got {amount}")

    # ── PaymentProvider interface ──

    async def create_payment(
        self,
        amount: Decimal,
        currency: str,
        metadata: dict,
    ) -> PaymentResult:
        """
        Create a Stripe Checkout Session via ACP.

        Args:
            amount: Payment amount in the given fiat currency.
            currency: ISO 4217 code (``USD``, ``EUR``, ``GBP``).
            metadata: Arbitrary key-value pairs.  Optional keys:
                      ``description``, ``success_url``, ``cancel_url``.

        Returns:
            PaymentResult with ``stripe_acp_`` prefixed payment ID.

        Raises:
            PaymentProviderError: On validation, SDK, or API errors.
        """
        self._require_stripe()
        self._require_api_key()
        self._validate_amount(amount)
        currency_upper = self._validate_currency(currency)

        description = metadata.get("description", "Agent Commerce payment")
        success_url = metadata.get("success_url", self._success_url)
        cancel_url = metadata.get("cancel_url", self._cancel_url)

        # Stripe amounts are in the smallest currency unit (e.g. cents).
        # Use Decimal arithmetic and round() to avoid sub-cent truncation
        # that int() alone would cause (e.g. 19.99 * 100 = 1998 via float).
        amount_cents = int(round(Decimal(str(amount)) * 100))

        # Generate a UUID-based idempotency key to prevent duplicate charges.
        idempotency_key = str(uuid.uuid4())

        try:
            # Pass api_key per-request instead of mutating the global
            # stripe.api_key, which is thread-unsafe under concurrency.
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[
                    {
                        "price_data": {
                            "currency": currency_upper.lower(),
                            "unit_amount": amount_cents,
                            "product_data": {"name": description},
                        },
                        "quantity": 1,
                    }
                ],
                mode="payment",
                success_url=success_url,
                cancel_url=cancel_url,
                metadata=metadata,
                api_key=self._api_key,
                idempotency_key=idempotency_key,
            )

            session_id = session.get("id", "") if isinstance(session, dict) else getattr(session, "id", "")
            checkout_url = session.get("url", None) if isinstance(session, dict) else getattr(session, "url", None)
            raw_status = session.get("status", "open") if isinstance(session, dict) else getattr(session, "status", "open")
            status = _STRIPE_STATUS_MAP.get(raw_status, PaymentStatus.pending)

            payment_id = f"stripe_acp_{session_id}"

            return PaymentResult(
                payment_id=payment_id,
                status=status,
                amount=Decimal(str(amount)),
                currency=currency_upper,
                checkout_url=checkout_url,
                metadata={
                    "stripe_session_id": session_id,
                    "description": description,
                    **{k: v for k, v in metadata.items() if k != "description"},
                },
            )

        except PaymentProviderError:
            raise
        except Exception as exc:
            raise PaymentProviderError(
                f"Stripe API error: {exc}"
            ) from exc

    async def verify_payment(self, payment_id: str) -> PaymentStatus:
        """
        Verify the current status of a Stripe ACP payment.

        Args:
            payment_id: The ``stripe_acp_`` prefixed payment ID.

        Returns:
            Current PaymentStatus.

        Raises:
            PaymentProviderError: If verification fails.
        """
        if not payment_id:
            raise PaymentProviderError("payment_id is required")

        self._require_stripe()
        self._require_api_key()

        session_id = payment_id.removeprefix("stripe_acp_")

        try:
            session = stripe.checkout.Session.retrieve(
                session_id,
                api_key=self._api_key,
            )
            raw_status = session.get("status", "open") if isinstance(session, dict) else getattr(session, "status", "open")
            return _STRIPE_STATUS_MAP.get(raw_status, PaymentStatus.pending)

        except PaymentProviderError:
            raise
        except Exception as exc:
            raise PaymentProviderError(
                f"Stripe verify error: {exc}"
            ) from exc

    async def get_payment(self, payment_id: str) -> dict:
        """
        Retrieve full payment details from Stripe.

        Args:
            payment_id: The ``stripe_acp_`` prefixed payment ID.

        Returns:
            Dict with payment details.

        Raises:
            PaymentProviderError: If the payment cannot be found.
        """
        if not payment_id:
            raise PaymentProviderError("payment_id is required")

        self._require_stripe()
        self._require_api_key()

        session_id = payment_id.removeprefix("stripe_acp_")

        try:
            session = stripe.checkout.Session.retrieve(
                session_id,
                api_key=self._api_key,
            )

            if isinstance(session, dict):
                return {
                    "payment_id": payment_id,
                    "provider": self.provider_name,
                    **session,
                }
            return {
                "payment_id": payment_id,
                "provider": self.provider_name,
                "stripe_session_id": session_id,
                "status": getattr(session, "status", "unknown"),
            }

        except PaymentProviderError:
            raise
        except Exception as exc:
            raise PaymentProviderError(
                f"Stripe get_payment error: {exc}"
            ) from exc
