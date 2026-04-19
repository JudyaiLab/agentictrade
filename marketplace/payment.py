"""
x402 Payment Integration — Server-side payment verification and configuration.

Provides x402 middleware setup for FastAPI and payment route configuration.
Uses x402 v2.x SDK with EVM ExactScheme on Base network.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

logger = logging.getLogger("payment")


@dataclass(frozen=True)
class PaymentConfig:
    """x402 payment configuration."""
    wallet_address: str
    network: str = "eip155:8453"  # Base Mainnet
    facilitator_url: str = "https://x402.org/facilitator"
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "PaymentConfig":
        """Load payment config from environment variables."""
        address = os.environ.get("WALLET_ADDRESS", "")
        if not address:
            logger.warning("WALLET_ADDRESS not set — x402 payments disabled")
            return cls(wallet_address="", enabled=False)

        return cls(
            wallet_address=address,
            network=os.environ.get("NETWORK", "eip155:8453"),
            facilitator_url=os.environ.get(
                "FACILITATOR_URL", "https://x402.org/facilitator"
            ),
            enabled=True,
        )


def build_x402_routes(
    services: list[dict],
    pay_to: str,
    network: str,
) -> dict:
    """
    Build x402 route config from registered services.

    Maps each service's proxy route to its pricing.
    Returns a dict suitable for PaymentMiddlewareASGI.
    """
    try:
        from x402.http import PaymentOption
        from x402.http.types import RouteConfig
    except ImportError:
        logger.error("x402 SDK not installed. Run: pip install 'x402[fastapi]'")
        return {}

    routes = {}
    for svc in services:
        if svc.get("payment_method") not in ("x402", "both"):
            continue
        if svc.get("status") != "active":
            continue

        price = Decimal(str(svc.get("price_per_call", 0)))
        if price <= 0:
            continue

        service_id = svc["id"]
        route_key = f"ANY /api/v1/proxy/{service_id}"

        routes[route_key] = RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=pay_to,
                    price=f"${price}",
                    network=network,
                ),
            ],
            description=svc.get("description", svc.get("name", "")),
        )

    logger.info("Built x402 routes for %d services", len(routes))
    return routes


def setup_x402_middleware(app, config: PaymentConfig, services: list[dict]) -> bool:
    """
    Attach x402 PaymentMiddlewareASGI to a FastAPI app.

    Returns True if middleware was added, False if x402 is disabled or unavailable.
    """
    if not config.enabled:
        logger.info("x402 payments disabled (no wallet configured)")
        return False

    try:
        from x402.http import FacilitatorConfig, HTTPFacilitatorClient
        from x402.http.middleware.fastapi import PaymentMiddlewareASGI
        from x402.mechanisms.evm.exact import ExactEvmServerScheme
        from x402.server import x402ResourceServer
    except ImportError:
        logger.warning(
            "x402 SDK not installed — running without payment middleware. "
            "Install with: pip install 'x402[fastapi]'"
        )
        return False

    facilitator = HTTPFacilitatorClient(
        FacilitatorConfig(url=config.facilitator_url)
    )

    server = x402ResourceServer(facilitator)
    server.register(config.network, ExactEvmServerScheme())

    routes = build_x402_routes(
        services=services,
        pay_to=config.wallet_address,
        network=config.network,
    )

    if not routes:
        logger.info("No x402-enabled services found — middleware not attached")
        return False

    app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
    logger.info(
        "x402 middleware attached: %d routes, network=%s, facilitator=%s",
        len(routes),
        config.network,
        config.facilitator_url,
    )
    return True


def extract_payment_tx(headers: dict) -> Optional[str]:
    """
    Extract x402 payment transaction hash from response headers.

    After x402 middleware verifies payment, the settlement response
    includes transaction details in headers.
    """
    # v2 header: PAYMENT-RESPONSE, v1 fallback: X-Payment-Transaction
    tx = (
        headers.get("payment-response")
        or headers.get("PAYMENT-RESPONSE")
        or headers.get("x-payment-transaction")
        or headers.get("X-Payment-Transaction")
    )
    return tx if tx else None
