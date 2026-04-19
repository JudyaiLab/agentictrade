---
title: "AgenticTrade Provider Onboarding Guide"
description: "The complete developer guide to listing, configuring, and monetizing your AI services on AgenticTrade — from signup to first automated payment. Two paths: External API or Prompt-as-API."
date: 2026-03-28
tags: ["agentictrade", "api", "developers", "onboarding", "provider", "prompt-as-api"]
categories: ["Developer Guide"]
---

# AgenticTrade Provider Onboarding Guide

Welcome to AgenticTrade. This guide walks you through listing your AI service and earning your first automated micro-payment from an AI agent. **No blockchain experience required.**

## Two Ways to Sell on AgenticTrade

| | External API (Type 1) | Prompt-as-API (Type 2) |
|---|---|---|
| **You provide** | HTTP endpoint (your existing API) | A system prompt |
| **Code required** | Yes (your API server) | No |
| **API key** | N/A (your own backend) | Your Anthropic key (stays on YOUR machine) |
| **Setup time** | 20–30 minutes | 3 minutes |
| **Best for** | Existing APIs, data services | Prompt engineering, AI expertise |
| **Cost to you** | Your server + compute | Anthropic API tokens (~$0.002–$0.02/call) |

**Not sure which to pick?**
- Already have an API? → **Type 1 (External API)**
- Have AI expertise but no API? → **Type 2 (Prompt-as-API)**

---

## Path A: Prompt-as-API (Recommended for New Providers)

**Turn your system prompt into a paid API in 3 minutes. No code required.**

Your Anthropic API key never leaves your computer. You run a lightweight server locally, expose it via a tunnel (ngrok), and paste the URL into your AgenticTrade dashboard. The platform routes buyer requests to your machine, your machine calls Claude, and the result goes back through the platform.

```
Buyer Agent → AgenticTrade Platform → Your Machine (Provider Agent) → Claude API
                                       ↑
                              Your API key stays here
```

### Step A1: Create Your Provider Account

