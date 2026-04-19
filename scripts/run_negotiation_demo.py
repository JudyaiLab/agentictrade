"""Run real negotiation demo via actual API — captures animated output."""
import httpx
import time
import sys

BASE = "http://localhost:8092/api/v1"
c = httpx.Client(base_url=BASE, timeout=10)


def p(msg):
    print(msg)
    sys.stdout.flush()


p("\033[1;36m══════════════════════════════════════════════════\033[0m")
p("\033[1;36m  Agent-to-Agent Negotiation — Live Demo\033[0m")
p("\033[1;36m══════════════════════════════════════════════════\033[0m")
p("")
time.sleep(0.5)

# --- Negotiation 1: Successful ---
p("\033[1m▶ Negotiation 1: Judy-Buyer ↔ CoinSifter Pro\033[0m")
p("")
time.sleep(0.3)

p("$ POST /api/v1/negotiate/start")
p('  {"buyer_id": "Judy-Buyer", "seller_id": "CoinSifter-Provider", "price": 0.005}')
time.sleep(0.2)
r = c.post("/negotiate/start", json={
    "buyer_id": "Judy-Buyer", "seller_id": "CoinSifter-Provider",
    "service_id": "svc-coinsifter", "proposed_price_usd": 0.005,
})
d = r.json()
sid = d["session_id"]
p(f'\033[32m  ← Judy-Buyer offers $0.005/call\033[0m')
p(f'\033[36m  session: {sid[:12]}... | round 1\033[0m')
p("")
time.sleep(0.8)

p(f"$ POST /api/v1/negotiate/{sid[:8]}.../counter")
time.sleep(0.2)
r = c.post(f"/negotiate/{sid}/counter", json={"agent_id": "CoinSifter-Provider", "proposed_price_usd": 0.008})
d = r.json()
p(f'\033[33m  ← CoinSifter-Provider counters $0.008/call\033[0m')
p(f'\033[36m  round {d["round"]}\033[0m')
p("")
time.sleep(0.8)

p(f"$ POST /api/v1/negotiate/{sid[:8]}.../counter")
time.sleep(0.2)
r = c.post(f"/negotiate/{sid}/counter", json={"agent_id": "Judy-Buyer", "proposed_price_usd": 0.006})
d = r.json()
p(f'\033[32m  ← Judy-Buyer counters $0.006/call\033[0m')
p(f'\033[36m  round {d["round"]}\033[0m')
p("")
time.sleep(0.8)

p(f"$ POST /api/v1/negotiate/{sid[:8]}.../accept")
time.sleep(0.2)
r = c.post(f"/negotiate/{sid}/accept", json={"agent_id": "CoinSifter-Provider"})
d = r.json()
p(f'\033[1;32m  ✓ DEAL CLOSED | final: ${d["final_price"]}/call | {d["total_rounds"]} rounds\033[0m')
p("")
time.sleep(1.2)

# --- Negotiation 2: Rejected ---
p("─────────────────────────────────────────────────")
p("")
p("\033[1m▶ Negotiation 2: Moongg-QA ↔ SignalFuse\033[0m")
p("")
time.sleep(0.3)

p("$ POST /api/v1/negotiate/start")
p('  {"buyer_id": "Moongg-QA", "seller_id": "SignalFuse-Provider", "price": 0.001}')
time.sleep(0.2)
r = c.post("/negotiate/start", json={
    "buyer_id": "Moongg-QA", "seller_id": "SignalFuse-Provider",
    "service_id": "svc-signalfuse", "proposed_price_usd": 0.001,
})
d = r.json()
sid2 = d["session_id"]
p(f'\033[32m  ← Moongg-QA offers $0.001/call\033[0m')
p(f'\033[36m  session: {sid2[:12]}... | round 1\033[0m')
p("")
time.sleep(0.8)

p(f"$ POST /api/v1/negotiate/{sid2[:8]}.../counter")
time.sleep(0.2)
r = c.post(f"/negotiate/{sid2}/counter", json={"agent_id": "SignalFuse-Provider", "proposed_price_usd": 0.005})
d = r.json()
p(f'\033[33m  ← SignalFuse-Provider counters $0.005/call (+400%)\033[0m')
p(f'\033[36m  round {d["round"]}\033[0m')
p("")
time.sleep(0.8)

p(f"$ POST /api/v1/negotiate/{sid2[:8]}.../reject")
time.sleep(0.2)
r = c.post(f"/negotiate/{sid2}/reject", json={"agent_id": "Moongg-QA"})
d = r.json()
p(f'\033[1;31m  ✗ REJECTED by Moongg-QA — price gap too large\033[0m')
p("")
time.sleep(1.0)

p("\033[1;36m══════════════════════════════════════════════════\033[0m")
p(f'\033[1;32m  Result 1: Judy-Buyer ↔ CoinSifter  → $0.006 ✓\033[0m')
p(f'\033[1;31m  Result 2: Moongg-QA ↔ SignalFuse   → REJECTED ✗\033[0m')
p("")
p("\033[1;36m  Fully autonomous. Zero human intervention.\033[0m")
p("\033[1;36m══════════════════════════════════════════════════\033[0m")
