# Why Arc? The Economics of Agent Micropayments

> When an AI agent makes a $0.001 API call, the gas fee on traditional chains costs more than the payment itself. Agent commerce is economically impossible on legacy infrastructure.

---

## 1. The Problem: Gas > Payment

Every on-chain payment incurs gas fees. For human-scale transactions ($50+ purchases), gas is a rounding error. For agent-scale transactions ($0.001-$0.01 API calls), gas is a dealbreaker.

| Chain | Avg Gas Fee | Payment | Net to Provider | Viable? |
|-------|-------------|---------|-----------------|---------|
| Ethereum L1 | $0.50 - $5.00 | $0.01 | -$0.49 to -$4.99 | No |
| Polygon | $0.01 - $0.05 | $0.01 | -$0.00 to -$0.04 | No |
| Arbitrum | $0.01 - $0.10 | $0.01 | -$0.00 to -$0.09 | No |
| Base (L2) | $0.001 - $0.01 | $0.01 | $0.00 to $0.009 | Marginal |
| **Arc + x402** | **$0 (batched)** | **$0.01** | **$0.009** | **Yes** |

The takeaway: on Ethereum, a $0.01 payment costs up to $5.00 in gas. That is a 50,000% overhead. No business model survives that.

---

## 2. x402 Payment Protocol: HTTP-Native Payments

x402 eliminates the separate "transaction step" entirely. Payment happens inside the HTTP request cycle.

### How It Works

```
1. Agent sends GET /api/market-data
2. Server returns 402 Payment Required + price + payee address
3. Agent signs an EIP-712 payment authorization
4. Agent retries with payment proof in HTTP header
5. Server verifies signature, serves data, queues settlement
```

### Why This Matters for Agents

- **No checkout page.** Agents do not have browsers. x402 embeds payment into the API call itself.
- **No wallet popups.** The EIP-712 signature is programmatic, not interactive.
- **No separate transaction.** Payment and data retrieval happen in a single HTTP round-trip.
- **Standard HTTP semantics.** Any HTTP client can implement x402. No blockchain SDK required on the caller side.

The result: an AI agent pays for an API call the same way it sends any HTTP request. Payment becomes invisible infrastructure.

---

## 3. Arc's Unique Advantages for Agent Commerce

### Sub-Second Finality
Agent workflows are real-time. An agent calling 10 APIs in sequence cannot wait 12 seconds (Ethereum) or even 2 seconds (Polygon) per payment. Arc provides sub-second finality, keeping agent latency under control.

### USDC as Native Gas Token
On Ethereum, agents need ETH for gas and USDC for payment -- two tokens, two balances to manage. Arc uses USDC as its native gas token. One token. One balance. Simpler agent treasury management.

### Batched Settlement via x402
This is the critical differentiator. Individual micropayments are not settled on-chain one by one. Instead:

1. Agent makes 1,000 API calls over an hour
2. Each call is authorized via EIP-712 signature (off-chain, zero gas)
3. The x402 gateway batches these into a single on-chain settlement
4. Gas cost is amortized: $0.001 gas / 1,000 calls = $0.000001 per call

### Zero Gas for Individual Calls
The gateway absorbs settlement costs. From the agent's perspective, every API call costs exactly the listed price. No gas estimation, no fee spikes, no failed transactions due to gas price changes.

---

## 4. Real Numbers from AgenticTrade

Our platform runs on Arc testnet with x402 payments. Here are the actual numbers:

| Metric | Value |
|--------|-------|
| Default price per API call | $0.001 |
| Demo transactions completed | 55+ |
| Gas paid by buyer per call | $0.00 |
| Settlement method | Batched on-chain |
| Payment protocol overhead | ~50ms (signature generation) |
| Failed transactions due to gas | 0 |

Every transaction on AgenticTrade follows the same flow:
1. Buyer agent discovers endpoint via MCP tool catalog
2. Agent calls endpoint, receives `402 Payment Required`
3. Agent signs payment, retries with `X-PAYMENT` header
4. Server delivers data, queues settlement
5. Buyer pays exactly the listed price. Nothing more.

---

## 5. The Math: Why This Matters at Scale

Agent commerce is high-frequency, low-value. The economics only work when transaction costs approach zero.

### Scenario: 10,000 API calls per day at $0.01 each

| Chain | Gross Revenue | Gas Cost | Net Revenue | Margin |
|-------|---------------|----------|-------------|--------|
| Ethereum L1 | $100 | $5,000 (10K x $0.50) | -$4,900 | -4900% |
| Polygon | $100 | $300 (10K x $0.03) | -$200 | -200% |
| Arbitrum | $100 | $500 (10K x $0.05) | -$400 | -400% |
| Base (L2) | $100 | $50 (10K x $0.005) | $50 | 50% |
| **Arc + x402** | **$100** | **$0.01** (10 batches x $0.001) | **$99.99** | **99.99%** |

### At higher scale: 1,000,000 calls/day

| Chain | Daily Gas Cost | Annual Gas Cost |
|-------|---------------|-----------------|
| Ethereum L1 | $500,000 | $182,500,000 |
| Polygon | $30,000 | $10,950,000 |
| Arc + x402 | $1.00 | $365 |

The difference is not incremental. It is structural. Traditional chains were designed for human transaction patterns (few, large). Agent commerce requires machine transaction patterns (many, tiny).

---

## 6. Why Not Just Use Stripe?

A fair question. Traditional payment rails avoid gas fees entirely. But they introduce different blockers for agent commerce:

| Requirement | Stripe | Arc + x402 |
|-------------|--------|------------|
| Minimum transaction | $0.50 | $0.0001 |
| Settlement time | 2-7 days | Sub-second |
| Machine-to-machine auth | OAuth + API keys | Cryptographic signatures |
| Cross-border, permissionless | KYC required | Wallet-to-wallet |
| Programmable payment logic | Limited webhooks | On-chain smart contracts |
| Agent-native integration | Not designed for agents | Built for agents |

Stripe works for humans buying software. It does not work for agents buying API calls at $0.001 each, thousands of times per day, across jurisdictions, with instant settlement.

---

## 7. Conclusion

Agent commerce has three non-negotiable requirements:

1. **Sub-cent transaction costs** -- because payments are $0.001-$0.01
2. **Sub-second finality** -- because agents operate in real-time pipelines
3. **HTTP-native payment** -- because agents speak HTTP, not checkout flows

Only Arc + x402 satisfies all three today.

The question is not whether AI agents will transact autonomously. They already do. The question is which payment infrastructure makes it economically viable. At $0.001 per call and 10,000 calls per day, the math is unambiguous: Arc is the only chain where agent micropayments produce positive unit economics.

---

*Built with AgenticTrade on Arc Testnet -- 55+ transactions, zero gas overhead, 99.99% margin.*
