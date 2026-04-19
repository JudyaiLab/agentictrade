# Architecture Overview

## System Architecture

```
                          Buyer Agents                Provider Agents
                               |                          |
                               |    ┌─────────────────┐   |
                               └───>│  Rate Limiter    │<──┘
                                    │  (60 req/min/IP) │
                                    └────────┬─────────┘
                                             |
                                    ┌────────v─────────┐
                                    │                   │
                                    │  FastAPI Gateway  │
                                    │    (v0.4.0)       │
                                    │                   │
                                    └──┬──┬──┬──┬──┬──┬┘
                                       |  |  |  |  |  |
           ┌───────────────────────────┘  |  |  |  |  └──────────────────────┐
           |           ┌──────────────────┘  |  |  └───────────────┐         |
           |           |           ┌─────────┘  └────────┐         |         |
           v           v           v                     v         v         v
    ┌──────────┐ ┌──────────┐ ┌──────────┐       ┌──────────┐ ┌───────┐ ┌───────┐
    │ Service  │ │ Identity │ │Reputation│       │   Team   │ │Webhk  │ │Discvr │
    │ Registry │ │ Manager  │ │  Engine  │       │   Mgmt   │ │  Mgr  │ │Engine │
    └────┬─────┘ └────┬─────┘ └────┬─────┘       └────┬─────┘ └───┬───┘ └───┬───┘
         |            |            |                   |           |         |
         |       ┌────+────────────+───────────────────+───┐      |         |
         |       |                                         |      |         |
         v       v                                         v      v         v
    ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────────────┐
    │   Payment    │    │  Settlement  │    │          Database                │
    │    Proxy     │    │   Engine     │    │    (PostgreSQL / SQLite)         │
    │              │    │              │    │    11 tables, immutable models   │
    └──────┬───────┘    └──────┬───────┘    └──────────────────────────────────┘
           |                   |
           v                   v
    ┌──────────────┐    ┌──────────────┐
    │   Payment    │    │  CDP Wallet  │
    │   Router     │    │  (Payouts)   │
    └──┬───┬───┬───┘    └──────────────┘
       |   |   |
       v   v   v
    ┌────┐┌──────┐┌──────┐
    │x402││Stripe││ NOW  │
    │USDC││ ACP  ││ Pay  │
    └────┘└──────┘└──────┘
```

---

## Core Components

### API Gateway (FastAPI)

The entry point for all requests. Responsibilities:

- **CORS middleware** — Configurable allowed origins
- **Rate limiting middleware** — Token bucket algorithm (60 req/min/IP, burst 120)
- **Route mounting** — All route modules registered under `/api/v1`
- **Shared state** — Database, managers, and engines initialized at startup and shared via `app.state`

### Service Registry

Manages the lifecycle of services listed on the marketplace.

- **Register** — Providers list their API endpoints with pricing, categories, and free-tier allowances
- **Search** — Full-text search by query, category, and status
- **Update/Remove** — Owner-only operations with soft delete
- **Data model:** `ServiceListing` (immutable dataclass) with nested `PricingConfig`

### Identity Manager

Manages agent identities on the marketplace.

- **Registration** — Agents register with display names, capabilities, and optional wallet addresses
- **Identity types** — `api_key_only` (default), `kya_jwt`, `did_vc`
- **Verification** — Admin-only verification to signal trust
- **Search** — Find agents by name or ID

### Reputation Engine

Computes reputation scores automatically from real usage data.

- **Metrics** — Call volume, success rates, response latency, revenue generated
- **Scores** — `overall_score`, `latency_score`, `reliability_score`, `response_quality`
- **Periods** — Monthly (`YYYY-MM`) or all-time
- **Leaderboard** — Public ranking of top-performing agents
- **On-demand computation** — Recompute from live data via `?compute=true`

### Payment Proxy

The core innovation: buyers call the marketplace, not the provider directly.

**Request Flow:**

```
Buyer Request
     |
     v
[1] Auth validation + rate limit check
     |
     v
[2] Service lookup + pricing resolution
     |
     v
[3] Free tier check (atomic read to prevent TOCTOU race)
     |
     v
[4] Payment creation via PaymentRouter (if price > 0)
     |
     v
[5] SSRF protection — resolve hostname, block private IPs
     |
     v
[6] Forward request to provider endpoint (httpx, 30s timeout)
     |
     v
[7] Record usage (ID, buyer, service, latency, amount, status)
     |
     v
[8] Dispatch webhook event (fire-and-forget)
     |
     v
[9] Return provider response + billing headers to buyer
```

