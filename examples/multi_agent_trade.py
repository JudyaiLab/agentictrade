"""
Agent Commerce Framework — Multi-Agent Trading Example

Three agents form a circular economy: A sells scraping and buys NLP,
B sells NLP and buys vision, C sells vision and buys scraping.

Run: python examples/multi_agent_trade.py
Requires: ACF server running + pip install httpx
"""
import os
import sys

import httpx

BASE_URL = os.getenv("ACF_BASE_URL", "http://localhost:8000")
API = f"{BASE_URL}/api/v1"


def _bootstrap_auth(client: httpx.Client) -> dict:
    """Create a temporary buyer key to authenticate provider key creation."""
    resp = client.post(f"{API}/keys", json={"owner_id": "bootstrap-multi", "role": "buyer"})
    resp.raise_for_status()
    d = resp.json()
    return {"Authorization": f"Bearer {d['key_id']}:{d['secret']}"}


def setup_agent(client: httpx.Client, name: str, display: str,
                caps: list[str], svc_name: str, svc_desc: str,
                svc_category: str, price: str,
                bootstrap_auth: dict | None = None) -> dict:
    """Register an agent with both provider and buyer keys + a service."""
    # Provider key requires auth — use bootstrap key
    headers = dict(bootstrap_auth) if bootstrap_auth else {}
    headers["Content-Type"] = "application/json"
    resp = client.post(f"{API}/keys", json={"owner_id": name, "role": "provider"}, headers=headers)
    resp.raise_for_status()
    pkey = resp.json()
    p_auth = {"Authorization": f"Bearer {pkey['key_id']}:{pkey['secret']}"}

    resp = client.post(f"{API}/agents", json={
        "display_name": display, "capabilities": caps,
    }, headers=p_auth)
    resp.raise_for_status()
    agent_id = resp.json()["agent_id"]

    resp = client.post(f"{API}/services", json={
        "name": svc_name,
        "description": svc_desc,
        "endpoint": f"https://{name}.example.com/api",
        "price_per_call": price,
        "category": svc_category,
        "tags": caps,
        "free_tier_calls": 3,
    }, headers=p_auth)
    resp.raise_for_status()
    svc_id = resp.json()["id"]

    # Buyer key (same owner, different role for purchasing)
    resp = client.post(f"{API}/keys", json={
        "owner_id": f"{name}-buyer", "role": "buyer",
    })
    resp.raise_for_status()
    bkey = resp.json()
    b_auth = {"Authorization": f"Bearer {bkey['key_id']}:{bkey['secret']}"}

    return {
        "name": name,
        "agent_id": agent_id,
        "service_id": svc_id,
        "provider_auth": p_auth,
        "buyer_auth": b_auth,
    }


def call_service(client: httpx.Client, buyer_auth: dict,
                 service_id: str, payload: dict) -> dict:
    """Call a service through the marketplace proxy."""
    resp = client.post(
        f"{API}/proxy/{service_id}/process",
        headers=buyer_auth,
        json=payload,
    )
    return {
        "status": resp.status_code,
        "amount": resp.headers.get("X-ACF-Amount", "0"),
        "free": resp.headers.get("X-ACF-Free-Tier", "false") == "true",
    }


def main():
    client = httpx.Client(timeout=30)

    # ── Register three agents ───────────────────────────────────
    print("=== Registering Agents ===")
    boot_auth = _bootstrap_auth(client)

    agent_a = setup_agent(
        client, "data-collector", "Data Collector Bot",
        ["scraping", "data"], "Web Scraper API",
        "Extract structured data from any URL", "data", "0.05",
        bootstrap_auth=boot_auth,
    )
    print(f"  A: {agent_a['name']}  service={agent_a['service_id'][:8]}...")

    agent_b = setup_agent(
        client, "nlp-engine", "NLP Analysis Engine",
        ["nlp", "sentiment"], "Sentiment Analyzer",
        "Analyze text sentiment and extract entities", "ai", "0.08",
        bootstrap_auth=boot_auth,
    )
    print(f"  B: {agent_b['name']}  service={agent_b['service_id'][:8]}...")

    agent_c = setup_agent(
        client, "vision-model", "Vision Classifier",
        ["vision", "classification"], "Image Classifier",
        "Classify images into 1000 categories", "ai", "0.12",
        bootstrap_auth=boot_auth,
    )
    print(f"  C: {agent_c['name']}  service={agent_c['service_id'][:8]}...")

    # ── Circular trading: A→B, B→C, C→A (2 rounds) ─────────────
    trades = [
        ("A→B", agent_a["buyer_auth"], agent_b["service_id"], {"text": "Analyze BTC sentiment"}),
        ("B→C", agent_b["buyer_auth"], agent_c["service_id"], {"image_url": "https://example.com/chart.png"}),
        ("C→A", agent_c["buyer_auth"], agent_a["service_id"], {"url": "https://example.com/feed"}),
        ("A→B", agent_a["buyer_auth"], agent_b["service_id"], {"text": "Summarize ETH news"}),
        ("B→C", agent_b["buyer_auth"], agent_c["service_id"], {"image_url": "https://example.com/heatmap.png"}),
        ("C→A", agent_c["buyer_auth"], agent_a["service_id"], {"url": "https://example.com/prices"}),
    ]

    print("\n=== Trading Round ===")
    for label, auth, svc_id, payload in trades:
        result = call_service(client, auth, svc_id, payload)
        tier = "FREE" if result["free"] else f"${result['amount']}"
        print(f"  {label}  HTTP {result['status']}  {tier}")

    # ── Check marketplace stats ─────────────────────────────────
    print("\n=== Marketplace Stats ===")
    resp = client.get(f"{API}/discover/categories")
    if resp.status_code == 200:
        cats = resp.json().get("categories", resp.json()) if isinstance(resp.json(), dict) else resp.json()
        for cat in cats:
            print(f"  {cat['category']}: {cat['count']} service(s)")

    # Check each agent's reputation
    print("\n=== Agent Reputations ===")
    for agent in [agent_a, agent_b, agent_c]:
        resp = client.get(f"{API}/agents/{agent['agent_id']}/reputation")
        score = "N/A"
        if resp.status_code == 200 and (recs := resp.json().get("records")):
            score = recs[0]["overall_score"]
        print(f"  {agent['name']:20s}  score={score}")

    print("\n✓ Multi-agent trading complete!")
    print(f"  3 agents, 6 trades, circular economy on {BASE_URL}")


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print(f"Error: Cannot connect to {BASE_URL}")
        print("Start the server first:  uvicorn api.main:app --port 8000")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"HTTP Error: {e.response.status_code} — {e.response.text}")
        sys.exit(1)
