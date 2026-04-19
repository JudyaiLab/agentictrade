"""
Agent Commerce Framework -- Quickstart Example

Your first agent marketplace transaction in 5 minutes.

This script demonstrates the complete lifecycle:
  1. Create API keys (provider + buyer)
  2. Register a provider agent identity
  3. List a service on the marketplace
  4. Buyer discovers available services
  5. Buyer calls the service through the payment proxy
  6. Check usage statistics and reputation

Prerequisites:
    - ACF server running:  uvicorn api.main:app --port 8092
    - httpx installed:     pip install httpx

Run:
    python examples/quickstart.py

Environment:
    ACF_BASE_URL  -- Server URL (default: http://localhost:8092)
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
    """Create an API key and return credentials dict.

    Provider and admin keys require ``auth_headers`` from an existing key.

    Returns:
        {
            "key_id": str,
            "secret": str,
            "auth": {"Authorization": "Bearer key_id:secret"},
        }
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


def print_step(step: int, total: int, msg: str) -> None:
    """Print a numbered step."""
    print(f"[{step}/{total}] {msg}")


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def main() -> None:
    client = httpx.Client(timeout=30)
    total_steps = 9

    # -- Step 1: Create bootstrap key, then provider key ---------------------
    print_step(1, total_steps, "Creating provider API key...")
    # Provider keys require auth, so create a buyer key first to bootstrap
    bootstrap = create_key(client, owner_id="bootstrap-quickstart", role="buyer")
    provider = create_key(
        client,
        owner_id="provider-quickstart",
        role="provider",
        auth_headers=bootstrap["auth"],
    )
    print(f"       Key ID: {provider['key_id']}")

    # -- Step 2: Register provider agent identity --------------------------
    print_step(2, total_steps, "Registering provider agent identity...")
    resp = client.post(f"{API}/agents", json={
        "display_name": "Summarizer Bot",
        "capabilities": ["nlp", "text-generation", "summarization"],
        "metadata": {"version": "1.0"},
    }, headers=provider["auth"])
    resp.raise_for_status()
    provider_agent = resp.json()
    print(f"       Agent ID: {provider_agent['agent_id']}")
    print(f"       Capabilities: {provider_agent['capabilities']}")

    # -- Step 3: List a service on the marketplace -------------------------
    print_step(3, total_steps, "Listing service on the marketplace...")
    resp = client.post(f"{API}/services", json={
        "name": "Text Summarizer API",
        "description": "Condense any document into a concise summary. "
                       "Supports plain text, markdown, and HTML input.",
        "endpoint": "https://api.example.com/summarize",
        "price_per_call": "0.03",
        "currency": "USDC",
        "category": "ai",
        "tags": ["nlp", "summarization", "text"],
        "payment_method": "nowpayments",
        "free_tier_calls": 10,
    }, headers=provider["auth"])
    resp.raise_for_status()
    service = resp.json()
    service_id = service["id"]
    print(f"       Service: {service['name']}")
    print(f"       Price:   ${service['pricing']['price_per_call']}/call ({service['pricing']['currency']})")
    print(f"       Free:    {service['pricing']['free_tier_calls']} calls included")
    print(f"       ID:      {service_id}")

    # -- Step 4: Buyer API key ---------------------------------------------
    print_step(4, total_steps, "Creating buyer API key...")
    buyer = create_key(client, owner_id="buyer-quickstart", role="buyer")
    print(f"       Key ID: {buyer['key_id']}")

    # -- Step 5: Fund buyer account ----------------------------------------
    print_step(5, total_steps, "Funding buyer account (pre-paid balance)...")

    # Check initial balance
    resp = client.get(f"{API}/balance/buyer-quickstart")
    resp.raise_for_status()
    balance = resp.json()
    print(f"       Initial balance: ${balance['balance']}")

    # Create a deposit (in production, this returns a checkout URL)
    resp = client.post(f"{API}/deposits", json={
        "amount": 5.0,
        "buyer_id": "buyer-quickstart",
    })
    resp.raise_for_status()
    deposit = resp.json()
    print(f"       Deposit ID: {deposit['deposit_id']}")
    print(f"       Status:     {deposit['status']}")
    if deposit.get("checkout_url"):
        print(f"       Pay at:     {deposit['checkout_url']}")
    else:
        print(f"       (Manual deposit mode — no payment gateway)")

    # For demo: use admin credit to simulate confirmed payment
    admin_key = os.getenv("ACF_ADMIN_SECRET", "test-admin-secret")
    resp = client.post(f"{API}/admin/credit", params={
        "buyer_id": "buyer-quickstart",
        "amount": 5.0,
        "admin_key": admin_key,
    })
    resp.raise_for_status()
    credited = resp.json()
    print(f"       Credited:   ${credited['credited']} → Balance: ${credited['new_balance']}")

    # -- Step 6: Discover services -----------------------------------------
    print_step(6, total_steps, "Discovering services on the marketplace...")
    resp = client.get(f"{API}/discover", params={
        "category": "ai",
        "has_free_tier": "true",
        "sort_by": "price",
    })
    resp.raise_for_status()
    found = resp.json()
    print(f"       Found {found['total']} service(s) in 'ai' category with free tier:")
    for svc in found["services"][:5]:
        print(f"         - {svc['name']}  ${svc['pricing']['price_per_call']}/call")

    # -- Step 7: Call the service through the proxy ------------------------
    print_step(7, total_steps, "Calling service through the marketplace proxy...")
    resp = client.post(
        f"{API}/proxy/{service_id}/summarize",
        headers=buyer["auth"],
        json={
            "text": (
                "Agent Commerce Framework enables AI agents to discover, "
                "trade, and pay for each other's services autonomously. "
                "It includes identity verification, reputation scoring, "
                "multi-rail payments, and team management."
            ),
            "max_length": 50,
        },
    )
    # Note: the provider endpoint is a placeholder in this demo.
    # In production, this returns the real provider response.
    print(f"       HTTP {resp.status_code}")

    # Display billing headers
    amount = resp.headers.get("X-ACF-Amount", "N/A")
    free_tier = resp.headers.get("X-ACF-Free-Tier", "false")
    latency = resp.headers.get("X-ACF-Latency-Ms", "N/A")
    usage_id = resp.headers.get("X-ACF-Usage-Id", "N/A")

    if free_tier == "true":
        print(f"       Billing: FREE (within free tier)")
    else:
        print(f"       Billing: ${amount} USDC")
    print(f"       Latency: {latency}ms")
    print(f"       Usage ID: {usage_id}")

    # -- Step 8: Check usage and balance -----------------------------------
    print_step(8, total_steps, "Checking buyer usage and balance...")
    resp = client.get(f"{API}/usage/me", headers=buyer["auth"])
    resp.raise_for_status()
    usage = resp.json()
    print(f"       Total calls:  {usage['total_calls']}")
    print(f"       Total spent:  ${usage['total_spent_usd']}")
    print(f"       Avg latency:  {usage['avg_latency_ms']}ms")

    resp = client.get(f"{API}/balance/buyer-quickstart")
    resp.raise_for_status()
    balance = resp.json()
    print(f"       Balance:      ${balance['balance']} (deposited: ${balance['total_deposited']}, spent: ${balance['total_spent']})")

    # -- Step 9: Check reputation ------------------------------------------
    print_step(9, total_steps, "Checking provider reputation...")
    resp = client.get(
        f"{API}/agents/{provider_agent['agent_id']}/reputation",
        params={"compute": "true"},
    )
    if resp.status_code == 200:
        rep = resp.json()
        if "overall_score" in rep:
            print(f"       Overall score: {rep['overall_score']}")
            print(f"       Latency:      {rep['latency_score']}")
            print(f"       Reliability:  {rep['reliability_score']}")
            print(f"       Quality:      {rep['response_quality']}")
            print(f"       Call count:   {rep['call_count']}")
        else:
            print("       No reputation data yet (need more usage)")
    else:
        print("       Reputation not available yet")

    # -- Summary -----------------------------------------------------------
    print()
    print("--- Quickstart Complete ---")
    print(f"  Server:       {BASE_URL}")
    print(f"  Provider:     {provider_agent['agent_id']} ({provider_agent['display_name']})")
    print(f"  Service:      {service_id} ({service['name']})")
    print(f"  Buyer:        buyer-quickstart")
    print()
    print("Payment flow:")
    print("  1. Buyer deposits USDC via POST /api/v1/deposits")
    print("  2. Pay at checkout URL (NOWPayments / Stripe)")
    print("  3. IPN callback confirms payment → balance credited")
    print("  4. Buyer calls APIs → balance deducted per call")
    print()
    print("Next steps:")
    print("  - Run: python examples/two_agents_trading.py  (two-agent trade)")
    print("  - Run: python examples/multi_agent_trade.py   (circular economy)")
    print("  - Read: docs/API_REFERENCE.md                 (full API docs)")


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
