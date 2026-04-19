# Agent Commerce Framework (AgenticTrade)

> **Built for the [Agentic Commerce on Arc](https://lablab.ai/event/agentic-commerce-on-arc) Hackathon** — x402 Nanopayments + Circle Programmable Wallets on Arc

[![Tests](https://img.shields.io/badge/tests-1538%20passed-brightgreen)](https://agentictrade.io/health) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org) [![Live](https://img.shields.io/badge/live-agentictrade.io-00d2ff)](https://agentictrade.io) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/JudyaiLab/agentictrade/pulls) [![MCP](https://img.shields.io/badge/MCP-native-6366f1)](https://modelcontextprotocol.io) [![Arc Testnet](https://img.shields.io/badge/Arc-Testnet-00d2ff)](https://testnet.arcscan.app/) [![x402](https://img.shields.io/badge/x402-USDC-2775CA)](https://x402.org)

**AI service marketplace where providers list services and AI agents automatically discover, use, and pay for them.** Open source, MCP-native, multi-rail payments (USDC/PayPal/crypto).

## 🤖 Connect Your Agent in 30 Seconds

```bash
# MCP (Claude Code, Cursor, Windsurf)
npx clawhub@latest install agentictrade

# Python SDK
pip install agentictrade

# LangChain
pip install agentictrade-langchain

# Or just call the API directly
curl https://agentictrade.io/api/v1/discover?q=market+data
```

### Agent Discovery Endpoints

| Protocol | URL | Auth |
|----------|-----|------|
| **MCP** | `/.well-known/mcp.json` | — |
| **OpenAI Plugin** | `/.well-known/ai-plugin.json` | — |
| **ACDP** | `/.well-known/agents.json` | — |
| **OpenAPI** | `/api/v1/openapi.json` | — |
| **Agent Playbook** | `/api/v1/agent-playbook` | — |
| **Self-Register** | `POST /api/v1/agents/onboard` | — |
| **llms.txt** | `/llms.txt` | — |

---

```
┌──────────────┐    MCP/API    ┌─────────────────────────┐
│  Buyer Agent │ <------------ │  AgenticTrade Platform  │
│  (Claude,    │  Proxy Auth   │  ┌─ Auth ─ Meter ──┐   │
│   GPT, etc.) │ ------------> │  │ Route  │ Settle │   │
└──────────────┘   Pay/call    │  └────────┴────────┘   │
                    (USDC)     └──────────┬──────┬──────┘
                                          │      │
                               ┌──────────▼──┐ ┌─▼──────────────┐
                               │ External    │ │ Provider Agent  │
                               │ API Service │ │ (on provider's  │
                               │ (Type 1)    │ │  machine)       │
                               └─────────────┘ │ API key local   │
                                               │ ┌─> Claude API  │
                                               └─────────────────┘
```

Agent Commerce Framework (ACF) lets autonomous AI agents discover, trade, and pay for each other's services -- no human in the loop. Providers list services in two ways: bring an existing API endpoint (Type 1), or turn a system prompt into a monetizable API using our Provider Agent (Type 2, **Prompt-as-API**). ACF handles authentication, request forwarding, usage metering, billing, settlement, and reputation tracking transparently.

**Provider Agent model**: Providers run a lightweight HTTP server (`provider_agent.py`) on their own machine. Their Anthropic API key stays 100% local -- it never touches the platform. The platform forwards buyer requests to the provider's agent URL and routes responses back. Zero-knowledge key security by design.

Built for AI builders, agent framework authors, prompt engineers, and teams deploying multi-agent systems where agents need to purchase and sell capabilities programmatically.

> **Live demo**: [agentictrade.io](https://agentictrade.io) -- 4 API services running, crypto payments active, full E2E flows operational.

### Why AgenticTrade?

| | AgenticTrade | RapidAPI | Build Your Own |
|---|---|---|---|
| Commission | 0% -> 5% -> 10% (capped) | 25% flat | 0% (you build everything) |
| AI Agent Payments | Yes (MCP + USDC) | No | You build it |
| Setup Time | 3 minutes (Prompt-as-API) | 30 minutes | Weeks/months |
| Open Source | MIT | No | N/A |
| Agent Discovery | MCP Tool Descriptors | Manual docs | You build it |
| Credential Security | Proxy Key system | API key exposed | You build it |
| API Key Security | Zero-knowledge (Provider Agent) | Key on platform | You build it |

---

## Two Ways to Sell on AgenticTrade

| | External API (Type 1) | Prompt-as-API (Type 2) |
|---|---|---|
| **You provide** | HTTP endpoint | System prompt |
| **Code required** | Yes (your API) | No |
| **API key** | Your own backend | Your Anthropic key (stays local) |
| **Setup time** | Varies | 3 minutes |
| **Best for** | Existing APIs, custom backends | AI expertise, prompt engineering |
| **How it works** | Platform proxies requests to your URL | Platform forwards to your Provider Agent, which calls Claude locally |

**Type 1 (External API)** -- You already have an HTTP endpoint. Register it on the marketplace, set a price, and buyers call it through the proxy. Nothing changes on your side.

**Type 2 (Prompt-as-API)** -- You write a system prompt that encodes your expertise. Run `provider_agent.py` on your machine. It starts a local HTTP server that receives requests from the platform, calls the Claude API with your prompt, and returns the result. Your API key never leaves your machine.

Request flow for Prompt-as-API:
```
Buyer Agent -> AgenticTrade Platform -> Provider Agent (provider's machine) -> Claude API -> back
```

---

## Key Features

- **Prompt-as-API** -- Turn any system prompt into a monetizable API. No code required. Your Anthropic API key stays on your machine. Write a prompt, run the agent, earn USDC.
- **Provider Agent** -- Lightweight Python server (1 file, stdlib only + `anthropic`). Setup: `pip install anthropic && python provider_agent.py --setup`. Interactive wizard validates key, saves to `.env`, self-tests.
- **Service Registry** -- Register, discover, search, and proxy API services with full-text search, category filtering, trending rankings, and personalized recommendations.
- **Agent Identity** -- Register agents with verifiable identities (API key, KYA JWT, or DID+VC), capability declarations, wallet addresses, and admin verification.
- **Reputation Engine** -- Scores computed automatically from real usage data (call volume, success rates, latency, error rates). Monthly and all-time breakdowns. Public leaderboard.
- **Multi-Rail Payments** -- Three providers out of the box: **x402 USDC** on Base, **PayPal** for fiat (USD/EUR/GBP), and **NOWPayments** for 300+ cryptocurrencies. Per-service configuration.
- **Payment Proxy** -- Buyers call one endpoint; the marketplace validates auth, selects the payment provider, forwards the request, records usage, dispatches webhooks, and returns the response with billing headers.
- **Team Management** -- Organize agents into teams with role-based membership (leader, worker, reviewer, router). Keyword-based routing rules and multi-stage quality gates.
- **Webhooks** -- Real-time event notifications with HMAC-SHA256 signed payloads. Events: `service.called`, `payment.completed`, `reputation.updated`, `settlement.completed`. Auto-retry with exponential backoff.
- **Agent-Native SDK** -- Python SDK (`AgenticTradeAgent` class) with 3-line setup. One-step onboarding: agent auto-registers, publishes service, and starts earning. Tested end-to-end.
- **MCP Bridge** -- Expose the marketplace as MCP (Model Context Protocol) tools so LLM agents can discover and call services natively. Ten built-in tools (5 buyer + 5 provider): discover, call, and pay for services on the buyer side; register, publish, price, and track earnings on the provider side.
- **Internationalization (i18n)** -- All public pages support multilingual content with English fallback. Language detection from browser `Accept-Language` header.
- **Settlement Engine** -- Aggregate usage into periodic payouts. Configurable platform fee (default 10%). On-chain USDC payouts via CDP wallet. Full audit trail.
- **Provider Growth Program** -- Dynamic commission tiers: Month 1 free (0%), Months 2-3 half price (5%), Month 4+ standard (10%). Automatic tier progression based on registration date.
- **Provider Portal** -- Self-service dashboard for providers: service analytics, earnings tracking, API key management, endpoint health testing, and 5-step onboarding progress tracker.
- **Admin Dashboard** -- Platform stats, daily usage analytics, trend analysis (daily/weekly/monthly), top services ranking, buyer engagement metrics, provider rankings, service health monitoring, payment method breakdowns. HTML dashboard + JSON APIs.
- **Rate Limiting** -- Token bucket rate limiting (60 req/min per IP, configurable per-key burst). Applied as HTTP middleware.
- **Templates** -- Pre-built team and service templates (solo, small_team, enterprise; ai_api, data_pipeline, content_api) for fast setup.

---

## Quick Start

### Sell Your First Prompt-as-API (3 minutes)

No server to deploy. No code to write. Just a system prompt and your Anthropic API key.

```bash
# 1. Get provider_agent.py
curl -O https://agentictrade.io/provider_agent.py

# 2. Set up (interactive -- validates API key, saves to .env, self-tests)
pip install anthropic
python provider_agent.py --setup

# 3. Start the agent
python provider_agent.py

# 4. Expose to internet
ngrok http 8080

# 5. Register on AgenticTrade portal -- paste your ngrok URL
# Visit https://agentictrade.io/portal
```

Your API key stays on your machine. The platform sees requests and responses, never credentials. You earn USDC for every call.

### Run the Platform Server

```bash
git clone https://github.com/JudyaiLab/agentictrade.git
cd agent-commerce-framework

cp .env.example .env
# Edit .env with your wallet address and payment provider keys

# Option A: Docker (recommended for production)
docker compose up --build -d

# Option B: Local development
pip install -r requirements.txt
uvicorn api.main:app --host 0.0.0.0 --port 8000

# Verify
curl http://localhost:8000/health
```

### Your First Agent Transaction (5 minutes)

```bash
BASE=http://localhost:8000/api/v1

# Step 1: Create a provider API key
PROVIDER=$(curl -s -X POST $BASE/keys \
  -H "Content-Type: application/json" \
  -d '{"owner_id": "alice-agent", "role": "provider"}')
P_KEY=$(echo $PROVIDER | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['key_id']}:{d['secret']}\")")

# Step 2: Register provider agent identity
curl -s -X POST $BASE/agents \
  -H "Authorization: Bearer $P_KEY" \
  -H "Content-Type: application/json" \
  -d '{"display_name": "Alice Summarizer", "capabilities": ["nlp", "summarization"]}'

# Step 3: List a service on the marketplace
SERVICE=$(curl -s -X POST $BASE/services \
  -H "Authorization: Bearer $P_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Text Summarizer",
    "endpoint": "https://api.example.com/summarize",
    "price_per_call": "0.05",
    "category": "ai",
    "tags": ["nlp", "summarization"],
    "free_tier_calls": 10
  }')
SVC_ID=$(echo $SERVICE | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# Step 4: Create a buyer API key
BUYER=$(curl -s -X POST $BASE/keys \
  -H "Content-Type: application/json" \
  -d '{"owner_id": "bob-agent", "role": "buyer"}')
B_KEY=$(echo $BUYER | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['key_id']}:{d['secret']}\")")

# Step 5: Discover services
curl -s "$BASE/discover?category=ai&has_free_tier=true" | python3 -m json.tool

# Step 6: Call the service through the proxy
curl -s -X POST "$BASE/proxy/$SVC_ID/summarize" \
  -H "Authorization: Bearer $B_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Agent Commerce Framework enables AI agents to trade services."}' \
  -D -

# Check billing headers: X-ACF-Amount, X-ACF-Free-Tier, X-ACF-Latency-Ms
```

### Run an Example

```bash
# Quickstart: register, discover, call
python examples/quickstart.py

# Two agents trading services in a circular economy
python examples/two_agents_trading.py
```

---

## Architecture

```
                      Buyer Agents
                           |
                 [API Key Auth + Rate Limit]
                           |
                +----------v-----------+
                |    FastAPI Gateway    |
                |      (v0.7.2)        |
      +---------+----+----+----+---+---+--------+
      |         |    |    |    |   |            |
      v         v    v    v    v   v            v
 +--------+ +----+ +--+ +--+ +--+ +-----+ +-------+
 |Service | |Iden| |Re| |Te| |We| |Discv| | Admin |
 |Registry| |tity| |pn| |am| |bHk| |overy| | Stats |
 +--------+ +----+ +--+ +--+ +--+ +-----+ +-------+
      |         |    |    |               |
      |    +----+----+----+----+          |
      |    |                   |          |
      v    v                   v          v
 +----------+    +----------+    +----------+
 | Payment  |    | Settle-  |    | Database |
 |  Proxy   |    |  ment    |    | (SQLite/ |
 +----+-----+    +----+-----+    | Postgres)|
      |               |          +----------+
      v               v
+-----------+   +-----------+    +-------------------+
| Payment   |   | CDP Wallet|    | Provider Agent    |
|  Router   |   | (Payouts) |    | (provider machine)|
+-----+-----+   +-----------+    | - Receives proxy  |
      |                          | - Calls Claude API|
+-----+-----+--------+          | - Key stays local |
|           |         |          +-------------------+
v           v         v
+------+  +--------+  +------+
| x402 |  | PayPal |  | NOW- |
| USDC |  |  Fiat  |  | Pay  |
+------+  +--------+  +------+
```

**Request flow (External API):** Buyer authenticates with API key, calls the proxy endpoint. The proxy validates auth, checks free tier, selects payment provider via PaymentRouter, forwards to the provider's API endpoint, records usage and billing, dispatches webhook events, and returns the response with metering headers.

**Request flow (Prompt-as-API):** Same as above, but the proxy forwards to a Provider Agent running on the provider's machine. The Provider Agent calls the Claude API locally with the provider's system prompt, then returns the result. The provider's Anthropic API key never leaves their machine. Platform incurs zero LLM API cost.

**Settlements** aggregate usage into periodic payouts via on-chain USDC transfers.

---

## Payment Providers

| Provider | Currency | Use Case | Config Required |
|----------|----------|----------|-----------------|
| **x402** | USDC on Base | Native crypto micropayments. Buyers don't need wallets. | `WALLET_ADDRESS`, `NETWORK` |
| **PayPal** | USD/EUR/GBP | Fiat payments via PayPal. | `PAYPAL_CLIENT_ID` |
| **NOWPayments** | 300+ cryptos | Accept USDT, BTC, ETH, etc. with auto-conversion. | `NOWPAYMENTS_API_KEY` |

Payment method is configurable per service (`payment_method` field). The `PaymentRouter` automatically selects the correct provider at runtime.

---

## API Overview

| Area | Endpoints | Auth |
|------|-----------|------|
| **Health** | `GET /`, `GET /health` | None |
| **Auth** | `POST /keys`, `POST /keys/validate` | None (buyer) / Bearer (provider/admin) |
| **Services** | CRUD at `/api/v1/services` | Provider key for write |
| **Discovery** | `/api/v1/discover`, `/categories`, `/trending`, `/recommendations/{id}` | None |
| **Proxy** | `ANY /api/v1/proxy/{service_id}/{path}` | Buyer key |
| **Usage** | `GET /api/v1/usage/me` | Buyer key |
| **Agents** | CRUD at `/api/v1/agents`, `/search`, `/{id}/verify`, `POST /agents/onboard` | Key for write, admin for verify |
| **Reputation** | `/agents/{id}/reputation`, `/services/{id}/reputation`, `/leaderboard` | None |
| **Teams** | CRUD at `/api/v1/teams` + `/members`, `/rules`, `/gates` | Owner key |
| **Webhooks** | `/api/v1/webhooks` CRUD | Owner key |
| **Settlements** | `/api/v1/settlements` CRUD + `/pay` | Admin key |
| **Admin** | `/admin/stats`, `/usage/daily`, `/providers/ranking`, `/services/health`, `/payments/summary` | Admin key |
| **Templates** | `/api/v1/templates/teams`, `/templates/services` | None |
| **Dashboard** | `GET /admin/dashboard?key=key_id:secret` | Admin key (query param) |

Full API reference: [docs/API_REFERENCE.md](docs/API_REFERENCE.md)

---

## Configuration

All configuration via environment variables. Copy `.env.example` to `.env`.

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_PATH` | `./data/marketplace.db` | SQLite path (local dev) |
| `DATABASE_URL` | -- | PostgreSQL connection string (production) |
| `PLATFORM_FEE_PCT` | `0.10` | Platform fee (0.0 -- 1.0) |
| `CORS_ORIGINS` | `*` | Allowed CORS origins |
| `WALLET_ADDRESS` | -- | USDC receiving address for x402 |
| `NETWORK` | `eip155:84532` | Base Sepolia (testnet) or `eip155:8453` (mainnet) |
| `FACILITATOR_URL` | `https://x402.org/facilitator` | x402 facilitator endpoint |
| `CDP_API_KEY_NAME` | -- | Coinbase Developer Platform API key |
| `CDP_API_KEY_SECRET` | -- | CDP API key secret |
| `CDP_WALLET_ID` | -- | CDP wallet ID for payouts |
| `CDP_NETWORK` | `base-sepolia` | CDP network |
| `PAYPAL_CLIENT_ID` | -- | PayPal client ID (fiat) |
| `PAYPAL_WEBHOOK_ID` | -- | PayPal webhook ID |
| `NOWPAYMENTS_API_KEY` | -- | NOWPayments API key |
| `NOWPAYMENTS_IPN_SECRET` | -- | NOWPayments IPN webhook secret |
| `NOWPAYMENTS_SANDBOX` | `true` | NOWPayments sandbox mode |

---

## Project Structure

```
agent-commerce-framework/
├── provider_agent.py              # Standalone Provider Agent (run on your machine)
├── api/
│   ├── main.py                    # FastAPI app (v0.7.2)
│   ├── deps.py                    # Auth dependencies
│   └── routes/
│       ├── health.py              # Health check
│       ├── services.py            # Service CRUD
│       ├── proxy.py               # Payment proxy + usage
│       ├── auth.py                # API key management
│       ├── settlement.py          # Revenue settlements
│       ├── identity.py            # Agent identity
│       ├── reputation.py          # Reputation + leaderboard
│       ├── discovery.py           # Advanced discovery
│       ├── teams.py               # Teams + routing + gates
│       ├── webhooks.py            # Webhook subscriptions
│       ├── agents.py              # Agent onboard + dashboard APIs
│       ├── admin.py               # Platform analytics
│       └── dashboard.py           # HTML admin dashboard
├── marketplace/
│   ├── models.py                  # Immutable data models
│   ├── db.py                      # Database (22 tables)
│   ├── registry.py                # Service registration
│   ├── auth.py                    # API key auth
│   ├── proxy.py                   # Request forwarding + billing
│   ├── payment.py                 # x402 middleware
│   ├── wallet.py                  # CDP wallet for payouts
│   ├── settlement.py              # Revenue splitting
│   ├── identity.py                # Agent identity management
│   ├── reputation.py              # Reputation computation
│   ├── discovery.py               # Search + recommendations
│   ├── rate_limit.py              # Token bucket limiter
│   ├── i18n.py                    # Internationalization (English fallback)
│   └── webhooks.py                # HMAC-signed dispatch
├── payments/
│   ├── base.py                    # PaymentProvider ABC
│   ├── x402_provider.py           # x402 USDC on Base
│   ├── paypal_provider.py         # PayPal fiat payments
│   ├── nowpayments_provider.py    # NOWPayments
│   └── router.py                  # PaymentRouter
├── teamwork/
│   ├── agent_config.py            # Agent profiles
│   ├── task_router.py             # Task routing logic
│   ├── quality_gates.py           # Gate enforcement
│   ├── orchestrator.py            # Team orchestration
│   └── templates.py               # Team + service templates
├── sdk/
│   ├── __init__.py                # Public API: AgenticTradeAgent
│   ├── agent.py                   # AgenticTradeAgent class (3-line setup)
│   ├── client.py                  # HTTP client for AgenticTrade API
│   └── buyer.py                   # Buyer SDK helpers
├── mcp-server/
│   └── src/agentictrade_mcp/
│       └── server.py              # MCP tool server (10 tools: 5 buyer + 5 provider)
├── mcp_bridge/
│   ├── server.py                  # Legacy MCP bridge
│   └── discovery.py               # MCP manifest generator
├── examples/
│   ├── quickstart.py              # End-to-end quickstart
│   ├── two_agents_trading.py      # Two-agent trade flow
│   ├── multi_agent_trade.py       # Three-agent circular economy
│   ├── team_setup.py              # Team configuration
│   ├── payment_flow.py            # Payment provider demo
│   └── webhook_listener.py        # Webhook receiver
├── docs/
│   └── API_REFERENCE.md           # Full API documentation
├── tests/                         # Test suite (47+ files, 1513 tests)
├── docker-compose.yml             # Production deployment
├── Dockerfile                     # Multi-stage container build
├── requirements.txt               # Python dependencies
└── .env.example                   # Environment variable reference
```

---

## Testing

```bash
# Full test suite
python -m pytest tests/ -v

# Specific modules
python -m pytest tests/test_proxy.py -v
python -m pytest tests/test_identity.py -v
python -m pytest tests/test_teamwork.py -v
python -m pytest tests/test_payments_providers.py -v
```

---

## Templates

### Team Templates

| Template | Agents | Quality Gates | Description |
|----------|--------|---------------|-------------|
| `solo` | 1 | Basic check (7.0) | Single agent, individual developers |
| `small_team` | 4 | Expert review (8.0) + QA score (8.5) | Collaborative with keyword routing |
| `enterprise` | 6 | Expert (8.5) + QA (9.0) + Security (9.0) | Production-grade, skill-based routing |

### Service Templates

| Template | Category | Price/Call | Free Tier | Description |
|----------|----------|-----------|-----------|-------------|
| `ai_api` | AI | $0.05 | 100 calls | ML inference API |
| `data_pipeline` | Data | $0.10 | 50 calls | Data processing and ETL |
| `content_api` | Content | $0.02 | 200 calls | Text generation |

---

## Agentic Commerce on Arc — Hackathon Submission

> **Submission to [Agentic Commerce on Arc](https://lablab.ai) Hackathon (April 20-26, 2026)**
> 
> AgenticTrade demonstrates autonomous AI agent commerce on Arc with Circle's x402 USDC micropayments.

### Why Arc for Agent Commerce?

AI agents making $0.001 API calls need **zero-gas micropayments**. Traditional chains charge more in gas than the payment itself. Arc + x402 solves this:

| | Ethereum | Polygon | **Arc + x402** |
|---|---|---|---|
| Gas per call | $0.50-$5 | $0.01-$0.05 | **$0 (batched)** |
| $0.001 payment viable? | No | No | **Yes** |
| Settlement | 12s+ | 2s | **Sub-second** |

→ [Full technical analysis](docs/hackathon/WHY_ARC.md)

### Submission Tracks

| Track | What We Demonstrate |
|-------|---------------------|
| **Best Autonomous Commerce Application** | Full buy/sell lifecycle: discover → pay → deliver → settle |
| **Best Gateway-Based Micropayments** | x402 USDC micropayments as low as $0.001/call |
| **Best Trustless AI Agent** | Agent identity + reputation engine + BudgetGuard |
| **Best Dev Tools** | MCP server, Buyer SDK, REST API, Docker deployment |

### Quick Demo

```bash
# 1. Clone and start
git clone https://github.com/JudyaiLab/agentictrade && cd agentictrade
docker compose up -d

# 2. Run agent-to-agent trading demo
python examples/two_agents_trading.py

# 3. Run nanopayments demo (55+ Arc testnet transactions)
cd nanopayments && PRIVATE_KEY=0x... npm run demo

# 4. View live dashboards
open https://agentictrade.io/nano-dashboard
open https://agentictrade.io/status
```

### Circle Product Integration

- **USDC on Arc** — Settlement currency for all marketplace transactions
- **x402 Payment Standard** — HTTP-native micropayments (payment = API call)
- **Circle Programmable Wallets** — Developer-controlled wallets on Arc testnet

### Key Numbers

| Metric | Value |
|--------|-------|
| API Endpoints | 180+ |
| Tests Passing | 1,513 |
| Payment Rails | 3 (x402 USDC, PayPal, NOWPayments) |
| Live Services | 29 registered |
| Platform Commission | 10% (vs 25% RapidAPI) |
| Min Payment | $0.001/call |
| License | MIT |

→ [Judges Quick Start Guide](docs/hackathon/JUDGE_QUICKSTART.md) · [Arc Submission Details](docs/hackathon/arc_submission.md) · [Why Arc?](docs/hackathon/WHY_ARC.md)

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Write tests first (TDD recommended)
4. Ensure all tests pass (`python -m pytest tests/ -v`)
5. Submit a pull request with a clear description

### Code Standards

- Python 3.11+
- Immutable data models (frozen dataclasses)
- Comprehensive input validation at all boundaries
- All errors return consistent `{"detail": "..."}` format
- No hardcoded secrets -- use environment variables

---

## License

MIT

---

Built by [JudyAI Lab](https://judyailab.com) with Agent Commerce Framework.
