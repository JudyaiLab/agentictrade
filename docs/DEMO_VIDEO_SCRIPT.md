# AgenticTrade Demo Video Script — ARC Hackathon

> **Target: 4-5 minutes**
> **Deadline: 2026-04-25**
> **Tracks: Best Autonomous Commerce / Best Gateway-Based Micropayments / Best Trustless AI Agent / Best Dev Tools**
> **Required in video: Transaction via Circle Developer Console + verify on Arc Block Explorer**

---

## Complete Voiceover Script (English — Judy reads)

---

### Page 1 — Title (0:00–0:15) ~15s

> "Hi, I'm Judy from JudyAI Lab. Today I'm presenting AgenticTrade — the commerce layer for the agent economy. It's an open-source AI service marketplace with x402 nanopayments on Arc. The platform is live at agentictrade.io, and the code is fully open source on GitHub."

---

### Page 2 — Why I Built AgenticTrade (0:15–1:05) ~50s

> "Why did I build this? I've been working with AI for years, and I noticed something important. A medical expert trains a far better medical AI than I ever could. A financial analyst builds a sharper trading model. Every domain specialist creates AI with unique strengths that generalists can't replicate.
>
> So I asked myself — why use a mediocre generalist when my AI can autonomously hire the exact domain expert it needs?
>
> That's the vision behind AgenticTrade. The future is AI agents autonomously finding the right partner and the right capability. But to make this work, agents need to pay each other — at sub-cent scale, without human approval. That's where Arc and Circle Nanopayments come in."

---

### Page 3 — The $2 Gas Fee Problem (1:05–1:30) ~25s

> "Here's the friction. On traditional Web3 rails, a single transaction costs 50 cents to 5 dollars in gas. But an AI agent API call is worth just one-tenth of a cent — $0.001. When your gas fee is two thousand times the value of the transaction, micro-commerce is mathematically impossible.
>
> AgenticTrade on Arc solves this. $0.001 per API call, zero gas. Nanopayments unlock autonomous trading at a scale that was never possible before."

---

### Page 4 — Architecture (1:30–2:00) ~30s

> "The architecture is a simple four-step loop — and it's fully end-to-end autonomous, no human approval required.
>
> Step 1: The Buyer Agent discovers services via MCP or OpenAPI.
> Step 2: It calls the service, and our proxy handles authentication and metering automatically.
> Step 3: An x402 nanopayment is triggered — just $0.001 USDC, zero gas.
> Step 4: The Provider receives USDC instantly, and the Buyer receives the AI result.
>
> Let me show you how this works in practice."

**[INSERT Demo Clip: two_agents_trading.py — agent registers, discovers, calls, pays]**

---

### Page 5 — Service Listing & Discovery (2:00–2:30) ~30s

> "Listing a service takes 30 seconds. A provider writes three lines of code — register a function, set a price, and the service is live. One API call is all it takes.
>
> On the discovery side, we support six protocols — MCP, OpenAPI, ACDP, llms.txt, Agent Playbook, and OpenAI Plugin. Any AI agent — whether it's Claude, GPT, LangChain, or a custom build — can discover and consume these services in milliseconds.
>
> Right now we have 29 registered services, 201 registered agents, and over 180 API endpoints."

**[INSERT Demo Clip: Service discovery API + MCP manifest]**

---

### Page 6 — Agent-to-Agent Commerce: The Core Loop (2:30–3:05) ~35s

> "This is the core loop in action. Our Buyer Agent — Judy-Buyer — pings an API, checks the price, triggers an x402 nanopayment, and the data is delivered to the Mimi-Research Agent on the other side.
>
> Four key numbers on screen:
> Zero human intervention — agents handle everything.
> Zero gas fees — thanks to x402 off-chain batching on Arc.
> Instant settlement — no waiting for block confirmations.
> And $0.001 per transaction — true sub-cent pricing.
>
> This is not a concept. It's running in production on Arc testnet right now."

---

### Page 7 — Mission Control: Real Transactions (3:05–3:35) ~30s

> "Here's the data. We built two payment layers — x402 nanopayments for zero-gas micropayments, and Circle Wallets for on-chain settlement. Together, our agents have processed over 600 transactions across both layers.
>
> The dashboard shows x402 micropayments — 589 off-chain calls at $0.001 each, zero gas. Three autonomous agents — Judy-Buyer, Mimi-Research, and Moongg-QA — trading across 6 services including CoinSifter Pro and SignalFuse.
>
> On top of that, we have 60-plus on-chain USDC transfers via Circle Programmable Wallets on Arc, each with gas under half a cent. Both layers together give us the best of both worlds."

**[INSERT Demo Clip: nano-dashboard showing live data]**

---

### Page 8 — On-Chain Proof: Trust, Verified (3:35–4:05) ~30s