1. Visit [agentictrade.io/portal/register](https://agentictrade.io/portal/register)
2. Enter your email, display name, and password
3. You're in — you'll see your **Provider Dashboard**

### Step A2: Set Up the Provider Agent

```bash
# Download the Provider Agent (single file, ~300 lines)
curl -O https://agentictrade.io/provider_agent.py

# Install the only dependency
pip install anthropic

# Run the setup wizard (paste your Anthropic API key when prompted)
python provider_agent.py --setup
```

The setup wizard:
1. Asks for your Anthropic API key (get one at [console.anthropic.com](https://console.anthropic.com/settings/keys))
2. Validates the key with a real API call
3. Saves it to a local `.env` file — **never sent to AgenticTrade**

### Step A3: Start Your Agent

```bash
python provider_agent.py
```

You'll see:
```
====================================================
  AgenticTrade Provider Agent
====================================================
  Status:   Running
  Listen:   http://0.0.0.0:8080
  API key:  sk-ant-api0...b4xQ
  .env:     loaded
----------------------------------------------------
  Tunnel:   ngrok http 8080
  Verify:   python provider_agent.py --test
  Stop:     Ctrl+C
```

### Step A4: Expose to the Internet

In a new terminal:

```bash
ngrok http 8080
```

Copy the HTTPS URL (e.g., `https://abc123.ngrok-free.app`).

### Step A5: Create Your Service

1. Go to your **Provider Dashboard** → **Create Service**
2. Choose **Prompt-as-API** type
3. Fill in:
   - **Service Name**: e.g., "Expert Code Reviewer"
   - **System Prompt**: Your prompt that defines the AI's behavior
   - **Model**: claude-haiku-4-5 (cheapest) or claude-sonnet-4-6
   - **Price per call**: e.g., $0.01
   - **Provider Agent URL**: Paste your ngrok URL
4. Click **Create**

### Step A6: Verify It Works

```bash
python provider_agent.py --test
```

All 3 tests should pass:
```
  1/3  Health check ... PASS
  2/3  Prompt execution ... PASS  "pong"  12+3 tok  340ms
  3/3  Error handling ... PASS

  All tests passed. Agent is ready.
```

### Pricing Guide for Prompt-as-API

Your cost is the Anthropic API tokens your agent consumes. Here's a guide:

| Model | Your Cost/Call | Suggested Price | Your Profit |
|-------|---------------|-----------------|-------------|
| Claude Haiku 4.5 | ~$0.002 | $0.005–$0.01 | 150%–400% |
| Claude Sonnet 4.6 | ~$0.006 | $0.02–$0.05 | 233%–733% |

**Rule of thumb**: Price at 3x your token cost. Use the [Pricing Calculator](https://agentictrade.io/pricing#calculator) for exact numbers.

### Security: Your API Key is Safe

- Your Anthropic API key lives in `.env` on YOUR machine
- AgenticTrade **never** sees, stores, or transmits your key
- The Provider Agent is a single open-source Python file — audit it yourself
- Only the prompt result (not the key) flows through the platform

---

## Path B: External API (For Existing APIs)

**Already have an HTTP endpoint? List it on AgenticTrade in 20 minutes.**

### Step B1: Create Your Provider Account

Same as Path A — register at [agentictrade.io/portal/register](https://agentictrade.io/portal/register).

### Step B2: Register Your API Service

From your **Provider Dashboard** → **Create Service** → **External API**:

1. **Service Name**: Your API name
2. **Endpoint URL**: Your API's base URL (e.g., `https://api.yourdomain.com/v1`)
3. **Category**: ai, data, utility, etc.
4. **Price per call**: In USD
5. **Free tier calls**: Optional (e.g., 10 free calls for new buyers)

Or via the API:

```bash
curl -X POST https://agentictrade.io/api/v1/services \
  -H "Authorization: Bearer $VENDOR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Your API Name",
    "endpoint": "https://api.yourdomain.com/v1/process",
    "price_per_call": "0.05",
    "category": "ai",
    "tags": ["nlp", "analysis"],
    "free_tier_calls": 10
  }'
```

### Step B3: Issue a Proxy API Key

Never give agents your raw API key. Create a **proxy key** through AgenticTrade:

```bash
curl -X POST https://agentictrade.io/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{"owner_id": "your-provider-id", "role": "provider"}'
```

Proxy keys route through the platform's billing layer, which meters usage, enforces rate limits, and charges the calling agent's balance.

### Step B4: Publish Your MCP Tool Descriptor

Make your API discoverable by AI agents via MCP (Model Context Protocol):

```bash
curl -X PUT https://agentictrade.io/api/v1/mcp/$SERVICE_ID/descriptor \
  -H "Authorization: Bearer $VENDOR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "schema_version": "1.0",
    "name": "your_api_slug",
    "description": "What capability your API provides to agents.",
    "tools": [
      {
        "name": "analyze",
        "description": "Analyze input data and return insights.",
        "input_schema": {
          "type": "object",
          "properties": {
            "data": { "type": "string", "description": "Input data to analyze." }
          },
          "required": ["data"]
        },
        "pricing": { "cost_usd": 0.05, "unit": "per_call" }
      }
    ]
  }'
```

**Descriptor tips:**
- Be specific in descriptions — agents use them to decide whether to call your API
- Set per-tool pricing if your endpoints have different compute costs
- Start with 2–3 tools, add more later

### Step B5: Test the Full Flow

```bash
# Simulate an agent discovering your service
curl https://agentictrade.io/api/v1/discover?category=ai

# Simulate an agent call through the proxy
curl -X POST "https://agentictrade.io/api/v1/proxy/$SERVICE_ID/analyze" \
  -H "Authorization: Bearer $BUYER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"data": "test input"}'
```

Check your dashboard — you should see the call logged with billing headers.

---

## How You Get Paid

AgenticTrade settles revenue automatically. No invoices required.

### Commission Tiers

| Phase | Months | Commission | You Receive |
|-------|--------|------------|-------------|
| Launch | Month 1 | **0%** | 100% |
| Growth | Months 2–3 | **5%** | 95% |
| Standard | Month 4+ | **10%** | 90% |

### Settlement Options

| Method | Currency | Min Payout |
|--------|----------|------------|
| x402 (USDC on Base) | USDC | $10 |
| PayPal | USD/EUR/GBP | $10 |
| NOWPayments | 300+ cryptos | $10 |

### Commission Comparison

| Monthly Revenue | RapidAPI (25%) | AgenticTrade (10%) | Annual Savings |
|-----------------|---------------|---------------------|----------------|
| $5,000 | $15,000/yr | $6,000/yr | **$9,000** |
| $20,000 | $60,000/yr | $24,000/yr | **$36,000** |
| $50,000 | $150,000/yr | $60,000/yr | **$90,000** |

---

## Monitor and Optimize

Your provider dashboard shows:

- **Call logs** — Every agent call, timestamp, cost, and agent ID
- **Revenue** — Daily/weekly/monthly trends
- **Reputation score** — Computed from latency, reliability, and response quality
- **Rate limit monitoring** — Which agents are hitting limits

Higher reputation scores improve your discovery ranking. Maintain low latency and high reliability to maximize visibility.

---

## Dispute Resolution

AgenticTrade uses an escrow system to protect both buyers and providers.

### Escrow Hold Periods

| Transaction Amount | Hold Period | Dispute Window |
|--------------------|-------------|----------------|
| Under $1 | 1 day | 24 hours |
| $1 – $100 | 3 days | 72 hours |
| Over $100 | 7 days | 7 days |

### Dispute Process

1. **Buyer opens a dispute** — Selects category and submits evidence
2. **You receive a notification** — Check your dashboard or the `escrow.dispute_opened` webhook
3. **You submit counter-evidence** — Server logs, timestamps, response proofs
4. **Admin reviews and resolves** — Binding decision if parties can't agree
5. **Auto-release** — If dispute window expires without admin action, payment releases to you

### Counter-Evidence Best Practices

- Include server-side logs showing the request was processed correctly
- Provide timestamps matching the disputed transaction
- Link to monitoring dashboards that confirm uptime and response quality

### Resolution Outcomes

| Outcome | Your Payout |
|---------|-------------|
| `release_to_provider` | Full amount released |
| `refund_buyer` | $0 — returned to buyer |
| `partial_refund` | You receive `hold_amount - refund_amount` |

### Minimizing Disputes

- Maintain high uptime and low latency
- Return clear error messages when requests fail
- Set accurate pricing — unexpected charges trigger disputes
- Monitor call logs for anomalies

---

## FAQ

**Q: Does AgenticTrade store my Anthropic API key?**
A: No. For Prompt-as-API services, your key stays on your machine. The Provider Agent is open source — audit it yourself.

**Q: What happens if my Provider Agent goes offline?**
A: Buyers receive a 503 error. Your service is temporarily unavailable but no charges occur. Restart the agent and it's back online.

**Q: Can I sell workflows, not just single prompts?**
A: Yes. The Provider Agent forwards requests to Claude. You can build multi-step workflows in your system prompt, or extend the agent with custom logic.

**Q: Do I need a crypto wallet?**
A: No. PayPal settlement is available. Crypto wallets are optional.

**Q: What's the minimum price I can set?**
A: $0.001 per call. We recommend at least 3x your token cost.

---

## Ready to Start?

| Path | Time | Link |
|------|------|------|
| **Prompt-as-API** | 3 minutes | [Register](https://agentictrade.io/portal/register) → Download `provider_agent.py` |
| **External API** | 20 minutes | [Register](https://agentictrade.io/portal/register) → Add your endpoint |

First month at 0% commission. Let the agents find you.

---

*Questions? Check the [API documentation](https://agentictrade.io/api-docs) or visit [agentictrade.io/providers](https://agentictrade.io/providers) for the full guide.*
