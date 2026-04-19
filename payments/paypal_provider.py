"""
PayPal payment provider.

Enables fiat payment support for the marketplace via PayPal's
Orders API v2, designed for agent-to-agent payments.

Uses httpx (already a project dependency) — no extra SDK required.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from decimal import Decimal
from typing import Any, Optional

import httpx

from .base import (
    PaymentProvider,
    PaymentProviderError,
    PaymentResult,
    PaymentStatus,
)

logger = logging.getLogger("payments.paypal")

_PAYPAL_STATUS_MAP: dict[str, PaymentStatus] = {
    "CREATED": PaymentStatus.pending,
    "SAVED": PaymentStatus.pending,
    "APPROVED": PaymentStatus.pending,
    "VOIDED": PaymentStatus.failed,
    "COMPLETED": PaymentStatus.completed,
    "PAYER_ACTION_REQUIRED": PaymentStatus.pending,
}

_PAYPAL_BASE_URLS = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}


class PayPalProvider(PaymentProvider):
    """
    PaymentProvider implementation for PayPal Orders API v2.

    Uses PayPal REST API with OAuth2 client credentials for
    agent-to-agent commerce.  No extra SDK required — uses httpx.
    """

    _SUPPORTED_CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD", "JPY"]

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        mode: str = "sandbox",
        return_url: str = "https://example.com/success",
        cancel_url: str = "https://example.com/cancel",
    ):
        self._client_id = client_id if client_id is not None else os.environ.get("PAYPAL_CLIENT_ID", "")
        self._client_secret = client_secret if client_secret is not None else os.environ.get("PAYPAL_CLIENT_SECRET", "")
        self._mode = mode if mode in _PAYPAL_BASE_URLS else "sandbox"
        self._base_url = _PAYPAL_BASE_URLS[self._mode]
        self._return_url = return_url
        self._cancel_url = cancel_url

        # Cached access token
        self._access_token: str = ""
        self._token_expires_at: float = 0.0

        if not self._client_id or not self._client_secret:
            logger.warning("PAYPAL_CLIENT_ID/SECRET not set - provider will fail on API calls")

    # ── properties ──

    @property
    def provider_name(self) -> str:
        return "paypal"

    @property
    def supported_currencies(self) -> list[str]:
        return list(self._SUPPORTED_CURRENCIES)

    # ── helpers ──

    def _require_credentials(self) -> None:
        if not self._client_id or not self._client_secret:
            raise PaymentProviderError("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are required")

    def _validate_currency(self, currency: str) -> str:
        upper = currency.upper()
        if upper not in self._SUPPORTED_CURRENCIES:
            raise PaymentProviderError(
                f"Unsupported currency '{currency}'. "
                f"Supported: {self._SUPPORTED_CURRENCIES}"
            )
        return upper

    @staticmethod
    def _validate_amount(amount: Decimal) -> None:
        if amount <= 0:
            raise PaymentProviderError(f"Amount must be positive, got {amount}")

    def _get_access_token(self) -> str:
        """Get or refresh OAuth2 access token."""
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        self._require_credentials()

        try:
            resp = httpx.post(
                f"{self._base_url}/v1/oauth2/token",
                auth=(self._client_id, self._client_secret),
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json"},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            self._token_expires_at = time.time() + data.get("expires_in", 3600)
            return self._access_token
        except Exception as exc:
            raise PaymentProviderError(f"PayPal OAuth2 error: {exc}") from exc

    def _headers(self) -> dict[str, str]:
        token = self._get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    # ── PaymentProvider interface ──

    async def create_payment(
        self,
        amount: Decimal,
        currency: str,
        metadata: dict,
    ) -> PaymentResult:
        """
        Create a PayPal Order.

        Args:
            amount: Payment amount in fiat currency.
            currency: ISO 4217 code.
            metadata: Arbitrary key-value pairs. Optional keys:
                      ``description``, ``return_url``, ``cancel_url``.

        Returns:
            PaymentResult with ``paypal_`` prefixed payment ID.
        """
        self._require_credentials()
        self._validate_amount(amount)
        currency_upper = self._validate_currency(currency)

        description = metadata.get("description", "Agent Commerce payment")
        return_url = metadata.get("return_url", self._return_url)
        cancel_url = metadata.get("cancel_url", self._cancel_url)

        request_id = str(uuid.uuid4())

        order_body: dict[str, Any] = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": request_id,
                    "description": description[:127],
                    "amount": {
                        "currency_code": currency_upper,
                        "value": str(amount.quantize(Decimal("0.01"))),
                    },
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                        "user_action": "PAY_NOW",
                        "brand_name": "AgenticTrade",
                    }
                }
            },
        }

        try:
            resp = httpx.post(
                f"{self._base_url}/v2/checkout/orders",
                json=order_body,
                headers={**self._headers(), "PayPal-Request-Id": request_id},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()

            order_id = data.get("id", "")
            raw_status = data.get("status", "CREATED")
            status = _PAYPAL_STATUS_MAP.get(raw_status, PaymentStatus.pending)

            # Extract approval URL for redirect
            checkout_url = None
            for link in data.get("links", []):
                if link.get("rel") == "payer-action":
                    checkout_url = link.get("href")
                    break

            payment_id = f"paypal_{order_id}"

            return PaymentResult(
                payment_id=payment_id,
                status=status,
                amount=Decimal(str(amount)),
                currency=currency_upper,
                checkout_url=checkout_url,
                metadata={
                    "paypal_order_id": order_id,
                    "description": description,
                    "request_id": request_id,
                    **{k: v for k, v in metadata.items() if k not in ("description", "return_url", "cancel_url")},
                },
            )

        except PaymentProviderError:
            raise
        except Exception as exc:
            raise PaymentProviderError(f"PayPal API error: {exc}") from exc

    async def verify_payment(self, payment_id: str) -> PaymentStatus:
        """
        Verify the current status of a PayPal order.

        Args:
            payment_id: The ``paypal_`` prefixed payment ID.

        Returns:
            Current PaymentStatus.
        """
        if not payment_id:
            raise PaymentProviderError("payment_id is required")

        self._require_credentials()

        order_id = payment_id.removeprefix("paypal_")

        try:
            resp = httpx.get(
                f"{self._base_url}/v2/checkout/orders/{order_id}",
                headers=self._headers(),
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            raw_status = data.get("status", "CREATED")
            return _PAYPAL_STATUS_MAP.get(raw_status, PaymentStatus.pending)

        except PaymentProviderError:
            raise
        except Exception as exc:
            raise PaymentProviderError(f"PayPal verify error: {exc}") from exc

    async def get_payment(self, payment_id: str) -> dict:
        """
        Retrieve full payment details from PayPal.

        Args:
            payment_id: The ``paypal_`` prefixed payment ID.

        Returns:
            Dict with payment details.
        """
        if not payment_id:
            raise PaymentProviderError("payment_id is required")

        self._require_credentials()

        order_id = payment_id.removeprefix("paypal_")

        try:
            resp = httpx.get(
                f"{self._base_url}/v2/checkout/orders/{order_id}",
                headers=self._headers(),
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()

            return {
                "payment_id": payment_id,
                "provider": self.provider_name,
                "paypal_order_id": order_id,
                **data,
            }

        except PaymentProviderError:
            raise
        except Exception as exc:
            raise PaymentProviderError(f"PayPal get_payment error: {exc}") from exc
