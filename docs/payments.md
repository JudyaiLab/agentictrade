# Payment System Developer Guide

> Agent Commerce Framework (ACF) — Three-Layer Payment Architecture

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Payment Providers](#payment-providers)
  - [x402 Provider (Crypto Micropayments)](#x402-provider)
  - [PayPal Provider (Fiat Payments)](#paypal-provider)
  - [AgentKit Provider (Direct USDC Transfers)](#agentkit-provider)
- [Payment Router](#payment-router)
- [Buyer Agent SDK](#buyer-agent-sdk)
- [Wallet Management](#wallet-management)
- [Environment Variables](#environment-variables)
- [End-to-End Example](#end-to-end-example)
- [FAQ](#faq)

---

## Architecture Overview

ACF provides three complementary payment methods, unified behind a single `PaymentProvider` interface:

```
┌──────────────────────────────────────────────────────────┐
│                     PaymentRouter                        │
│  route("x402")      → X402Provider                       │
│  route("paypal")     → PayPalProvider                     │
│  route("agentkit")   → AgentKitProvider                  │
└───────┬─────────────────┬─────────────────┬──────────────┘
        │                 │                 │
┌───────▼───────┐ ┌───────▼───────┐ ┌───────▼───────┐
│  x402         │ │  PayPal       │ │  AgentKit     │
│  HTTP 402     │ │  Checkout     │ │  Direct USDC  │
│  USDC on Base │ │  USD/EUR/GBP  │ │  via CDP SDK  │
│  Middleware   │ │  Card/Bank    │ │  Instant      │
└───────┬───────┘ └───────┬───────┘ └───────┬───────┘
        │                 │                 │
┌───────▼─────────────────▼─────────────────▼──────────────┐
│              SettlementEngine                             │
│  Aggregates usage → calculates fees → pays out           │
│  Uses WalletManager for USDC settlement                  │
└──────────────────────────────────────────────────────────┘
```

### When to Use Each Provider

| Provider | Currency | Best For | Settlement |
|----------|----------|----------|------------|
| **x402** | USDC | Per-call API micropayments | Middleware-verified, automatic |
| **PayPal** | USD, EUR, GBP | Fiat subscriptions, one-time purchases | Checkout session → webhook |
| **AgentKit** | USDC | Direct agent-to-agent transfers, bulk payouts | Instant on-chain |

### Base Interface

All providers implement `PaymentProvider` (`payments/base.py`):

```python
from payments.base import PaymentProvider, PaymentResult, PaymentStatus

class PaymentProvider(ABC):
    async def create_payment(amount: Decimal, currency: str, metadata: dict) -> PaymentResult
    async def verify_payment(payment_id: str) -> PaymentStatus
    async def get_payment(payment_id: str) -> dict

    @property
    def provider_name(self) -> str
    @property
    def supported_currencies(self) -> list[str]
```

`PaymentResult` is an immutable dataclass:

```python
@dataclass(frozen=True)
class PaymentResult:
    payment_id: str              # Unique ID (prefixed by provider)
    status: PaymentStatus        # pending | completed | failed | expired
    amount: Decimal
    currency: str
    checkout_url: Optional[str]  # For PayPal redirect flows
    metadata: dict               # Provider-specific data (tx_hash, network, etc.)
```

---

## Payment Providers

### x402 Provider

**File:** `payments/x402_provider.py`

The x402 protocol enables HTTP-native micropayments. When a buyer agent calls a paid API endpoint, the server responds with `HTTP 402 Payment Required`. The buyer signs a USDC payment, re-sends the request with the payment signature, and the middleware verifies it before forwarding to the upstream service.

#### Payment Flow

```
Buyer Agent → GET /api/v1/proxy/{service_id}
           ← HTTP 402 + PAYMENT-REQUIRED header (base64-encoded JSON)
Buyer Agent → parses requirements, signs USDC payment
           → GET /api/v1/proxy/{service_id} + Payment-Signature header
           ← HTTP 200 + API response + X-Payment-Transaction header
```

#### Usage

```python
from payments.x402_provider import X402Provider
from marketplace.payment import PaymentConfig

# Initialize from environment variables
provider = X402Provider()

# Or with explicit config
config = PaymentConfig(
    wallet_address="0xYourSellerWallet",
    network="eip155:8453",           # Base Mainnet
    facilitator_url="https://x402.org/facilitator",
    enabled=True,
)
provider = X402Provider(config=config)

# Create a payment intent (tracking record — actual payment is middleware-driven)
result = await provider.create_payment(
    amount=Decimal("0.01"),
    currency="USDC",
    metadata={"service_id": "svc-weather-001", "buyer_id": "agent-123"},
)
# result.payment_id → "x402_a1b2c3d4e5f67890"
# result.status → PaymentStatus.pending (verified later by middleware)

# Extract tx hash from response headers after middleware verification
tx_hash = provider.extract_tx_from_headers(response_headers)
```

#### Key Points

- x402 payments are **middleware-verified** — the server-side `PaymentMiddlewareASGI` handles verification automatically.
- `create_payment()` records intent; actual verification happens at the HTTP layer.
- Supports USDC on Base network (mainnet and Sepolia testnet).
- The `X402Provider` wraps the existing `PaymentConfig` from `marketplace/payment.py`.

---

### PayPal Provider

**File:** `payments/stripe_acp.py`

PayPal enables fiat payments (credit card, bank) for agent-to-agent commerce. It uses PayPal Checkout Sessions with support for USD, EUR, and GBP.

#### Payment Flow

```
Buyer Agent → POST /api/v1/payments/checkout {amount, currency: "USD"}
           ← {checkout_url, session_id, payment_id}
           → (Agent or human completes checkout at checkout_url)
           → Stripe webhook fires: checkout.session.completed
Server     → verify_payment(payment_id) → PaymentStatus.completed
```

#### Usage

```python
from payments.stripe_acp import StripeACPProvider

# Initialize (reads PAYPAL_CLIENT_ID from env if not passed)
provider = StripeACPProvider(
    api_key="sk_test_...",
    success_url="https://yourapp.com/success",
    cancel_url="https://yourapp.com/cancel",
)

# Create a Stripe Checkout Session
result = await provider.create_payment(
    amount=Decimal("29.99"),
    currency="USD",
    metadata={
        "description": "CoinSifter Pro API — Monthly",
        "agent_id": "agent-456",
    },
)
# result.payment_id → "stripe_acp_cs_test_..."
# result.checkout_url → "https://checkout.stripe.com/c/pay/..."
# result.status → PaymentStatus.pending

# Verify payment status after webhook or polling
status = await provider.verify_payment(result.payment_id)
# status → PaymentStatus.completed

# Get full session details
details = await provider.get_payment(result.payment_id)
```

#### Key Points

- Requires `pip install stripe>=8.0`.
- Falls back gracefully if `stripe` is not installed (raises `PaymentProviderError` with installation instructions).
- Amounts are converted to cents internally (Stripe smallest-unit convention).
- Supports `USD`, `EUR`, `GBP` currencies.
- The `api_key` parameter falls back to `PAYPAL_CLIENT_ID` or `PAYPAL_CLIENT_SECRET` env vars.

---

### AgentKit Provider

**File:** `payments/agentkit_provider.py`

Direct agent-to-agent USDC transfers via CDP SDK v2. Unlike x402 (middleware-driven) or PayPal (fiat checkout), this provider executes **immediate on-chain transfers** from one wallet to another.

#### Payment Flow

```
Buyer Agent → POST /api/v1/payments/transfer {to_address, amount_usdc}
           → AgentKit provider signs + sends USDC via CDP wallet
           ← {payment_id, tx_hash, status: "completed"}
```

#### Usage

```python
from payments.agentkit_provider import AgentKitProvider
from marketplace.wallet import WalletManager, WalletConfig

# Initialize wallet manager
wallet_config = WalletConfig.from_env()  # Reads CDP_API_KEY_ID, CDP_API_KEY_SECRET
wallet = WalletManager(wallet_config)

# Initialize provider
provider = AgentKitProvider(wallet_manager=wallet)

# Execute a direct USDC transfer
result = await provider.create_payment(
    amount=Decimal("5.00"),
    currency="USDC",
    metadata={
        "to_address": "0xRecipientWalletAddress",
        "agent_id": "agent-789",
        "service_id": "svc-analysis-001",
    },
)
# result.payment_id → "agentkit_a1b2c3d4e5f67890"
# result.status → PaymentStatus.completed (instant settlement)
# result.metadata["tx_hash"] → "0xabc123..."

# Create a wallet for a new agent
address = await provider.create_agent_wallet("new-agent-001")
# address → "0x..."
```

#### Key Points

- Transfers are **synchronous** — `create_payment()` waits for the on-chain transaction to complete.
- `metadata` **must** include `to_address` (recipient wallet address).
- Returns `PaymentStatus.completed` on success or `PaymentStatus.failed` on failure.
- Falls back to dry-run mode (logged but not executed) if CDP wallet is not configured.
- Uses CDP SDK v2 (`CdpClient` + `EvmServerAccount`) — not the `coinbase-agentkit` wrapper, due to dependency conflicts with x402 2.x.

---

## Payment Router

**File:** `payments/router.py`

The `PaymentRouter` dispatches payment method strings to the correct provider.

```python
from payments.router import PaymentRouter
from payments.x402_provider import X402Provider
from payments.stripe_acp import StripeACPProvider
from payments.agentkit_provider import AgentKitProvider

router = PaymentRouter({
    "x402": X402Provider(),
    "stripe_acp": StripeACPProvider(),
    "agentkit": AgentKitProvider(wallet_manager=wallet),
})

# Route to the correct provider (case-insensitive)
provider = router.route("x402")
if provider:
    result = await provider.create_payment(...)

# List all available methods
methods = router.list_providers()
# ["agentkit", "stripe_acp", "x402"]

# Check if a method exists
if "x402" in router:
    ...
```

---

## Buyer Agent SDK

**File:** `sdk/buyer.py`

The `BuyerAgent` class provides a high-level client for agents that need to **purchase** services through the marketplace. It handles x402 payment flows automatically.

### Initialization

```python
from sdk.buyer import BuyerAgent

async with BuyerAgent(
    marketplace_url="http://localhost:8000",
    api_key="key_id:secret",               # ACF API key
    cdp_api_key_id="your-cdp-key-id",      # For wallet/payment signing
    cdp_api_key_secret="your-cdp-secret",
    wallet_name="my-buyer-agent",           # CDP account name
    network="base-sepolia",                 # base-sepolia or base-mainnet
    timeout=30.0,
) as buyer:
    print(f"Wallet: {buyer.wallet_address}")
```

### Discover Services

```python
# Search with filters
services = await buyer.discover_services(
    query="weather",
    category="data",
    max_price="0.10",
)
for svc in services:
    print(f"{svc['id']}: {svc['name']} — ${svc['price_per_call']}/call")
```

### Call a Paid API

```python
# x402 payment is handled automatically when auto_pay=True (default)
result = await buyer.call_service(
    service_id="svc-weather-001",
    method="GET",
    path="/forecast",
    params={"city": "Seoul"},
)
print(result)  # The API response from the upstream service
```

**Under the hood**, `call_service()`:
1. Sends the initial request to the marketplace proxy
2. If the server returns HTTP 402, parses the `PAYMENT-REQUIRED` header
3. Signs the payment using the CDP wallet
4. Re-sends the request with the `Payment-Signature` header
5. Returns the API response

### Check Balance and Payment History

```python
# USDC balance
balance = await buyer.get_balance()
print(f"Balance: {balance} USDC")

# Payment history for this session
for record in buyer.payment_history:
    print(f"{record.service_id}: {record.amount} {record.currency} (tx: {record.tx_hash})")
```

### Error Handling

```python
from sdk.buyer import BuyerAgent, BuyerAgentError

try:
    result = await buyer.call_service("svc-001", path="/data")
except BuyerAgentError as e:
    # Covers: no wallet, 402 parse failure, signing failure, HTTP errors
    print(f"Payment or API error: {e}")
```

---

## Wallet Management

**File:** `marketplace/wallet.py`

The `WalletManager` handles USDC operations using CDP SDK v2.

### WalletConfig

```python
from marketplace.wallet import WalletManager, WalletConfig

# Load from environment (recommended)
config = WalletConfig.from_env()

# Or create manually
config = WalletConfig(
    api_key_id="your-cdp-key-id",
    api_key_secret="your-cdp-secret",
    account_name="marketplace-settlement",
    network="base-sepolia",
    enabled=True,
)

wallet = WalletManager(config)
```

### Operations

```python
# Check wallet address and readiness
print(wallet.address)    # "0x..." or None
print(wallet.is_ready)   # True/False

# Transfer USDC
tx_hash = await wallet.transfer_usdc(
    to_address="0xRecipient",
    amount=Decimal("10.00"),
)

# Check balance
balance = await wallet.get_balance()  # Decimal or None

# Create agent-specific wallet
agent_addr = await wallet.create_agent_wallet("agent-001")
```

### Network Constants

```python
from marketplace.wallet import USDC_ADDRESSES, NETWORK_MAP

# USDC contract addresses
USDC_ADDRESSES = {
    "base-mainnet": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "base-sepolia": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
}

# x402-style network ID → CDP network name
NETWORK_MAP = {
    "eip155:8453":  "base-mainnet",
    "eip155:84532": "base-sepolia",
}
```

---

## Environment Variables

All environment variables used by the payment system:

| Variable | Required By | Description |
|----------|-------------|-------------|
| `CDP_API_KEY_ID` | AgentKit, BuyerAgent, WalletManager | CDP API key ID from [portal.cdp.coinbase.com](https://portal.cdp.coinbase.com) |
| `CDP_API_KEY_SECRET` | AgentKit, BuyerAgent, WalletManager | CDP API private key |
| `CDP_ACCOUNT_NAME` | WalletManager | Server account name (default: `marketplace-settlement`) |
| `CDP_NETWORK` | WalletManager | Network identifier (default: `base-mainnet`) |
| `WALLET_ADDRESS` | x402 | Seller's Base wallet address for receiving payments |
| `NETWORK` | x402 | x402 network ID: `eip155:8453` (mainnet) or `eip155:84532` (testnet) |
| `FACILITATOR_URL` | x402 | x402 facilitator endpoint (default: `https://x402.org/facilitator`) |
| `PAYPAL_CLIENT_ID` | PayPal | PayPal client ID |
| `PAYPAL_CLIENT_SECRET` | PayPal | PayPal client secret |

### Minimal `.env` for Production (Mainnet)

```bash
# CDP (for AgentKit + WalletManager)
CDP_API_KEY_ID=org-xxxxxxxx-xxxx
CDP_API_KEY_SECRET=<your EC private key from CDP portal, PEM format>
CDP_NETWORK=base-mainnet

# x402 (for seller payment reception)
WALLET_ADDRESS=0xYourBaseMainnetWallet
NETWORK=eip155:8453
FACILITATOR_URL=https://x402.org/facilitator

# PayPal (for fiat payments)
PAYPAL_CLIENT_ID=your_paypal_client_id
PAYPAL_CLIENT_SECRET=your_paypal_client_secret
```

---

## End-to-End Example

**File:** `examples/agent_buys_api.py`

A complete demo of an agent buying an API service:

```python
import asyncio
import os
from sdk.buyer import BuyerAgent

async def main():
    async with BuyerAgent(
        marketplace_url="http://localhost:8000",
        cdp_api_key_id=os.environ["CDP_API_KEY_ID"],
        cdp_api_key_secret=os.environ["CDP_API_KEY_SECRET"],
        network="base-sepolia",
    ) as buyer:
        # 1. Discover services
        services = await buyer.discover_services(max_price="0.10")
        print(f"Found {len(services)} services")

        # 2. Call a paid API (x402 payment auto-handled)
        if services:
            result = await buyer.call_service(
                service_id=services[0]["id"],
                method="GET",
                path="/",
            )
            print(f"Response: {result}")

        # 3. Check payment history
        for record in buyer.payment_history:
            print(f"Paid {record.amount} {record.currency} for {record.service_id}")

asyncio.run(main())
```

### Running the Demo

```bash
# 1. Start the ACF server
cd projects/agent-commerce-framework
pip install -r requirements.txt
uvicorn api.main:app --host 0.0.0.0 --port 8000

# 2. Set environment variables
export CDP_API_KEY_ID="..."
export CDP_API_KEY_SECRET="..."

# 3. (Optional) Get testnet USDC from faucet
# https://faucet.circle.com — select Base Sepolia

# 4. Run the demo
python examples/agent_buys_api.py
```

---

## FAQ

### General

**Q: Which payment method should I choose?**

- **x402** for per-call API micropayments (USDC, automatic via middleware)
- **PayPal** for fiat one-time or subscription payments (card/bank)
- **AgentKit** for direct wallet-to-wallet USDC transfers between agents

**Q: Can I use multiple payment methods simultaneously?**

Yes. Register all providers with the `PaymentRouter` and each service can specify its preferred method. The router dispatches automatically.

**Q: What network should I use for testing?**

Use **Base Sepolia** (`base-sepolia` / `eip155:84532`). Get testnet USDC from [Circle's faucet](https://faucet.circle.com).

### x402

**Q: How does x402 work at the HTTP level?**

1. Client sends a request to a protected endpoint
2. Server responds `HTTP 402` with a base64-encoded `PAYMENT-REQUIRED` header containing price, network, and `payTo` address
3. Client signs the payment with their wallet and re-sends with `Payment-Signature` header
4. The `PaymentMiddlewareASGI` verifies the signature, forwards to the upstream service, and returns the response with an `X-Payment-Transaction` header

**Q: Do I need to implement 402 handling manually?**

No, if you use the `BuyerAgent` SDK. It handles the full 402 → sign → retry flow automatically via `call_service()`.

### PayPal

**Q: What PayPal credentials do I need?**

A client ID and client secret from your PayPal developer dashboard. Use sandbox credentials for testing and live credentials for production.

**Q: How do I handle PayPal webhooks?**

Set up a webhook endpoint for `CHECKOUT.ORDER.APPROVED` events. Call `verify_payment(payment_id)` to confirm the payment status in your handler.

### AgentKit / Wallet

**Q: Why use `cdp-sdk` directly instead of `coinbase-agentkit`?**

The `coinbase-agentkit` package (0.7.x) pins `x402<2`, creating a dependency conflict with `x402` 2.4.0 which ACF requires. It also has Python 3.12 build issues due to the `bcl` dependency using the removed `pkgutil.ImpImporter`. Using `cdp-sdk` 1.40 directly provides the same wallet capabilities without these conflicts.

**Q: Are USDC transfers on Base gasless?**

Yes. Base network supports gasless USDC transfers — the CDP SDK handles gas sponsorship automatically.

**Q: What happens if CDP credentials are not set?**

The `WalletManager` and `AgentKitProvider` fall back to **dry-run mode** — transfers are logged but not executed. The `BuyerAgent` SDK will raise `BuyerAgentError` if a service requires payment but no wallet is configured.

### Troubleshooting

**Q: `PaymentProviderError: stripe SDK is not installed`**

Run `pip install stripe>=8.0` to install the Stripe SDK.

**Q: `PaymentProviderError: CDP wallet not configured`**

Ensure `CDP_API_KEY_ID` and `CDP_API_KEY_SECRET` are set. Get credentials from [portal.cdp.coinbase.com](https://portal.cdp.coinbase.com).

**Q: `PaymentProviderError: x402 provider is disabled`**

Set `WALLET_ADDRESS` to your Base network wallet address. The x402 provider requires a configured seller wallet.

**Q: `BuyerAgentError: Received HTTP 402 but no wallet configured`**

Initialize `BuyerAgent` with `cdp_api_key_id` and `cdp_api_key_secret` to enable automatic payment handling.

---

*Agent Commerce Framework — Payment System Documentation*
*Last updated: 2026-03-19*
