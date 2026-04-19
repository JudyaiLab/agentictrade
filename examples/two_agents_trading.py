"""
Agent Commerce Framework -- Two Agents Trading Example

Demonstrates the core use case: Agent A registers a service, Agent B
discovers it on the marketplace, calls it through the payment proxy,
and payment flows through automatically.

Flow:
  1. Agent A (provider) registers an NLP service at $0.05/call
  2. Agent B (buyer) discovers it via the marketplace
  3. Agent B calls the service through the proxy -- payment handled automatically
  4. Both agents check their usage stats and reputation

Prerequisites:
    - ACF server running:  uvicorn api.main:app --port 8092
    - httpx installed:     pip install httpx

Run:
    python examples/two_agents_trading.py

Environment:
    ACF_BASE_URL  -- Server URL (default: http://localhost:8000)
"""
from __future__ import annotations

import os
import sys

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = os.getenv("ACF_BASE_URL", "http://localhost:8092")
API = f"{BASE_URL}/api/v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_key(
    client: httpx.Client,
    owner_id: str,
    role: str,
    auth_headers: dict | None = None,
) -> dict:
    """Create an API key and return credentials.

    Provider/admin keys require ``auth_headers`` from an existing key.
    """
    headers = dict(auth_headers) if auth_headers else {}
    headers["Content-Type"] = "application/json"
    resp = client.post(
        f"{API}/keys",
        json={"owner_id": owner_id, "role": role},
        headers=headers,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "key_id": data["key_id"],
        "secret": data["secret"],
        "auth": {"Authorization": f"Bearer {data['key_id']}:{data['secret']}"},
    }