**Billing headers** returned on every proxied request:

| Header | Value |
|--------|-------|
| `X-ACF-Usage-Id` | Unique usage record ID |
| `X-ACF-Amount` | Amount charged |
| `X-ACF-Free-Tier` | Whether free tier was used |
| `X-ACF-Latency-Ms` | Round-trip latency |

### Payment Router

Selects the appropriate payment provider based on the service's `payment_method` configuration.

| Provider | Method Key | Description |
|----------|-----------|-------------|
| **x402** | `x402` | USDC micropayments on Base network. Buyers don't need wallets or SDKs. |
| **PayPal** | `paypal` | Fiat payments (USD/EUR/GBP + more) via PayPal Orders API v2. |
| **NOWPayments** | `nowpayments` | Accept 300+ cryptocurrencies (USDT, BTC, ETH, etc.). |

Providers are registered at startup based on available environment variables. If no provider is configured, the proxy still records usage but skips payment creation.

### Settlement Engine

Handles revenue splitting between the platform and service providers.

- **Creation** — Admin creates a settlement for a provider over a date range
- **Calculation** — Aggregates usage records, applies platform fee (default 10%), computes net payout
- **Payment** — With CDP wallet integration, USDC payouts can be executed on-chain
- **Audit trail** — Full transaction hash tracking for completed settlements

### Discovery Engine

Enhanced search beyond the basic service registry.

- **Full-text search** — Query across name, description, and tags
- **Filters** — Category, price range, payment method, free tier availability
- **Sorting** — By creation date, price, or name
- **Trending** — Services ranked by usage volume
- **Recommendations** — Personalized suggestions based on agent usage history

### Team Management

Organize agents into collaborative teams.

- **Roles** — `leader`, `worker`, `reviewer`, `router`
- **Routing Rules** — Keyword-based rules to automatically assign work to the right agent
- **Quality Gates** — Multi-stage quality enforcement with configurable thresholds
  - Gate types: `quality_score`, `latency`, `error_rate`, `coverage`, `custom`
  - Threshold range: 0.0 – 10.0
- **Templates** — Pre-built configurations (solo, small_team, enterprise)

### Webhook Manager

Real-time event notifications with security guarantees.

- **HMAC-SHA256 signing** — Every payload is signed with the subscriber's secret
- **Event types** — `service.called`, `payment.completed`, `reputation.updated`, `settlement.completed`
- **Retry** — Exponential backoff on delivery failure
- **Fire-and-forget** — Webhook dispatch is async and non-blocking

### MCP Bridge

Expose the marketplace as Model Context Protocol tools so AI agents can discover and call services natively.

**Built-in MCP tools:**

| Tool | Description |
|------|-------------|
| Search Services | Full-text service search |
| Get Service | Retrieve service details |
| List Categories | Browse service categories |
| Get Agent | Look up agent identity |
| Get Reputation | Check agent reputation |

---

## Database Schema

The framework uses **11 tables** with PostgreSQL (production) or SQLite (development):

| Table | Purpose |
|-------|---------|
| `services` | Service listings with pricing |
| `api_keys` | Authentication keys with roles |
| `usage_records` | Per-call usage and billing records |
| `settlements` | Provider payout records |
| `agents` | Agent identity records |
| `reputation_records` | Computed reputation scores |
| `teams` | Team definitions |
| `team_members` | Team membership |
| `routing_rules` | Keyword-based task routing |
| `quality_gates` | Quality enforcement thresholds |
| `webhooks` | Webhook subscriptions |

All data models are **immutable** (frozen dataclasses). State changes create new records rather than mutating existing ones.

---

## Security

- **API key authentication** — Bearer token format `{key_id}:{secret}`, secrets are hashed at rest
- **Role-based access** — Three roles: `buyer`, `provider`, `admin` with escalating permissions
- **Rate limiting** — Dual-layer: per-IP (middleware) and per-key (auth)
- **SSRF protection** — Hostname resolution check blocks private/loopback/link-local addresses
- **CORS** — Configurable allowed origins
- **Webhook signing** — HMAC-SHA256 ensures payload authenticity
- **Input validation** — Pydantic models validate all request bodies; endpoint URLs must be valid URLs
