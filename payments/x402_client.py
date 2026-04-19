"""
x402 Client — Buyer-side payment client for AI agents.

Enables AI agents to make paid API calls to x402-protected endpoints
using automatic USDC micropayments on Base network.

Uses EthAccountSigner (standalone private key) — no CDP API keys required.

Usage:
    client = X402AgentClient.from_env()
    result = await client.call_api("https://agentictrade.io/api/v1/proxy/svc_123")
    print(result.data)       # API response
    print(result.tx_hash)    # On-chain settlement TX
    print(result.cost_usdc)  # Amount paid
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger("payments.x402_client")


@dataclass(frozen=True)
class ApiCallResult:
    """Immutable result of a paid API call."""
    status_code: int
    data: Any
    tx_hash: Optional[str] = None
    cost_usdc: Optional[Decimal] = None
    headers: dict = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class X402ClientError(Exception):
    """Error during x402 client operation."""


class X402AgentClient:
    """
    x402-enabled HTTP client for AI agent buyers.

    Automatically handles the 402 payment flow:
    1. Send request → get 402 + PAYMENT-REQUIRED header
    2. Parse payment options, sign EIP-712 authorization
    3. Retry with PAYMENT-SIGNATURE header
    4. Return 200 + data + settlement TX hash
    """

    def __init__(
        self,
        private_key: str,
        network: str = "eip155:8453",
    ):
        """
        Initialize x402 agent client.

        Args:
            private_key: Hex private key (0x-prefixed).
            network: CAIP-2 network identifier.
                     eip155:8453 = Base Mainnet
                     eip155:84532 = Base Sepolia (testnet)
        """
        if not private_key:
            raise X402ClientError("private_key is required")

        self._private_key = private_key
        self._network = network
        self._client = None
        self._http_client = None
        self._wallet_address = None

    @classmethod
    def from_env(cls) -> "X402AgentClient":
        """Create client from environment variables.

        Reads:
            EVM_PRIVATE_KEY or BUYER_PRIVATE_KEY: Hex private key
            NETWORK: CAIP-2 network (default: eip155:8453)
        """
        key = os.environ.get("EVM_PRIVATE_KEY") or os.environ.get("BUYER_PRIVATE_KEY", "")
        if not key:
            raise X402ClientError(
                "EVM_PRIVATE_KEY or BUYER_PRIVATE_KEY environment variable required"
            )
        network = os.environ.get("NETWORK", "eip155:8453")
        return cls(private_key=key, network=network)

    def _ensure_initialized(self) -> None:
        """Lazy-initialize x402 SDK components."""
        if self._client is not None:
            return

        try:
            from eth_account import Account
            from x402 import x402Client
            from x402.http import x402HTTPClient
            from x402.mechanisms.evm import EthAccountSigner
            from x402.mechanisms.evm.exact.register import register_exact_evm_client
        except ImportError as e:
            raise X402ClientError(
                "x402 SDK not installed. Run: pip install 'x402[httpx,evm]'"
            ) from e

        account = Account.from_key(self._private_key)
        self._wallet_address = account.address

        self._client = x402Client()
        register_exact_evm_client(self._client, EthAccountSigner(account))
        self._http_client = x402HTTPClient(self._client)

        logger.info(
            "x402 agent client initialized: wallet=%s, network=%s",
            self._wallet_address, self._network,
        )

    @property
    def wallet_address(self) -> Optional[str]:
        """Return the wallet address (available after first call)."""
        if self._wallet_address is None:
            self._ensure_initialized()
        return self._wallet_address

    async def call_api(
        self,
        url: str,
        method: str = "GET",
        json_body: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: float = 30.0,
    ) -> ApiCallResult:
        """
        Make a paid API call to an x402-protected endpoint.

        Payment is handled automatically by the x402 SDK:
        - First request gets 402 + payment options
        - SDK signs payment authorization
        - Retries with signed payment
        - Returns response + settlement details

        Args:
            url: Full URL of the API endpoint.
            method: HTTP method (GET, POST, etc.).
            json_body: JSON body for POST/PUT requests.
            headers: Additional HTTP headers.
            timeout: Request timeout in seconds.

        Returns:
            ApiCallResult with response data and payment details.
        """
        self._ensure_initialized()

        try:
            from x402.http.clients import x402HttpxClient
        except ImportError as e:
            raise X402ClientError(
                "x402 httpx client not installed. Run: pip install 'x402[httpx,evm]'"
            ) from e

        request_headers = dict(headers) if headers else {}

        try:
            async with x402HttpxClient(self._client) as http:
                if method.upper() == "GET":
                    response = await http.get(
                        url, headers=request_headers, timeout=timeout,
                    )
                elif method.upper() == "POST":
                    response = await http.post(
                        url, json=json_body, headers=request_headers, timeout=timeout,
                    )
                elif method.upper() == "PUT":
                    response = await http.put(
                        url, json=json_body, headers=request_headers, timeout=timeout,
                    )
                elif method.upper() == "DELETE":
                    response = await http.delete(
                        url, headers=request_headers, timeout=timeout,
                    )
                else:
                    response = await http.request(
                        method.upper(), url,
                        json=json_body, headers=request_headers, timeout=timeout,
                    )

                await response.aread()

                # Extract payment settlement details
                tx_hash = None
                cost_usdc = None
                try:
                    settle_response = self._http_client.get_payment_settle_response(
                        lambda name: response.headers.get(name)
                    )
                    if settle_response:
                        tx_hash = getattr(settle_response, "transaction_hash", None)
                        amount = getattr(settle_response, "amount", None)
                        if amount is not None:
                            cost_usdc = Decimal(str(amount))
                except (ValueError, AttributeError):
                    pass

                # Parse response body
                data = None
                if response.headers.get("content-type", "").startswith("application/json"):
                    data = response.json()
                else:
                    data = response.text

                resp_headers = dict(response.headers)

                if response.status_code == 402:
                    return ApiCallResult(
                        status_code=402,
                        data=None,
                        error="Payment required but could not complete. Check wallet balance.",
                        headers=resp_headers,
                    )

                return ApiCallResult(
                    status_code=response.status_code,
                    data=data,
                    tx_hash=tx_hash,
                    cost_usdc=cost_usdc,
                    headers=resp_headers,
                )

        except Exception as e:
            logger.error("x402 API call failed: %s %s — %s", method, url, e)
            return ApiCallResult(
                status_code=0,
                data=None,
                error=str(e),
            )

    async def get_balance(self) -> Optional[Decimal]:
        """
        Check USDC balance of the buyer wallet on the configured network.

        Returns:
            Balance in USDC, or None if check fails.
        """
        self._ensure_initialized()

        try:
            from web3 import Web3

            # USDC contract addresses by network
            usdc_addresses = {
                "eip155:8453": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",   # Base Mainnet
                "eip155:84532": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # Base Sepolia
            }

            usdc_addr = usdc_addresses.get(self._network)
            if not usdc_addr:
                logger.warning("Unknown network for USDC balance check: %s", self._network)
                return None

            rpc_urls = {
                "eip155:8453": "https://mainnet.base.org",
                "eip155:84532": "https://sepolia.base.org",
            }
            rpc = rpc_urls.get(self._network, "https://mainnet.base.org")

            w3 = Web3(Web3.HTTPProvider(rpc))
            erc20_abi = [
                {
                    "name": "balanceOf",
                    "type": "function",
                    "stateMutability": "view",
                    "inputs": [{"name": "account", "type": "address"}],
                    "outputs": [{"name": "", "type": "uint256"}],
                },
            ]
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(usdc_addr), abi=erc20_abi,
            )
            raw_balance = contract.functions.balanceOf(self._wallet_address).call()
            return Decimal(raw_balance) / Decimal(10**6)

        except Exception as e:
            logger.error("USDC balance check failed: %s", e)
            return None