def separator(title: str) -> None:
    """Print a section separator."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Main scenario
# ---------------------------------------------------------------------------

def main() -> None:
    client = httpx.Client(timeout=30)

    # ======================================================================
    # PHASE 1: Agent A sets up as a provider
    # ======================================================================
    separator("PHASE 1: Agent A (Provider) Setup")

    # Create provider key (requires auth, so bootstrap with a buyer key first)
    print("[A] Creating provider API key...")
    bootstrap = create_key(client, "bootstrap-two-agents", "buyer")
    agent_a_key = create_key(client, "agent-a-nlp", "provider", auth_headers=bootstrap["auth"])
    print(f"    Key: {agent_a_key['key_id']}")

    # Register agent identity
    print("[A] Registering agent identity...")
    resp = client.post(f"{API}/agents", json={
        "display_name": "Agent A - NLP Service",
        "capabilities": ["nlp", "sentiment-analysis", "entity-extraction"],
        "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
        "metadata": {
            "framework": "transformers",
            "model": "bert-base",
            "version": "2.0",
        },
    }, headers=agent_a_key["auth"])
    resp.raise_for_status()
    agent_a = resp.json()
    agent_a_id = agent_a["agent_id"]
    print(f"    Agent ID:     {agent_a_id}")
    print(f"    Display name: {agent_a['display_name']}")
    print(f"    Capabilities: {agent_a['capabilities']}")

    # Register service on marketplace
    print("[A] Listing service on marketplace...")
    resp = client.post(f"{API}/services", json={
        "name": "Sentiment Analysis API",
        "description": (
            "Analyze text sentiment with confidence scores. "
            "Supports English, Chinese, Korean, and Japanese. "
            "Returns positive/negative/neutral label with 0-1 confidence."
        ),
        "endpoint": "https://httpbin.org",
        "price_per_call": "0.05",
        "category": "ai",
        "tags": ["nlp", "sentiment", "multilingual"],
        "payment_method": "x402",
        "free_tier_calls": 5,
    }, headers=agent_a_key["auth"])
    resp.raise_for_status()
    service = resp.json()
    service_id = service["id"]
    print(f"    Service:  {service['name']}")
    print(f"    ID:       {service_id}")
    print(f"    Price:    ${service['pricing']['price_per_call']}/call")
    print(f"    Free:     {service['pricing']['free_tier_calls']} calls")
    print(f"    Category: {service['category']}")

    # ======================================================================
    # PHASE 2: Agent B discovers and subscribes to events
    # ======================================================================
    separator("PHASE 2: Agent B (Buyer) Discovery")

    # Create buyer key
    print("[B] Creating buyer API key...")
    agent_b_key = create_key(client, "agent-b-researcher", "buyer")
    print(f"    Key: {agent_b_key['key_id']}")

    # Browse categories
    print("[B] Browsing marketplace categories...")
    resp = client.get(f"{API}/discover/categories")
    if resp.status_code == 200:
        cats = resp.json().get("categories", [])
        for cat in cats[:5]:
            print(f"    - {cat['category']}: {cat['count']} service(s)")

    # Search for NLP services
    print("[B] Searching for NLP services...")
    resp = client.get(f"{API}/discover", params={
        "q": "sentiment",
        "category": "ai",
        "has_free_tier": "true",
    })
    resp.raise_for_status()
    results = resp.json()
    print(f"    Found {results['total']} matching service(s):")
    for svc in results["services"]:
        print(f"    - {svc['name']} (${svc['pricing']['price_per_call']}/call)")
        print(f"      Tags: {svc['tags']}")
        print(f"      Free tier: {svc['pricing']['free_tier_calls']} calls")

    # Get detailed service info
    print(f"[B] Getting service details for {service_id[:8]}...")
    resp = client.get(f"{API}/services/{service_id}")
    resp.raise_for_status()
    detail = resp.json()
    print(f"    Name:        {detail['name']}")
    print(f"    Description: {detail['description'][:80]}...")
    print(f"    Endpoint:    (proxied through marketplace)")

    # ======================================================================
    # PHASE 3: Agent B calls Agent A's service
    # ======================================================================
    separator("PHASE 3: Agent B Calls Agent A's Service")

    # 7 test texts: first 5 will be FREE (within free tier), last 2 are PAID
    test_texts = [
        "I absolutely love this new AI framework! It makes agent development so easy.",
        "The market crashed again today. Investors are worried about the economy.",
        "The weather is partly cloudy with a temperature of 22 degrees.",
        "This restaurant serves the best ramen I have ever tasted in my life.",
        "Scientists discovered a new exoplanet orbiting a nearby red dwarf star.",
        "The new tax policy will devastate small businesses across the country.",
        "Our team just shipped the fastest inference engine, beating all benchmarks!",
    ]

    # Deposit funds so paid calls (calls 6-7) succeed.
    # Uses the admin credit endpoint available in dev/test environments.
    print("[B] Depositing $1.00 USDC for paid calls...")
    admin_secret = os.getenv("ACF_ADMIN_SECRET", "test-admin-secret")
    resp = client.post(
        f"{API}/admin/credit",
        params={"buyer_id": "agent-b-researcher", "amount": 1.0},
        headers={"X-Admin-Key": admin_secret},
    )
    if resp.status_code == 200:
        balance_data = resp.json()
        print(f"    Balance: ${balance_data.get('new_balance', '?')} USDC")
    else:
        print(f"    Warning: Could not deposit funds (HTTP {resp.status_code})")
        print(f"    Paid calls may fail with 'Insufficient balance'")

    # Use a short timeout for proxy calls since httpbin.org may be slow.
    proxy_client = httpx.Client(timeout=15)

    free_tier_limit = 5
    for i, text in enumerate(test_texts, 1):
        tier_label = "FREE" if i <= free_tier_limit else "PAID"
        print(f"\n[B] Call {i}/{len(test_texts)} [{tier_label}]: Analyzing sentiment...")
        print(f"    Input: \"{text[:60]}...\"")

        try:
            resp = proxy_client.post(
                f"{API}/proxy/{service_id}/post",
                headers=agent_b_key["auth"],
                json={"text": text, "language": "en"},
            )

            status = resp.status_code
            amount = resp.headers.get("X-ACF-Amount", "0")
            free = resp.headers.get("X-ACF-Free-Tier", "false") == "true"
            latency = resp.headers.get("X-ACF-Latency-Ms", "?")

            if free:
                billing_info = f"FREE (call {i}/{free_tier_limit} free tier)"
            else:
                billing_info = f"${amount} USDC PAID"
            print(f"    Result: HTTP {status} | {billing_info} | {latency}ms")
        except (httpx.ReadTimeout, httpx.ReadError, httpx.ConnectError):
            print(f"    Result: Timeout/connection error (provider endpoint is external)")
            print(f"    Note: In production, the provider returns a real response.")

    # ======================================================================
    # PHASE 4: Check usage and reputation
    # ======================================================================
    separator("PHASE 4: Usage Stats and Reputation")

    # Buyer usage stats
    print("[B] Buyer usage statistics:")
    resp = client.get(f"{API}/usage/me", headers=agent_b_key["auth"])
    if resp.status_code == 200:
        usage = resp.json()
        print(f"    Total calls:  {usage['total_calls']}")
        print(f"    Total spent:  ${usage['total_spent_usd']}")
        print(f"    Avg latency:  {usage['avg_latency_ms']}ms")
    else:
        print(f"    (HTTP {resp.status_code})")

    # Provider reputation — query using the owner_id that matches
    # provider_id in usage_records (not the agent identity UUID).
    provider_owner_id = "agent-a-nlp"
    print(f"\n[A] Provider reputation (computed from usage):")
    resp = client.get(
        f"{API}/agents/{provider_owner_id}/reputation",
        params={"compute": "true"},
    )
    if resp.status_code == 200:
        rep = resp.json()
        if "overall_score" in rep:
            print(f"    Overall:     {rep['overall_score']}/10")
            print(f"    Latency:     {rep['latency_score']}/10")
            print(f"    Reliability: {rep['reliability_score']}/10")
            print(f"    Quality:     {rep['response_quality']}/10")
            print(f"    Calls:       {rep['call_count']}")
        else:
            print("    (Scores computed on next period)")

    # Leaderboard
    print("\n[*] Marketplace leaderboard:")
    resp = client.get(f"{API}/reputation/leaderboard", params={"limit": 5})
    if resp.status_code == 200:
        board = resp.json().get("leaderboard", [])
        if board:
            for rank, entry in enumerate(board, 1):
                verified = " [verified]" if entry.get("verified") else ""
                print(
                    f"    #{rank} {entry['display_name']} "
                    f"(score: {entry['reputation_score']}){verified}"
                )
        else:
            print("    (Empty -- build reputation with more trades)")

    # ======================================================================
    # Summary
    # ======================================================================
    separator("Trading Complete")
    print(f"  Provider (Agent A): {agent_a['display_name']}")
    print(f"    - Registered service: {service['name']}")
    print(f"    - Price: ${service['pricing']['price_per_call']}/call")
    print(f"    - Agent ID: {agent_a_id}")
    print()
    print(f"  Buyer (Agent B): agent-b-researcher")
    print(f"    - Discovered service via marketplace search")
    print(f"    - Made {len(test_texts)} calls through the payment proxy")
    print(f"    - Payment handled automatically by ACF")
    print()
    print("  Key takeaways:")
    print("    1. Agent B never knew Agent A's real endpoint")
    print("    2. First 5 calls were FREE (free tier), calls 6-7 were PAID")
    print("    3. Payment was automatic -- no wallet SDK needed on buyer side")
    print("    4. Usage was metered and billed per-call ($0.05/call)")
    print("    5. Reputation was computed from real performance data")
    print("    6. Free-to-paid transition was seamless")
    print()
    print("Next steps:")
    print("  - python examples/multi_agent_trade.py   (3-agent circular economy)")
    print("  - python examples/team_setup.py          (organize agents into teams)")
    print("  - python examples/webhook_listener.py    (real-time event notifications)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print(f"Error: Cannot connect to {BASE_URL}")
        print("Start the server first:")
        print("  uvicorn api.main:app --port 8000")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"HTTP Error: {e.response.status_code}")
        print(f"  {e.response.text}")
        sys.exit(1)
