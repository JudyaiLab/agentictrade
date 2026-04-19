"""
NOWPayments Provider — crypto payment gateway via NOWPayments REST API.

Supports 300+ cryptocurrencies. Default: USDT on TRON (usdttrc20).
Docs: https://documenter.getpostman.com/view/7907941/S1a32n38
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from decimal import Decimal
from typing import Optional

import httpx

from .base import (
    PaymentProvider,
    PaymentProviderError,
    PaymentResult,
    PaymentStatus,
)

logger = logging.getLogger("payments.nowpayments")

_API_BASE = "https://api.nowpayments.io/v1"

# NOWPayments status → our PaymentStatus mapping
_STATUS_MAP: dict[str, PaymentStatus] = {
    "waiting": PaymentStatus.pending,
    "confirming": PaymentStatus.pending,
    "confirmed": PaymentStatus.pending,
    "sending": PaymentStatus.pending,
    "partially_paid": PaymentStatus.pending,
    "finished": PaymentStatus.completed,
    "failed": PaymentStatus.failed,
    "refunded": PaymentStatus.failed,
    "expired": PaymentStatus.expired,
}


class NOWPaymentsProvider(PaymentProvider):
    """
    PaymentProvider implementation for NOWPayments.

    Uses the NOWPayments REST API to create and verify crypto payments.
    Supports IPN (Instant Payment Notification) webhook verification.
    """

    _SUPPORTED_CURRENCIES = [
        "USD", "EUR", "BTC", "ETH", "USDT", "USDC", "TRX", "SOL",
        "DOGE", "LTC", "XRP", "BNB", "MATIC", "AVAX",
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        ipn_secret: Optional[str] = None,
        default_pay_currency: str = "usdttrc20",
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        """
        Initialize NOWPayments provider.

        Args:
            api_key: NOWPayments API key. Falls back to NOWPAYMENTS_API_KEY env var.
            ipn_secret: IPN secret for webhook verification. Falls back to env var.
            default_pay_currency: Default crypto to receive (e.g. "usdttrc20").
            http_client: Optional httpx.AsyncClient for dependency injection.
        """
        self._api_key = api_key or os.environ.get("NOWPAYMENTS_API_KEY", "")
        self._ipn_secret = ipn_secret or os.environ.get("NOWPAYMENTS_IPN_SECRET", "")
        self._default_pay_currency = default_pay_currency
        self._external_client = http_client

        if not self._api_key:
            logger.warning("NOWPAYMENTS_API_KEY not set — provider will fail on API calls")

    @property
    def provider_name(self) -> str:
        return "nowpayments"

    @property
    def supported_currencies(self) -> list[str]:
        return list(self._SUPPORTED_CURRENCIES)

    def _headers(self) -> dict[str, str]:
        """Build request headers with API key."""
        return {
            "x-api-key": self._api_key,
            "Content-Type": "application/json",
        }

    def _get_client(self) -> httpx.AsyncClient:
        """Return the injected client or create a new one."""
        if self._external_client is not None:
            return self._external_client
        return httpx.AsyncClient(timeout=30)

    async def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[dict] = None,
    ) -> dict:
        """
        Make an authenticated request to the NOWPayments API.

        Args:
            method: HTTP method (GET, POST).
            path: API path (e.g. "/payment").
            json_body: Optional JSON body for POST requests.

        Returns:
            Parsed JSON response as dict.

        Raises:
            PaymentProviderError: On API errors or network failures.
        """
        if not self._api_key:
            raise PaymentProviderError("NOWPAYMENTS_API_KEY not configured")

        url = f"{_API_BASE}{path}"
        client = self._get_client()
        owns_client = self._external_client is None

        try:
            response = await client.request(
                method=method.upper(),
                url=url,
                headers=self._headers(),
                json=json_body,
            )

            if response.status_code >= 400:
                error_text = response.text[:500]
                raise PaymentProviderError(
                    f"NOWPayments API error {response.status_code}: {error_text}"
                )

            return response.json()

        except httpx.TimeoutException as exc:
            raise PaymentProviderError(f"NOWPayments API timeout: {exc}") from exc
        except httpx.ConnectError as exc:
            raise PaymentProviderError(f"NOWPayments API unreachable: {exc}") from exc
        except PaymentProviderError:
            raise
        except Exception as exc:
            raise PaymentProviderError(f"NOWPayments request failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def create_payment(
        self,
        amount: Decimal,
        currency: str,
        metadata: dict,
    ) -> PaymentResult:
        """
        Create a NOWPayments payment.

        Args:
            amount: Price amount in the price currency.
            currency: Price currency (e.g. "USD", "USDT").
            metadata: Must include "order_id". Optional: "pay_currency", "description".

        Returns:
            PaymentResult with checkout URL and payment details.

        Raises:
            PaymentProviderError: On validation or API errors.
        """
        if amount <= 0:
            raise PaymentProviderError(f"Amount must be positive, got {amount}")

        order_id = metadata.get("order_id", "")
        if not order_id:
            raise PaymentProviderError("metadata must include 'order_id'")

        pay_currency = metadata.get("pay_currency", self._default_pay_currency)

        # Use str() for Decimal->API conversion to avoid float precision loss
        # on large amounts. NOWPayments API accepts numeric strings.
        body: dict = {
            "price_amount": str(amount),
            "price_currency": currency.lower(),
            "pay_currency": pay_currency,
            "order_id": str(order_id),
        }

        if "description" in metadata:
            body["order_description"] = metadata["description"]

        if "ipn_callback_url" in metadata:
            body["ipn_callback_url"] = metadata["ipn_callback_url"]

        # Idempotency: include a unique case ID to prevent duplicate payments on retry.
        idempotency_key = metadata.get("idempotency_key", str(uuid.uuid4()))
        body["case"] = idempotency_key

        data = await self._request("POST", "/payment", json_body=body)

        payment_id = str(data.get("payment_id", ""))
        raw_status = data.get("payment_status", "waiting")
        status = _STATUS_MAP.get(raw_status, PaymentStatus.pending)

        pay_address = data.get("pay_address", "")
        pay_amount = data.get("pay_amount")

        return PaymentResult(
            payment_id=payment_id,
            status=status,
            amount=Decimal(str(amount)),
            currency=currency.upper(),
            checkout_url=data.get("invoice_url"),
            metadata={
                "pay_address": pay_address,
                "pay_amount": str(pay_amount) if pay_amount is not None else None,
                "pay_currency": pay_currency,
                "order_id": order_id,
                "nowpayments_status": raw_status,
            },
        )

    async def verify_payment(self, payment_id: str) -> PaymentStatus:
        """
        Verify the status of a NOWPayments payment.

        Args:
            payment_id: NOWPayments payment ID.

        Returns:
            Current PaymentStatus.

        Raises:
            PaymentProviderError: If verification fails.
        """
        if not payment_id:
            raise PaymentProviderError("payment_id is required")

        data = await self._request("GET", f"/payment/{payment_id}")
        raw_status = data.get("payment_status", "waiting")
        return _STATUS_MAP.get(raw_status, PaymentStatus.pending)

    async def get_payment(self, payment_id: str) -> dict:
        """
        Get full payment details from NOWPayments.

        Args:
            payment_id: NOWPayments payment ID.

        Returns:
            Dict with all payment details from the API.

        Raises:
            PaymentProviderError: If the payment cannot be found.
        """
        if not payment_id:
            raise PaymentProviderError("payment_id is required")

        return await self._request("GET", f"/payment/{payment_id}")

    def verify_ipn_signature(self, body: bytes, signature: str) -> bool:
        """
        Verify IPN webhook signature using HMAC SHA-512.

        NOWPayments signs IPN callbacks with the IPN secret.
        The body must be sorted by keys before hashing.

        Args:
            body: Raw request body bytes.
            signature: The x-nowpayments-sig header value.

        Returns:
            True if signature is valid, False otherwise.
        """
        if not self._ipn_secret:
            logger.warning("IPN secret not configured — cannot verify signature")
            return False

        if not signature:
            return False

        try:
            payload = json.loads(body)
            sorted_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            computed = hmac.new(
                self._ipn_secret.encode("utf-8"),
                sorted_payload.encode("utf-8"),
                hashlib.sha512,
            ).hexdigest()
            return hmac.compare_digest(computed, signature.lower())
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.error("Failed to parse IPN body for signature verification")
            return False
