# AgenticTrade — Agentic Commerce on Arc Hackathon Submission

**Status:** APPROVED
**Last Updated:** 2026-04-19
**Hackathon:** Agentic Commerce on Arc (LabLab.ai) — April 20-26, 2026

## Project Name
AgenticTrade: Autonomous AI Agent Commerce Marketplace

## Short Description (280 chars)
Open-source marketplace where AI agents autonomously discover, negotiate, and purchase API services using USDC on Arc. Built-in MCP integration, multi-rail payments (x402 USDC + PayPal + 300+ crypto), agent identity, and reputation engine.

## Long Description

### The Problem
AI agents are increasingly autonomous — they can browse the web, write code, and make decisions. But when they need to pay for an API service, a human still has to set up a subscription, enter credit card details, and manage API keys manually.

The agent economy needs commerce infrastructure built for machines, not humans.

### Our Solution: AgenticTrade
AgenticTrade is an open-source marketplace framework (MIT license) where AI agents can:

1. **Discover** services via MCP tools or REST API (no UI needed)
2. **Verify** provider reputation through on-chain usage data
3. **Pay** autonomously using x402 USDC on Arc (or PayPal/crypto)
4. **Settle** through periodic on-chain USDC payouts

### How It Works
```
Buyer Agent → MCP discover_services("NLP API") → AgenticTrade Registry
Buyer Agent → MCP call_service(service_id, payload) → Payment Proxy
Payment Proxy → x402 USDC on Arc → Provider API → Response
Settlement Engine → USDC payout to Provider wallet (periodic)
```

### Circle Product Feedback

**Products Used:**
- **USDC on Arc** — Settlement currency for all marketplace micropayments. Deposit TX: `0xead8b5f7bf91ca850f9af5293b2ee3aad0ac0fc32b8eeadad40aef1de6fed141`. 589 successful paid API calls at $0.001 each on Arc testnet (chain ID `eip155:5042002`). Full off-chain payment log in `data/nanopayment_transactions.json`. **Important:** The explorer shows only 2 on-chain transactions (deposit + batch settlement) — this is by design. Individual $0.001 API calls are processed off-chain via x402 batching, which is what enables zero gas fees per call. The 589 off-chain payment authorizations are EIP-712 signed and logged locally.
- **x402 Payment Standard** — Server-side: `@circle-fin/x402-batching` middleware via `createGatewayMiddleware()` + `gateway.require("$0.001")` per-route pricing (`nanopayments/server.ts:39-79`). Client-side: `GatewayClient` with `client.deposit()` + `client.pay()` flow (`nanopayments/demo-buyer.ts:47-93`). Python buyer uses base `x402` library with `EthAccountSigner` for EIP-712 signed payments (`payments/x402_client.py:102-117`).
- **Circle Programmable Wallets** — Developer-controlled seller wallet on ARC-TESTNET (wallet ID `ea88a05e-f3a4-5960-900d-506ab312e6be`, wallet set `ab698b2d...`, address `0xc041...a5c8`, balance 40 USDC) via `payments/circle_wallets.py`, eliminating private key management for the provider side.

**Why These Products:**
- x402 eliminates the checkout step entirely — the agent includes USDC payment proof with every API call, no human intervention needed. This is the difference between "agent-compatible" and "agent-native" commerce.
- Off-chain batching means individual API calls cost $0 gas — only the initial `client.deposit()` TX costs gas, making $0.001 micropayments economically viable. We ran 589 calls at $0.001 each with zero gas after one deposit.
- USDC as Arc's native gas token eliminates the dual-token complexity of bridging ETH for gas fees. For AI agents that don't understand token management, single-token simplicity is critical.
- Circle Programmable Wallets let us onboard providers with just an email — no MetaMask, no seed phrases, no "what's a gas fee?" friction.

**What Worked Well:**
1. **`GatewayClient.deposit()` + `.pay()` two-step flow is perfectly suited for AI agents** — deposit once with a single on-chain TX, then make unlimited off-chain micropayments. Our `demo-buyer.ts:52-59` checks `balances.gateway.available`, auto-deposits if low, then fires 519 paid API calls — all automated, no human needed.
2. **`createGatewayMiddleware()` + `gateway.require(price)` is the simplest payment middleware we've ever used** — 3 lines of code (`server.ts:39-42`) to protect any Express route with USDC payments. Compare this to Stripe integration which requires 50+ lines of webhook handling, customer objects, and session management.
3. **Dynamic per-route pricing works cleanly** — we fetch the service's price from our registry, then pass it to `gateway.require()` dynamically (`server.ts:109-126`). This enables a marketplace with hundreds of services at different price points through a single gateway.
4. **Arc testnet chain ID (`eip155:5042002`) auto-detected** — no manual chain registration. `GatewayClient({ chain: "arcTestnet" })` just works (`demo-buyer.ts:48`).
5. **Off-chain payment objects contain structured payer data** — `(req as any).payment.payer` gives us the buyer's wallet address per-request (`server.ts:82-83`), which we use to build per-agent transaction histories and reputation scores without additional identity infrastructure.

