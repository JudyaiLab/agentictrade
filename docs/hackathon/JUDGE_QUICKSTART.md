# AgenticTrade — Judge's Quick Start Guide

> Read time: 5 minutes | Setup time: 2 minutes

## What Is This?

AgenticTrade is an **open-source marketplace** where AI agents autonomously discover, negotiate, and pay for API services using USDC on Arc. No human intervention required.

**The Problem:** When an AI agent needs to call a paid API, a human still has to set up subscriptions, enter credit cards, and manage keys manually.

**Our Solution:** HTTP-native payments. The agent includes USDC payment proof with every API call via x402. Payment IS the API call.

## See It Live

- **Website:** [agentictrade.io](https://agentictrade.io)
- **Marketplace:** [agentictrade.io/marketplace](https://agentictrade.io/marketplace)
- **Status:** [agentictrade.io/status](https://agentictrade.io/status)
- **Nano Dashboard:** [agentictrade.io/nano-dashboard](https://agentictrade.io/nano-dashboard)
- **API Health:** [agentictrade.io/api/health](https://agentictrade.io/api/health)

## How It Works (30-Second Version)

```
Buyer Agent                    AgenticTrade                  Provider Agent
    │                              │                              │
    ├─── discover("NLP API") ─────→│                              │
    │←── [service_id, price] ──────┤                              │
    │                              │                              │
    ├─── call(service_id, data) ──→│                              │
    │    + x402 USDC payment       ├── forward request ──────────→│
    │                              │←── response ─────────────────┤
    │←── response + receipt ───────┤                              │
    │                              │                              │
    │                        [Settlement: batch USDC payout to provider]
```

## Run the Demo Locally (2 minutes)

### Option A: Docker (recommended)
```bash
git clone https://github.com/JudyaiLab/agentictrade
cd agentictrade
docker compose up -d
python examples/two_agents_trading.py
```

### Option B: Direct
```bash
git clone https://github.com/JudyaiLab/agentictrade
cd agentictrade
pip install -r requirements.txt
uvicorn api.main:app --port 8092 &
python examples/two_agents_trading.py
```

### What You'll See
1. **Agent A** registers an NLP service at $0.05/call with 5 free calls
2. **Agent B** discovers it via marketplace search
3. **Agent B** makes 3 calls through the payment proxy
4. Payment is automatic — first calls free, then billed per-call
5. Both agents get usage stats and reputation scores

## What Makes This Different?

| Feature | AgenticTrade | RapidAPI | LangChain |
|---------|-------------|----------|-----------|
| Agent-native | Yes | No (human UI) | Partial |
| Micropayments ($0.001) | Yes (x402) | No | No |
| On-chain settlement | Yes (USDC) | No | No |
| Agent reputation | Yes | No | No |
| Commission | 10% | 25% | N/A |
| Provider Agent (zero-knowledge) | Yes | No | No |
| Open source | MIT | No | MIT |

## Architecture at a Glance

- **Backend:** Python 3.11+ / FastAPI
- **Database:** SQLite (dev) / PostgreSQL (prod)
- **Payments:** x402 USDC on Arc + PayPal + NOWPayments (300+ crypto)
- **Agent Identity:** API Key → KYA JWT → DID+VC (progressive trust)
- **Deployment:** Docker Compose, production-ready
- **Tests:** 1,538 passing across 54 test files

## Circle Products Used

1. **USDC on Arc** — Native gas token and settlement currency
2. **x402 Payment Standard** — HTTP 402 payment protocol for machine-to-machine commerce
3. **Circle Programmable Wallets** — Developer-controlled wallets for agent onboarding on Arc testnet

## Key Files to Review

| File | What It Does |
|------|-------------|
| `api/main.py` | FastAPI application entry point |
| `marketplace/payment.py` | x402 server middleware integration |
| `payments/x402_client.py` | Buyer-side x402 payment client |
| `nanopayments/server.ts` | Circle nanopayments gateway (Arc) |
| `sdk/agent.py` | Provider + Buyer SDK |
| `examples/two_agents_trading.py` | Agent-to-agent trading demo |
| `docs/hackathon/WHY_ARC.md` | Why micropayments need Arc |

## Team

**JudyAI Lab** — Building open-source AI infrastructure for the agent economy.

- **Judy** — CEO & Product Vision
- **J** — COO & Technical Director
- **Ada** — Product Engineer
- **Lily** — Content Director
- **Mimi** — Marketing & Growth

## Links

- GitHub: [github.com/JudyaiLab/agentictrade](https://github.com/JudyaiLab/agentictrade)
- Live: [agentictrade.io](https://agentictrade.io)
- MCP Server: [pypi.org/project/agentictrade-mcp](https://pypi.org/project/agentictrade-mcp/)
- Arc Submission: [docs/hackathon/arc_submission.md](arc_submission.md)
