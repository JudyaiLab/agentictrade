# AgenticTrade — Agentic Commerce on Arc Hackathon Submission

**Status:** APPROVED
**Last Updated:** 2026-04-03
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
- **USDC on Arc** — Settlement currency for all marketplace micropayments. Deposit TX: `0xead8b5f7bf91ca850f9af5293b2ee3aad0ac0fc32b8eeadad40aef1de6fed141`. 10 successful paid API calls at $0.001 each on Arc testnet (chain ID 5042002).
- **x402 Payment Standard** — `@circle-fin/x402-batching` server middleware (`gateway.require()`) and client SDK (`GatewayClient`) for HTTP-native micropayments with off-chain batching.
- **Circle Programmable Wallets** — Developer-controlled seller wallet on ARC-TESTNET (wallet set `ab698b2d...`), eliminating private key management for the provider side.

**Why These Products:**
- x402 eliminates the checkout step entirely — the agent includes USDC payment proof with every API call, no human intervention needed
- Off-chain batching means individual API calls cost $0 gas — only the initial deposit TX costs gas, making $0.001 micropayments economically viable
- USDC as Arc's native gas token eliminates the complexity of bridging ETH for gas fees
- Circle Programmable Wallets let us create developer-controlled seller wallets without managing raw private keys

**What Worked Well:**
- **GatewayClient deposit + pay flow worked seamlessly** — deposit USDC once, then make unlimited micropayment API calls without additional on-chain transactions
- **Off-chain batching eliminates per-call gas costs** — our 10 test calls at $0.001 each cost zero gas after the initial deposit, making true micropayments viable
- **Circle Programmable Wallets on Arc testnet** — developer-controlled wallets with no private key management needed for the seller side, received 40 USDC for testing
- **EIP-155 chain ID detection** — Arc testnet chain ID (5042002) was automatically detected by the SDK with no special configuration
- **USDC as native gas token** — no ETH bridging needed, simplifying the entire payment flow for autonomous agents

**What Could Be Improved:**
- **GatewayClient environment handling** — the client doesn't auto-load `.env` files; we had to pass `PRIVATE_KEY` via `process.env` explicitly, which is error-prone in containerized deployments
- **Opaque error messages from `gateway.require()`** — when payment fails, the middleware returns generic "Payment failed" errors without indicating whether it's an insufficient balance, wrong chain, or signature issue. More granular error codes would speed up debugging significantly
- **Arc testnet USDC faucet documentation** — there's no official documentation for obtaining testnet USDC on Arc; we had to use the Circle faucet manually and discover the process through trial and error
- **Limited TypeScript documentation for x402-batching** — the server-side middleware setup for `@circle-fin/x402-batching` lacks comprehensive examples, especially for Express/Fastify integration patterns
- **No Python SDK for x402-batching** — only TypeScript is supported; our Python buyer client had to fall back to the base `x402` library, which doesn't support off-chain batching. A Python SDK would unlock the large FastAPI/Django ecosystem for x402 adoption

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
- 1,530+ tests across 50+ test files
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

## Action Items Before Submission

- [ ] Register on LabLab.ai
- [ ] Join LabLab.ai Discord
- [ ] Create Circle Developer Console account
- [ ] Deploy AgenticTrade instance on Arc testnet
- [ ] Integrate Circle Gateway for USDC balance management
- [ ] Record 2-minute demo video showing end-to-end agent commerce flow on Arc
- [x] Finalize "Circle Product Feedback" section with real integration experience
- [ ] Submit by April 25, 2026

## Links
- GitHub: https://github.com/JudyaiLab/agentictrade
- Live Demo: https://agentictrade.io
- MCP Server: https://pypi.org/project/agentictrade-mcp/
