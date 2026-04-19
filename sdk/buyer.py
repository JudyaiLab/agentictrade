"""
Buyer Agent SDK — automated payment handling for x402-protected APIs.

Provides a high-level client that:
1. Discovers services on the marketplace
2. Automatically handles HTTP 402 Payment Required flows
3. Signs payments using a CDP wallet
4. Tracks payment history

Usage:
    from sdk.buyer import BuyerAgent

    buyer = BuyerAgent(
        marketplace_url="http://localhost:8000",
        api_key="key_id:secret",
        cdp_api_key_id="...",
        cdp_api_key_secret="...",
    )

    # Call a paid API — payment is handled automatically
    result = await buyer.call_service("svc-weather-001", path="/forecast")

Requires: pip install httpx cdp-sdk>=1.30
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

import httpx

logger = logging.getLogger("sdk.buyer")


@dataclass(frozen=True)
class PaymentRecord:
    """Record of a completed payment."""
    service_id: str
    amount: str
    currency: str
    network: str
    tx_hash: Optional[str] = None
    payment_id: Optional[str] = None


class BuyerAgentError(Exception):
    """Buyer agent operation errors."""


class BuyerAgent:
    """
    High-level buyer agent that auto-handles x402 payments.

    When calling an x402-protected API endpoint, the flow is:
    1. Send initial request
    2. Receive HTTP 402 with PAYMENT-REQUIRED header
    3. Parse payment requirements (price, network, pay_to)
    4. Sign payment with CDP wallet
    5. Re-send request with PAYMENT-SIGNATURE header
    6. Receive HTTP 200 with the actual response
    """

    def __init__(
        self,
        marketplace_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        cdp_api_key_id: Optional[str] = None,
        cdp_api_key_secret: Optional[str] = None,
        wallet_name: Optional[str] = None,
        network: str = "base-sepolia",
        timeout: float = 30.0,
    ):
        """
        Args:
            marketplace_url: ACF marketplace server URL.
            api_key: API key in format "key_id:secret".
            cdp_api_key_id: CDP API key ID for wallet operations.
            cdp_api_key_secret: CDP API key secret.
            wallet_name: Name for the agent's CDP wallet account.
            network: Network for payments (base-sepolia, base-mainnet).
            timeout: HTTP request timeout in seconds.
        """
        self.marketplace_url = marketplace_url.rstrip("/")
        self.api_key = api_key
        self.network = network
        self._payment_history: list[PaymentRecord] = []

        self._http = httpx.AsyncClient(timeout=timeout)

        self._wallet = None
        self._wallet_address: Optional[str] = None

        if cdp_api_key_id and cdp_api_key_secret:
            self._init_wallet(cdp_api_key_id, cdp_api_key_secret, wallet_name)

    def _init_wallet(
        self,
        api_key_id: str,
        api_key_secret: str,
        wallet_name: Optional[str],
    ) -> None:
        """Initialize CDP wallet for payment signing."""
        try:
            from cdp import CdpClient

            client = CdpClient(
                api_key_id=api_key_id,
                api_key_secret=api_key_secret,
            )
            name = wallet_name or "buyer-agent"
            self._wallet = client.evm.get_or_create_account(name=name)
            self._wallet_address = str(self._wallet.address)
            logger.info("Buyer wallet ready: %s", self._wallet_address)

        except ImportError:
            logger.warning(
                "cdp-sdk not installed — x402 payments disabled. "
                "Install with: pip install cdp-sdk>=1.30"
            )
        except Exception as e:
            logger.error("Failed to initialize buyer wallet: %s", e)

    @property
    def wallet_address(self) -> Optional[str]:
        """Get the buyer agent's wallet address."""
        return self._wallet_address

    @property
    def payment_history(self) -> list[PaymentRecord]:
        """Get the list of completed payments."""
        return list(self._payment_history)

    def _headers(self) -> dict[str, str]:
        """Build request headers."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def discover_services(
        self,
        query: Optional[str] = None,
        category: Optional[str] = None,
        max_price: Optional[str] = None,
    ) -> list[dict]:
        """
        Search the marketplace for available services.

        Args:
            query: Text search query.
            category: Filter by category.
            max_price: Maximum price per call filter.

        Returns:
            List of service dicts matching the criteria.
        """
        params = {
            k: v for k, v in {
                "q": query,
                "category": category,
                "max_price": max_price,
            }.items() if v is not None
        }

        resp = await self._http.get(
            f"{self.marketplace_url}/api/v1/discover",
            headers=self._headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("services", [])

    async def call_service(
        self,
        service_id: str,
        method: str = "GET",
        path: str = "/",
        body: Optional[dict] = None,
        params: Optional[dict] = None,
        auto_pay: bool = True,
    ) -> dict:
        """
        Call a service through the marketplace proxy.

        Automatically handles x402 payment if the service requires it.

        Args:
            service_id: Target service identifier.
            method: HTTP method.
            path: Path appended to the service endpoint.
            body: JSON body for POST/PUT requests.
            params: Query parameters.
            auto_pay: If True, automatically handle 402 payment.

        Returns:
            The service response as a dict.

        Raises:
            BuyerAgentError: On payment or API errors.
        """
        clean_path = path.lstrip("/")
        url = f"{self.marketplace_url}/api/v1/proxy/{service_id}/{clean_path}"

        resp = await self._http.request(
            method,
            url,
            headers=self._headers(),
            json=body,
            params=params,
        )

        if resp.status_code == 402 and auto_pay:
            return await self._handle_402(resp, method, url, body, params, service_id)

        if resp.status_code >= 400:
            raise BuyerAgentError(
                f"Service call failed: HTTP {resp.status_code} — {resp.text[:200]}"
            )

        try:
            return resp.json()
        except Exception:
            return {"body": resp.text, "status_code": resp.status_code}

    async def _handle_402(
        self,
        initial_response: httpx.Response,
        method: str,
        url: str,
        body: Optional[dict],
        params: Optional[dict],
        service_id: str,
    ) -> dict:
        """
        Handle HTTP 402 Payment Required response.

        Parses the PAYMENT-REQUIRED header, signs a payment,
        and re-sends the request with PAYMENT-SIGNATURE header.
        """
        if self._wallet is None:
            raise BuyerAgentError(
                "Received HTTP 402 but no wallet configured for payment. "
                "Initialize BuyerAgent with cdp_api_key_id and cdp_api_key_secret."
            )

        payment_header = (
            initial_response.headers.get("payment-required")
            or initial_response.headers.get("PAYMENT-REQUIRED")
        )
        if not payment_header:
            raise BuyerAgentError(
                "HTTP 402 received but no PAYMENT-REQUIRED header found"
            )

        try:
            payment_req = json.loads(base64.b64decode(payment_header))
        except Exception as e:
            raise BuyerAgentError(
                f"Failed to parse PAYMENT-REQUIRED header: {e}"
            ) from e

        price = payment_req.get("maxAmountRequired", payment_req.get("price", "0"))
        network = payment_req.get("network", self.network)
        pay_to = payment_req.get("payTo", payment_req.get("pay_to", ""))
        scheme = payment_req.get("scheme", "exact")

        logger.info(
            "x402 payment required: %s to %s on %s (scheme=%s)",
            price, pay_to, network, scheme,
        )

        try:
            payment_payload = await self._sign_payment(
                price=price,
                pay_to=pay_to,
                network=network,
            )
        except Exception as e:
            raise BuyerAgentError(f"Payment signing failed: {e}") from e

        headers = {
            **self._headers(),
            "Payment-Signature": base64.b64encode(
                json.dumps(payment_payload).encode()
            ).decode(),
        }

        resp = await self._http.request(
            method, url, headers=headers, json=body, params=params,
        )

        if resp.status_code >= 400:
            raise BuyerAgentError(
                f"Paid request failed: HTTP {resp.status_code} — {resp.text[:200]}"
            )

        tx_hash = (
            resp.headers.get("x-payment-transaction")
            or resp.headers.get("X-Payment-Transaction")
        )

        self._payment_history.append(PaymentRecord(
            service_id=service_id,
            amount=str(price),
            currency="USDC",
            network=network,
            tx_hash=tx_hash,
        ))

        logger.info(
            "Payment completed: %s USDC for %s (tx: %s)",
            price, service_id, tx_hash or "pending",
        )

        try:
            return resp.json()
        except Exception:
            return {"body": resp.text, "status_code": resp.status_code}

    async def _sign_payment(
        self,
        price: str,
        pay_to: str,
        network: str,
    ) -> dict:
        """
        Sign an x402 payment using the CDP wallet.

        Returns a payment payload dict containing the signed authorization.
        """
        message = json.dumps({
            "price": price,
            "payTo": pay_to,
            "network": network,
            "from": self._wallet_address,
        }, sort_keys=True)

        signature = self._wallet.sign_message(message=message)

        return {
            "scheme": "exact",
            "network": network,
            "payload": {
                "signature": str(signature),
                "from": self._wallet_address,
                "payTo": pay_to,
                "price": price,
            },
        }

    async def get_balance(self) -> Optional[str]:
        """
        Get the buyer agent's USDC balance.

        Returns:
            Balance as a string (e.g., "10.50"), or None if wallet not configured.
        """
        if self._wallet is None:
            return None

        try:
            from marketplace.wallet import USDC_ADDRESSES, NETWORK_MAP

            cdp_network = NETWORK_MAP.get(self.network, self.network)
            balances = self._wallet.list_token_balances(network=cdp_network)

            usdc_addr = USDC_ADDRESSES.get(cdp_network, "").lower()
            for tb in balances.token_balances:
                contract = getattr(tb, 'contract_address', '')
                if contract and contract.lower() == usdc_addr:
                    raw = getattr(tb, 'amount', 0)
                    return str(Decimal(str(raw)) / Decimal("1000000"))

            return "0"
        except Exception as e:
            logger.error("Failed to get balance: %s", e)
            return None

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._http.aclose()

    async def __aenter__(self) -> "BuyerAgent":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
