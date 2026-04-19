"""
Circle Nanopayments Provider — x402 micropayment integration for AgenticTrade.

Enables AI agents to pay for API calls via Circle Gateway Nanopayments
(gas-free USDC on Arc testnet/mainnet).

Architecture:
  Python agent → NanopaymentsBuyer.call() → Nanopayments Gateway (Node.js)
  → x402 auth → ACF backend → response

The Node.js gateway handles the actual x402 protocol. This module provides
a Python-friendly interface for buyer agents and integrates with the
existing PaymentRouter.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger("nanopayments")

# Default gateway URL (Node.js sidecar)
NANO_GATEWAY_URL = os.environ.get("NANO_GATEWAY_URL", "http://localhost:3402")


@dataclass(frozen=True)
class NanoPaymentResult:
    """Result of a nanopayment API call."""
    success: bool
    status_code: int
    data: dict = field(default_factory=dict)
    amount_usdc: str = "0"
    error: str = ""


class NanopaymentsBuyer:
    """Python client for making paid API calls through the Nanopayments gateway.

    For AI agents that want to discover and pay for services via x402.
    The actual payment signing is handled by the Node.js gateway client;
    this wrapper provides a clean Python API.
    """

    def __init__(
        self,
        gateway_url: str = NANO_GATEWAY_URL,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.gateway_url = gateway_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def health(self) -> dict:
        """Check if the Nanopayments gateway is running."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.gateway_url}/health")
            return resp.json()

    async def discover_services(
        self, query: Optional[str] = None, category: Optional[str] = None,
    ) -> list[dict]:
        """Discover available services (free, no payment required)."""
        params: dict[str, str] = {}
        if query:
            params["query"] = query
        if category:
            params["category"] = category

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.gateway_url}/api/v1/services",
                params=params,
                headers=self._headers(),
            )
            if resp.status_code == 200:
                return resp.json()
            return []

    async def call_service(
        self,
        service_id: str,
        payload: Optional[dict] = None,
    ) -> NanoPaymentResult:
        """Call a paid service through the Nanopayments gateway.

        The gateway handles x402 payment negotiation automatically.
        Requires the Node.js buyer client to be configured with a funded wallet.
        """
        url = f"{self.gateway_url}/api/v1/nano/call/{service_id}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    url,
                    json=payload or {},
                    headers=self._headers(),
                )

                if resp.status_code == 402:
                    return NanoPaymentResult(
                        success=False,
                        status_code=402,
                        error="Payment required — configure buyer wallet with funded Gateway balance",
                    )

                data = resp.json() if resp.status_code < 500 else {}
                return NanoPaymentResult(
                    success=200 <= resp.status_code < 300,
                    status_code=resp.status_code,
                    data=data,
                )
        except httpx.TimeoutException:
            return NanoPaymentResult(
                success=False, status_code=0, error="Gateway timeout"
            )
        except Exception as e:
            return NanoPaymentResult(
                success=False, status_code=0, error=str(e)
            )

    async def get_stats(self) -> dict:
        """Get Nanopayments gateway transaction stats."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.gateway_url}/api/v1/nano/stats")
            return resp.json()


class NanopaymentsProvider:
    """Payment provider for the ACF PaymentRouter.

    Integrates Nanopayments as one of the available payment rails
    alongside x402, PayPal, and NOWPayments.
    """

    def __init__(self, gateway_url: str = NANO_GATEWAY_URL):
        self.gateway_url = gateway_url.rstrip("/")
        self.name = "nanopayments"
        self.enabled = True

    @property
    def is_configured(self) -> bool:
        """Check if the Nanopayments gateway is reachable."""
        try:
            resp = httpx.get(f"{self.gateway_url}/health", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def get_payment_info(self, amount: str, currency: str = "USDC") -> dict:
        """Return payment info for x402 negotiation."""
        return {
            "provider": "nanopayments",
            "protocol": "x402",
            "gateway": self.gateway_url,
            "chain": "Arc Testnet (eip155:5042002)",
            "currency": currency,
            "amount": amount,
            "gas_fee": "$0 (batched settlement)",
            "min_payment": "$0.000001",
        }