> "Let me show you the on-chain proof. Here's the Arc Block Explorer showing our seller wallet — you can see multiple USDC transactions between our buyer and seller agents, each $0.01, all confirmed on Arc. And here's the buyer agent's wallet with its own transaction history.
>
> Now let's look at the Circle Developer Console. Both wallets are on ARC-TESTNET — the seller with 35 USDC, the buyer with about 5 USDC. You can see 20 transaction records — each one is a real on-chain USDC transfer between autonomous agents, with gas fees under half a cent per transaction.
>
> We also have 589 additional micropayments at $0.001 each processed via x402 off-chain batching — that's the other layer. x402 lets agents make sub-cent API calls with zero gas, while Circle Wallets handle the on-chain settlement.
>
> Full transparency, fully verifiable. This is real infrastructure on Arc."

**[INSERT Demo Clip: Arc Block Explorer showing buyer/seller transactions + Judy records Circle Console]**

---

### Page 9 — Governance & Safety (4:05–4:30) ~25s

> "Autonomous doesn't mean uncontrolled. We built three governance layers.
>
> First, the Budget Governor. Human owners set absolute spending limits — daily, per-call, and monthly. Your agent can never exceed what you allow.
>
> Second, Agent Negotiation. Agents autonomously haggle for market-driven pricing. In this example, the buyer offered 5 cents, the seller countered at 2 cents, and they settled at 2 cents. Owners retain the full audit history.
>
> Third, Trust and Reputation. Reliable buyer agents build reputation scores over time, and high-trust agents earn automated discounts — like the 5% discount unlocked here.
>
> Human oversight plus agent autonomy. These are the guardrails the agent economy needs."

**[INSERT Demo Clips: Budget UI + Negotiation history + Reputation leaderboard]**

---

### Page 10 — The Business Value (4:30–4:50) ~20s

> "How does this compare? Against RapidAPI, we charge 0 to 10% commission — capped — versus their flat 25%. We're AI agent native with x402 USDC. Setup takes 30 seconds, not 30 minutes. We're fully open source under MIT license. We support sub-cent pricing at $0.001, and gas fees are zero on Arc.
>
> The total addressable market is $52.6 billion for AI agents by 2030, and McKinsey estimates agent commerce at 3 to 5 trillion dollars.
>
> This is production-ready, battle-tested, and built to scale."

---

### Page 11 — Closing (4:50–5:00) ~10s

> "Agents discover. Agents trade. Agents pay. Zero friction. Zero gas. Zero human intervention.
>
> Try the live demo at agentictrade.io. Check out the code on GitHub. And see real transaction data on our nano-dashboard.
>
> Built by JudyAI Lab. Thank you Arc, Circle, and LabLab for this opportunity."

---

## Demo Clips to Insert (Already Recorded)

| Clip File | Insert After | Content |
|-----------|-------------|---------|
| `clip_c_trading.webm` / terminal run | Page 4 | Agent-to-Agent trading full flow |
| `clip_b_discovery.webm` | Page 5 | Discovery API + MCP manifest |
| `clip_d_nano_dashboard.webm` | Page 7 | Nano dashboard live data |
| `clip_e_arc_explorer.webm` | Page 8 | Arc Explorer TX + seller address ★ Required |
| `clip_f_budget.webm` | Page 9 | Budget Governor UI |
| `clip_g_negotiations.webm` | Page 9 | Negotiation history |
| `clip_h_reputation.webm` | Page 9 | Reputation + leaderboard |

**Circle Developer Console** — Judy records this manually (login required): open `console.circle.com`, show wallet activity, insert alongside clip_e.

---

## Final Edit Timeline

```
Page 1   — Judy voiceover: Opening (15s)
Page 2   — Judy voiceover: Why I built this ★ Soul of pitch (50s)
Page 3   — Judy voiceover: $2 gas fee problem (25s)
Page 4   — Judy voiceover: Architecture + [Demo: agent trading] (30s)
Page 5   — Judy voiceover: Listing & Discovery + [Demo: API discovery] (30s)
Page 6   — Judy voiceover: Core Loop (35s)
Page 7   — Judy voiceover: 564 transactions + [Demo: nano-dashboard] (30s)
Page 8   — Judy voiceover: On-chain proof + [Demo: Arc Explorer + Circle Console] ★ Required (30s)
Page 9   — Judy voiceover: Governance + [Demo: Budget + Negotiation + Reputation] (25s)
Page 10  — Judy voiceover: Business Value (20s)
Page 11  — Judy voiceover: Closing (10s)
                                                          Total: ~5:00
```

---

## Pre-Recording Checklist

- [ ] Practice reading the full voiceover once (time it — should be ~4:30 of speaking)
- [ ] All demo clips in `docs/demo_clips/` — watch each one to make sure they look good
- [ ] Circle Developer Console — login and record wallet screen (30s)
- [ ] agentictrade.io is up: `curl https://agentictrade.io/api/health`
- [ ] Nano dashboard shows current data: https://agentictrade.io/nano-dashboard
- [ ] Status page shows operational: https://agentictrade.io/status

## Tips

- Page 2 is the soul of the pitch — speak slowly and sincerely, not like reading a script
- Pages 7-8 are data-heavy — let the slides do the talking, just narrate the key numbers
- Page 9 has three sub-sections — pace yourself, don't rush through governance
- Keep energy up for Page 11 closing — end strong