**What Could Be Improved (specific, actionable):**

1. **`gateway.require()` error responses lack diagnostic codes** — When a buyer's `client.pay()` fails, the middleware returns HTTP 402 with a generic body. We cannot distinguish between: (a) insufficient gateway balance, (b) wrong chain ID, (c) invalid signature, (d) expired payment authorization. We had to add our own `client.supports(url)` pre-check (`demo-buyer.ts:78-84`) to detect unsupported routes before paying. **Suggestion:** Return a JSON body with `{ "error_code": "INSUFFICIENT_BALANCE" | "CHAIN_MISMATCH" | "SIGNATURE_INVALID", "required_amount": "0.001", "buyer_balance": "0.000" }` so agent clients can self-diagnose and auto-recover.

2. **No Python SDK for `x402-batching` off-chain flow** — Our backend is Python/FastAPI. The base `x402` Python library (`x402[fastapi,evm]>=2.0.0`) supports on-chain EVM ExactScheme payments but NOT the off-chain batching that `@circle-fin/x402-batching` provides. This forced us into a dual-language architecture: TypeScript sidecar for nanopayments + Python for everything else. We had to build `payments/x402_client.py` (290 lines) as a workaround using `x402HTTPClient` + `EthAccountSigner`, but it lacks gateway balance management. **Suggestion:** Release `circle-x402-batching` as a PyPI package wrapping the same `deposit/pay/getBalances` flow. The Python AI agent ecosystem (LangChain, CrewAI, AutoGen) is massive — this would 10x x402 adoption.

3. **`GatewayClient` constructor doesn't validate chain support** — `new GatewayClient({ chain: "arcTestnet", privateKey: key })` silently succeeds even when Arc testnet is unreachable. The first failure only appears at `client.deposit()` time, with an opaque error. **Suggestion:** Add a `client.validateConnection()` async method, or throw on construction if the chain RPC is unreachable.

4. **No webhook/callback for batch settlement events** — Off-chain payments batch-settle periodically, but there's no webhook to notify the seller when settlement actually lands on-chain. Our platform (`marketplace/settlement.py`) has to poll the explorer to detect settlements. **Suggestion:** Add a `gateway.onSettle(callback)` server-side hook that fires when a batch settles, with `{ batch_id, tx_hash, total_amount, individual_payments[] }`.

5. **Arc testnet USDC acquisition is undocumented** — There's no official faucet page for Arc testnet USDC. We obtained test USDC through Circle Developer Console funding (40 USDC), but the process was trial-and-error. A `npx @circle-fin/faucet arc-testnet 0xADDRESS 100` CLI command would dramatically reduce onboarding friction for hackathon participants.

6. **`x402-batching` `latest` tag on npm is risky for production** — The package doesn't have stable semver releases yet, so our `package.json` pins `"@circle-fin/x402-batching": "latest"`. A breaking change in middleware API shape during our development window (April 15-19) would have silently broken our 589-transaction demo. **Suggestion:** Publish tagged releases (`@circle-fin/x402-batching@1.0.0`) and document the middleware contract as stable.

**Recommendations for a More Seamless Developer Experience:**

1. **Unified Python + TypeScript SDK coverage** — The biggest friction in our build was maintaining a dual-language architecture (TypeScript nanopayments sidecar + Python FastAPI backend) because x402-batching only exists in TypeScript. A `circle-x402-batching` PyPI package with the same `deposit/pay/getBalances` flow would let Python-first teams (LangChain, CrewAI, AutoGen) adopt x402 without a sidecar. This is the single highest-impact improvement Circle could make for agent commerce adoption.

2. **Arc testnet developer onboarding kit** — Bundle a one-command setup: `npx @circle-fin/arc-quickstart` that creates a wallet set, funds testnet USDC from faucet, deploys a sample x402-protected endpoint, and opens the Arc Block Explorer to verify — all in under 60 seconds. Our hackathon team spent hours piecing together docs across Circle Wallets, x402, and Arc testnet. A unified quickstart would drastically reduce time-to-first-transaction.

3. **Circle Console → Arc Explorer deep links** — When viewing a transaction in the Circle Developer Console, add a "View on Arc Explorer" button that links directly to the corresponding `testnet.arcscan.app/tx/{hash}` page. Currently, developers must manually copy the TX hash and navigate to the explorer. This small UX improvement would immediately validate the on-chain proof story for every Circle Wallets user on Arc.

4. **x402 payment receipt webhook** — After `gateway.require()` processes a payment, provide a server-side callback with structured receipt data: `{ payer, amount, txHash, batchId, timestamp }`. Currently we parse `(req as any).payment` manually, and batch settlement timing is opaque. A first-class receipt webhook would let marketplace platforms like ours reconcile payments reliably.

