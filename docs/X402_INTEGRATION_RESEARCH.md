# x402 Payment Protocol Integration Research
## For MIM-301: AgentKit + x402 Integration into AgenticTrade

**Date**: 2026-03-28
**Status**: Research Complete
**SDK Version Researched**: x402 v2.5.0 (released 2026-03-20)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Installation & Dependencies](#2-installation--dependencies)
3. [Server Side: FastAPI Middleware](#3-server-side-fastapi-middleware)
4. [Client Side: httpx with Automatic Payments](#4-client-side-httpx-with-automatic-payments)
5. [Wallet Setup: EthAccountSigner (No CDP Keys)](#5-wallet-setup-ethaccountsigner-no-cdp-keys)
6. [x402 Payment Flow (Protocol)](#6-x402-payment-flow-protocol)
7. [v1 to v2 Migration (Breaking Changes)](#7-v1-to-v2-migration-breaking-changes)
8. [Network Identifiers (CAIP-2)](#8-network-identifiers-caip-2)
9. [Token Addresses](#9-token-addresses)
10. [Facilitator Configuration](#10-facilitator-configuration)
11. [Current ACF Codebase Status](#11-current-acf-codebase-status)
12. [Implementation Plan for MIM-301](#12-implementation-plan-for-mim-301)
13. [Sources](#13-sources)

---

## 1. Executive Summary

x402 is an open-source payment protocol by Coinbase that turns the HTTP 402 "Payment Required" status code into a fully-featured, on-chain payment layer. It enables API monetization and agent-to-agent payments using USDC on Base, Polygon, Solana, and other networks.

Key facts:
- **Latest version**: 2.5.0 (2026-03-20)
- **License**: MIT
- **Python**: >=3.10
- **Payment**: USDC stablecoins on EVM (Base) and SVM (Solana) networks
- **Facilitator**: Coinbase-hosted service handles on-chain verification and settlement
- **No CDP keys required for buyers**: Can use a standalone private key via `EthAccountSigner`
- **CDP keys required for**: Mainnet facilitator authentication and server-side wallet management

The ACF codebase already has a working x402 integration in `marketplace/payment.py` and `payments/x402_provider.py`. MIM-301 extends this with standalone wallet support (no CDP dependency for buyers) and proper client-side payment handling.

---

## 2. Installation & Dependencies

### Pip Install Commands

```bash
# Server (FastAPI middleware + EVM support)
pip install "x402[fastapi,evm]"

# Client (httpx async + EVM support)
pip install "x402[httpx,evm]"

# Client (requests sync + EVM support)
pip install "x402[requests,evm]"

# Full install (all frameworks + all chains)
pip install "x402[all]"

# Add Solana support
pip install "x402[svm]"
```

### Available Extras

| Extra | Purpose |
|-------|---------|
| `httpx` | Async HTTP client integration |
| `requests` | Sync HTTP client integration |
| `fastapi` | FastAPI middleware (ASGI) |
| `flask` | Flask middleware |
| `evm` | Ethereum/Base/Polygon support |
| `svm` | Solana support |
| `mcp` | Model Context Protocol integration |
| `all` | Everything |

### Core Dependencies (from pyproject.toml)

```toml
[project]
requires-python = ">=3.10"
dependencies = [
    "x402[fastapi,evm]",
    "python-dotenv>=1.2.1",
    "uvicorn[standard]>=0.40.0",
]
```

### Version History

| Version | Date | Notes |
|---------|------|-------|
| 2.5.0 | 2026-03-20 | Latest (current) |
| 2.4.0 | 2026-03-16 | |
| 2.3.0 | 2026-03-06 | |
| 2.0.0 | 2026-01-22 | v2 launch (breaking changes) |
| 1.0.0 | 2025-12-10 | v1 stable |
| 0.1.0 | 2025-02-20 | Initial release |

---

## 3. Server Side: FastAPI Middleware

### Complete Working Example

```python
import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

load_dotenv()

# Configuration
EVM_ADDRESS = os.getenv("WALLET_ADDRESS")
NETWORK = os.getenv("NETWORK", "eip155:84532")  # Base Sepolia for testing
FACILITATOR_URL = os.getenv("FACILITATOR_URL", "https://x402.org/facilitator")

app = FastAPI()

# 1. Create facilitator client (handles on-chain verification)
facilitator = HTTPFacilitatorClient(
    FacilitatorConfig(url=FACILITATOR_URL)
)

# 2. Create resource server and register payment scheme
server = x402ResourceServer(facilitator)
server.register(NETWORK, ExactEvmServerScheme())

# 3. Define protected routes with pricing
routes: dict[str, RouteConfig] = {
    "GET /api/v1/weather": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=EVM_ADDRESS,
                price="$0.01",          # USDC amount ($ prefix required)
                network=NETWORK,         # CAIP-2 format
            ),
        ],
        mime_type="application/json",
        description="Weather report API",
    ),
    "GET /api/v1/premium/*": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=EVM_ADDRESS,
                price="$0.05",
                network=NETWORK,
            ),
        ],
        mime_type="application/json",
        description="Premium content",
    ),
}

# 4. Add middleware (MUST be before first request, per Starlette)
app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)

# 5. Define endpoints normally
@app.get("/api/v1/weather")
async def get_weather() -> dict[str, Any]:
    return {"weather": "sunny", "temperature": 70}

@app.get("/api/v1/premium/content")
async def get_premium() -> dict[str, str]:
    return {"content": "This is premium content"}

# Non-protected endpoint (no payment required)
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4021)
```

### Key Points

- `PaymentMiddlewareASGI` intercepts requests to configured routes
- If no valid `PAYMENT-SIGNATURE` header is present, returns HTTP 402 with `PAYMENT-REQUIRED` header
- If valid payment signature is present, facilitator verifies on-chain and settles
- Routes use glob patterns: `"GET /premium/*"` matches all paths under `/premium/`
- Price requires `$` prefix: `"$0.01"` not `"0.01"`

### Route Config with Custom Asset (USDC by contract address)

```python
from x402.schemas import AssetAmount

routes = {
    "GET /api/endpoint": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=EVM_ADDRESS,
                price=AssetAmount(
                    amount="10000",  # 0.01 USDC (6 decimals)
                    asset="0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # USDC on Base Sepolia
                    extra={"name": "USDC", "version": "2"},
                ),
                network="eip155:84532",
            ),
        ],
    ),
}
```

---

## 4. Client Side: httpx with Automatic Payments

### Async Client (httpx) - Complete Example

```python
import asyncio
import os

from dotenv import load_dotenv
from eth_account import Account

from x402 import x402Client
from x402.http import x402HTTPClient
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client

load_dotenv()


async def main() -> None:
    # 1. Create x402 client
    client = x402Client()

    # 2. Create signer from private key (NO CDP keys needed)
    account = Account.from_key(os.getenv("EVM_PRIVATE_KEY"))
    register_exact_evm_client(client, EthAccountSigner(account))
    print(f"Wallet address: {account.address}")

    # 3. Create HTTP helper for payment response extraction
    http_client = x402HTTPClient(client)

    # 4. Make request (payment is handled AUTOMATICALLY)
    url = "https://api.example.com/api/v1/weather"
    async with x402HttpxClient(client) as http:
        response = await http.get(url)
        await response.aread()

        print(f"Status: {response.status_code}")
        print(f"Body: {response.text}")

        # 5. Extract payment settlement details
        try:
            settle_response = http_client.get_payment_settle_response(
                lambda name: response.headers.get(name)
            )
            print(f"Payment settled: {settle_response.model_dump_json(indent=2)}")
        except ValueError:
            print("No payment response header found")


asyncio.run(main())
```

### Sync Client (requests) - Complete Example

```python
import os

from eth_account import Account

from x402 import x402ClientSync
from x402.http import x402HTTPClientSync
from x402.http.clients import x402_requests
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client


def main() -> None:
    client = x402ClientSync()
    account = Account.from_key(os.getenv("EVM_PRIVATE_KEY"))
    register_exact_evm_client(client, EthAccountSigner(account))

    http_client = x402HTTPClientSync(client)

    with x402_requests(client) as session:
        response = session.get("https://api.example.com/api/v1/weather")

        print(f"Status: {response.status_code}")
        print(f"Body: {response.text}")

        if response.ok:
            settle_response = http_client.get_payment_settle_response(
                lambda name: response.headers.get(name)
            )
            print(f"Payment: {settle_response}")


main()
```

### How Automatic Payment Works

1. Client sends initial GET request (no payment header)
2. Server returns HTTP 402 + `PAYMENT-REQUIRED` header (base64 JSON with payment options)
3. x402 client SDK automatically:
   a. Parses the `PAYMENT-REQUIRED` header
   b. Selects a compatible payment option
   c. Creates and signs an EIP-712 payment authorization
   d. Retries the request with `PAYMENT-SIGNATURE` header
4. Server forwards to facilitator for on-chain verification
5. Facilitator settles payment on-chain
6. Server returns 200 + resource + `PAYMENT-RESPONSE` header

---

## 5. Wallet Setup: EthAccountSigner (No CDP Keys)

### Key Insight for MIM-301

There are TWO ways to create wallet signers in x402 v2:

#### Option A: Standalone Private Key (EthAccountSigner) - NO CDP KEYS NEEDED

```python
from eth_account import Account
from x402.mechanisms.evm import EthAccountSigner

# Just a hex private key - works with any Ethereum wallet
account = Account.from_key("0xYOUR_PRIVATE_KEY_HERE")
signer = EthAccountSigner(account)

# Use in client
from x402 import x402Client
from x402.mechanisms.evm.exact.register import register_exact_evm_client

client = x402Client()
register_exact_evm_client(client, signer)
```

This approach:
- Requires only `eth_account` package (part of `x402[evm]`)
- Works with ANY Ethereum private key (MetaMask export, hardware wallet, etc.)
- No Coinbase account or CDP API keys needed
- Suitable for: buyer agents, standalone wallets, testing

#### Option B: CDP Server Wallet (via cdp-sdk) - REQUIRES CDP KEYS

```python
from cdp import CdpClient

cdp_client = CdpClient(
    api_key_id="YOUR_CDP_KEY_ID",
    api_key_secret="YOUR_CDP_KEY_SECRET",
)
account = cdp_client.evm.get_or_create_account(name="my-agent")
```

This approach:
- Requires `cdp-sdk>=1.30` and a Coinbase Developer Platform account
- Provides managed wallets with gas sponsorship on Base
- Better for: production sellers, marketplace settlement wallets
- The ACF already uses this in `marketplace/wallet.py` (WalletManager)

### For MIM-301: Dual Support

The integration should support BOTH approaches:
- **Sellers (ACF marketplace)**: Use CDP WalletManager (existing) for receiving payments
- **Buyers (agent clients)**: Use EthAccountSigner with just a private key

---

## 6. x402 Payment Flow (Protocol)

```
Client                    Resource Server              Facilitator           Blockchain
  |                            |                           |                     |
  |-- GET /api/weather ------->|                           |                     |
  |                            |                           |                     |
  |<-- 402 Payment Required ---|                           |                     |
  |    (PAYMENT-REQUIRED hdr)  |                           |                     |
  |                            |                           |                     |
  |  [SDK auto: parse options, |                           |                     |
  |   select scheme, sign      |                           |                     |
  |   EIP-712 authorization]   |                           |                     |
  |                            |                           |                     |
  |-- GET /api/weather ------->|                           |                     |
  |   (PAYMENT-SIGNATURE hdr)  |                           |                     |
  |                            |-- POST /verify ---------->|                     |
  |                            |                           |-- submit tx ------->|
  |                            |                           |<-- tx confirmed ----|
  |                            |<-- verification result ---|                     |
  |                            |                           |                     |
  |<-- 200 OK + data ---------|                           |                     |
  |   (PAYMENT-RESPONSE hdr)   |                           |                     |
```

### HTTP Headers (v2)

| Header | Direction | Purpose |
|--------|-----------|---------|
| `PAYMENT-REQUIRED` | Server -> Client | Base64 JSON with payment options (on 402) |
| `PAYMENT-SIGNATURE` | Client -> Server | Base64 signed payment authorization |
| `PAYMENT-RESPONSE` | Server -> Client | Base64 settlement result (on 200) |

---

## 7. v1 to v2 Migration (Breaking Changes)

### Header Changes

| Purpose | v1 | v2 |
|---------|----|----|
| Client payment | `X-PAYMENT` | `PAYMENT-SIGNATURE` |
| Server response | `X-PAYMENT-RESPONSE` | `PAYMENT-RESPONSE` |
| Requirements | Body-embedded | `PAYMENT-REQUIRED` header |

### Python Import Path Changes

| v1 | v2 |
|----|-----|
| `x402.clients.httpx` | `x402.http.clients.x402HttpxClient` |
| `x402.clients.requests` | `x402.http.clients.x402_requests` |
| `x402.fastapi.middleware` | `x402.http.middleware.fastapi` |
| `x402.flask.middleware` | `x402.http.middleware.flask` |
| `x402.facilitator` | `x402.http.HTTPFacilitatorClient` |

### Client-Side API Changes

```python
# v1 (OLD - DO NOT USE)
from x402.clients.httpx import x402HttpxClient
account = Account.from_key(os.getenv("PRIVATE_KEY"))
async with x402HttpxClient(account=account, base_url="...") as client:
    response = await client.get("/endpoint")

# v2 (CURRENT)
from x402 import x402Client
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client

client = x402Client()
account = Account.from_key(os.getenv("EVM_PRIVATE_KEY"))
register_exact_evm_client(client, EthAccountSigner(account))
async with x402HttpxClient(client) as http:
    response = await http.get("https://full-url/endpoint")
```

### Server-Side API Changes

```python
# v1 (OLD)
from x402.fastapi.middleware import require_payment
app.middleware("http")(require_payment(path="/weather", price="$0.001",
    pay_to_address="0x...", network="base-sepolia"))

# v2 (CURRENT)
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
# ... (see Section 3 for full example)
app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
```

### Environment Variable Changes

| v1 | v2 |
|----|-----|
| `PRIVATE_KEY` | `EVM_PRIVATE_KEY` |
| Network: `"base-sepolia"` | Network: `"eip155:84532"` (CAIP-2) |

### Compatibility Note

The Coinbase facilitator supports both v1 and v2 during the transition period, but v2 clients get full feature support.

---

## 8. Network Identifiers (CAIP-2)

v2 uses CAIP-2 standardized identifiers:

| Network | CAIP-2 ID | Chain ID |
|---------|-----------|----------|
| Base Mainnet | `eip155:8453` | 8453 |
| Base Sepolia (testnet) | `eip155:84532` | 84532 |
| Ethereum Mainnet | `eip155:1` | 1 |
| Sepolia (testnet) | `eip155:11155111` | 11155111 |
| Polygon Mainnet | `eip155:137` | 137 |
| Solana Mainnet | `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp` | - |
| Solana Devnet | `solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1` | - |

---

## 9. Token Addresses

### USDC Contract Addresses

| Network | Address |
|---------|---------|
| Base Mainnet | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |
| Base Sepolia | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |

USDC uses 6 decimals: 1 USDC = 1,000,000 atomic units.

---

## 10. Facilitator Configuration

| Environment | URL | Auth Required | Networks |
|-------------|-----|---------------|----------|
| Testnet (free) | `https://x402.org/facilitator` | None | Base Sepolia, Solana Devnet |
| Production (CDP) | `https://api.cdp.coinbase.com/platform/v2/x402` | CDP API keys | Base, Polygon, Solana mainnet |

### Testnet Setup (no auth)

```python
facilitator = HTTPFacilitatorClient(
    FacilitatorConfig(url="https://x402.org/facilitator")
)
```

### Production Setup (with CDP auth)

```python
# Requires: CDP_API_KEY_ID and CDP_API_KEY_SECRET env vars
facilitator = HTTPFacilitatorClient(
    FacilitatorConfig(url="https://api.cdp.coinbase.com/platform/v2/x402")
)
```

---

## 11. Current ACF Codebase Status

### Already Implemented

| File | Status | Description |
|------|--------|-------------|
| `marketplace/payment.py` | v2 compatible | `PaymentConfig`, `build_x402_routes()`, `setup_x402_middleware()` |
| `payments/x402_provider.py` | v2 compatible | PaymentProvider wrapper for x402 |
| `payments/agentkit_provider.py` | Working | Direct USDC transfers via CDP SDK |
| `marketplace/wallet.py` | Working | CDP WalletManager with USDC transfers |
| `api/main.py` | Working | x402 middleware setup in `_init_components()` |

### What Already Works

1. Server-side x402 middleware is correctly implemented using v2 APIs
2. Route building from registered services (`build_x402_routes`)
3. Facilitator client setup with configurable URL
4. EVM ExactScheme registration
5. Payment extraction from response headers

### What MIM-301 Needs to Add

1. **Client-side x402 payment capability** - For agents buying services from the marketplace
2. **EthAccountSigner support** - So buyer agents only need a private key, not CDP credentials
3. **Multi-network support** - Add Solana as a payment option alongside Base EVM
4. **Bazaar discovery metadata** - Enable API discovery for x402 ecosystem
5. **x402 client wrapper** - Utility for agent clients to make paid API calls

---

## 12. Implementation Plan for MIM-301

### Phase 1: Client-Side x402 Support (Priority)

Create a new module `payments/x402_client.py`:

```python
# Conceptual structure (not final code)
class X402AgentClient:
    """Client for agents to make x402 payments to protected APIs."""

    def __init__(self, private_key: str, network: str = "eip155:84532"):
        # Uses EthAccountSigner - no CDP keys needed
        pass

    async def call_paid_api(self, url: str) -> dict:
        # Wraps x402HttpxClient for easy agent usage
        pass
```

### Phase 2: Enhanced Server Configuration

- Add AssetAmount support for flexible pricing
- Add Bazaar discovery extension for API discoverability
- Support multiple networks per route (Base + Solana)

### Phase 3: Testing

- Testnet integration test (Base Sepolia + x402.org facilitator)
- Client payment flow test
- Middleware verification test

### Environment Variables Needed

```bash
# Server (seller) - already configured
WALLET_ADDRESS=0x...        # Receiving address for payments
NETWORK=eip155:84532        # Base Sepolia for testing
FACILITATOR_URL=https://x402.org/facilitator

# Client (buyer agent) - NEW for MIM-301
EVM_PRIVATE_KEY=0x...       # Agent's private key for signing payments

# Production (when ready)
CDP_API_KEY_ID=...          # For mainnet facilitator auth
CDP_API_KEY_SECRET=...      # For mainnet facilitator auth
```

---

## 13. Sources

- [coinbase/x402 GitHub Repository](https://github.com/coinbase/x402)
- [x402 FastAPI Server Example](https://github.com/coinbase/x402/tree/main/examples/python/servers/fastapi)
- [x402 httpx Client Example](https://github.com/coinbase/x402/tree/main/examples/python/clients/httpx)
- [x402 PyPI Package](https://pypi.org/project/x402/)
- [x402 Quickstart for Buyers](https://docs.cdp.coinbase.com/x402/quickstart-for-buyers)
- [x402 Quickstart for Sellers](https://docs.cdp.coinbase.com/x402/quickstart-for-sellers)
- [x402 v1 to v2 Migration Guide](https://docs.cdp.coinbase.com/x402/migration-guide)
- [x402 Official Website](https://www.x402.org/)
- [x402 V2 Launch Announcement](https://www.x402.org/writing/x402-v2-launch)
- [x402 FAQ](https://docs.cdp.coinbase.com/x402/support/faq)
- [x402 Network Support](https://docs.cdp.coinbase.com/x402/network-support)
- [x402 Payment Flow (Avalanche Builder Hub)](https://build.avax.network/academy/blockchain/x402-payment-infrastructure/03-technical-architecture/01-payment-flow)
- [x402 Facilitator Concept](https://docs.x402.org/core-concepts/facilitator)
- [Coinbase x402 Product Page](https://www.coinbase.com/developer-platform/products/x402)
- [USDC on Base (BaseScan)](https://basescan.org/token/0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913)
- [awesome-x402 Resource List](https://github.com/xpaysh/awesome-x402)
- [x402 and Agentic Commerce (AWS)](https://aws.amazon.com/blogs/industries/x402-and-agentic-commerce-redefining-autonomous-payments-in-financial-services/)
- [x402 on Stripe](https://docs.stripe.com/payments/machine/x402)