### Technical Architecture

**Core Stack:**
- Python 3.11+ / FastAPI backend
- SQLite (dev) / PostgreSQL (prod), 23-table schema
- Docker Compose deployment, production-ready

**Payment Layer:**
- x402 USDC on Arc — EVM ExactScheme, web-native micropayments
- PaymentRouter — intelligent provider selection at runtime
- PayPal (fiat: USD/EUR/GBP) + NOWPayments (300+ crypto)
- Payment Proxy — single-endpoint proxy that validates auth, checks free tier, selects payment provider, forwards request, records usage/billing, dispatches HMAC-signed webhook events, returns response with metering headers (X-ACF-Amount, X-ACF-Free-Tier, X-ACF-Latency-Ms)
- Settlement Engine — periodic on-chain USDC payouts via CDP wallet, configurable platform fee (default 10%), Provider Growth Program (Month 1 free, Months 2-3 half commission, Month 4+ standard)

**Agent Infrastructure:**
- Coinbase CDP SDK for wallet management
- Agent Identity — API key → KYA JWT → DID+VC (progressive trust)
- Reputation Engine — trust scores from usage data (call volume, success rate, latency)
- MCP Bridge — 5 tools: discover_services, get_service_details, call_service, get_balance, list_categories

**Quality & Coverage:**
- 1,538+ tests across 54 test files
- 9-language i18n support
- MIT License (fully open-source)

### Tracks

**Best Autonomous Commerce Application** (Primary)
- AgenticTrade is literally an autonomous commerce platform for AI agents
- Full buy/sell lifecycle: discovery → negotiation → payment → delivery → settlement
- No human in the loop required

**Best Gateway-Based Micropayments Integration**
- x402 USDC micropayments for per-API-call billing
- Supports payments as small as $0.001 per call
- Gateway-based verification ensures payment before API delivery

**Best Trustless AI Agent**
- Agent identity: API key → KYA JWT → DID+VC (progressive trust)
- Reputation engine with on-chain verification (call volume, success rates, latency, error rates)
- HMAC-signed webhooks for trust-minimized event delivery
- Budget limits and spending controls

**Best Dev Tools**
- MCP server: `pip install agentictrade-mcp`
- 5 built-in tools for any MCP-compatible agent
- Full REST API for non-MCP agents
- Docker Compose deployment

## Demo Video Script (2 min)
1. (0:00-0:15) Title card + problem statement
2. (0:15-0:45) Live demo: agent discovers API via MCP, checks pricing
3. (0:45-1:15) Live demo: agent makes API call with x402 USDC payment on Arc
4. (1:15-1:35) Dashboard showing settlement, reputation, and analytics
5. (1:35-1:50) Architecture overview (30 sec)
6. (1:50-2:00) GitHub link + call to action

## Team

**JudyAI Lab** — Building open-source AI infrastructure for the agent economy.

- **Judy** — CEO & Product Vision. Defines product direction and strategic priorities.
- **J** — COO & Technical Director. System architecture, backend development, deployment, and security.
- **Ada** — Product Engineer. Feature development, data pipelines, and product management.
- **Lily** — Content Director. Documentation, content strategy, and quality assurance.
- **Mimi** — Marketing & Growth. Market research, competitive analysis, and go-to-market execution.

**Circle Developer Console Email:** contact@judyailab.com

## Hackathon Integration Plan

1. Deploy AgenticTrade on Arc mainnet/testnet with USDC as the settlement currency
2. Integrate Circle Gateway for unified USDC balance across chains
3. Demonstrate end-to-end autonomous commerce: agent discovery → negotiation → x402 micropayment → service execution → reputation update → settlement — all onchain on Arc
4. Provide the "Circle Product Feedback" section detailing our experience with Arc, USDC, Gateway, and x402
5. Submit to multiple tracks: Best Autonomous Commerce Application (primary), Best Gateway-Based Micropayments, Best Trustless AI Agent, Best Dev Tools

## Submission Checklist

- [x] Register on LabLab.ai
- [x] Join LabLab.ai Discord
- [x] Create Circle Developer Console account
- [x] Deploy AgenticTrade instance on Arc testnet
- [x] Integrate x402 Nanopayments via `@circle-fin/x402-batching`
- [x] Integrate Circle Programmable Wallets for seller onboarding
- [x] 589 nanopayment transactions on Arc testnet ($0.001 USDC each)
- [x] Finalize "Circle Product Feedback" section with code-level specifics
- [x] Public GitHub repo with MIT license
- [x] Live demo at agentictrade.io
- [ ] Record demo video (in progress)
- [ ] Submit by April 25, 2026

## Links
- GitHub: https://github.com/JudyaiLab/agentictrade
- Live Demo: https://agentictrade.io
- MCP Server: https://pypi.org/project/agentictrade-mcp/
