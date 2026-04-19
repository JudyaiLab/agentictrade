"""
Agent Commerce Framework — Payment & Settlement Example

Demonstrates the full payment lifecycle:
  1. Provider lists a paid service
  2. Buyer makes several proxy calls (free tier + paid)
  3. Admin creates a settlement for the provider
  4. Admin marks the settlement as paid

This example uses the built-in mock payment provider that ships
with ACF when no external payment keys are configured.

Run:
    python examples/payment_flow.py

Requires:
    - ACF server running (uvicorn api.main:app --port 8092)
    - pip install httpx
"""
import os
import sys

import httpx

BASE_URL = os.getenv("ACF_BASE_URL", "http://localhost:8092")
API = f"{BASE_URL}/api/v1"


def create_key(client: httpx.Client, owner_id: str, role: str, auth_headers: dict | None = None) -> dict:
    """Create an API key and return credentials dict."""
    headers = dict(auth_headers) if auth_headers else {}
    headers["Content-Type"] = "application/json"
    resp = client.post(f"{API}/keys", json={"owner_id": owner_id, "role": role}, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return {
        "key_id": data["key_id"],
        "secret": data["secret"],
        "auth": {"Authorization": f"Bearer {data['key_id']}:{data['secret']}"},
    }


def main():
    client = httpx.Client(timeout=30)

    # ── Setup: provider + service ───────────────────────────────
    print("=== Setup ===")
    bootstrap = create_key(client, "bootstrap-pay-demo", "buyer")
    provider = create_key(client, "provider-pay-demo", "provider", auth_headers=bootstrap["auth"])
    print(f"Provider key: {provider['key_id']}")

    resp = client.post(f"{API}/services", json={
        "name": "Image Classifier",
        "description": "Classify images into 1000 categories",
        "endpoint": "https://httpbin.org",
        "price_per_call": "0.10",
        "category": "ai",
        "tags": ["vision", "classification"],
        "free_tier_calls": 2,  # only 2 free calls for demo
    }, headers=provider["auth"])
    resp.raise_for_status()
    service_id = resp.json()["id"]
    print(f"Service listed: {service_id} ($0.10/call, 2 free)")

    # ── Buyer setup + fund balance ───────────────────────────────
    print("\n=== Buyer Setup ===")
    buyer = create_key(client, "buyer-pay-demo", "buyer")

    # Fund the buyer account (admin credit for demo)
    admin_key = os.getenv("ACF_ADMIN_SECRET", "test-admin-secret")
    resp = client.post(f"{API}/admin/credit", params={
        "buyer_id": "buyer-pay-demo",
        "amount": 1.0,
    }, headers={"x-admin-key": admin_key})
    resp.raise_for_status()
    print(f"  Funded buyer with ${resp.json()['credited']} USDC")
    print(f"  Balance: ${resp.json()['new_balance']}")

    # ── Buyer calls the service ─────────────────────────────────
    print("\n=== Buyer Calls ===")

    proxy_client = httpx.Client(timeout=10)
    for i in range(1, 5):
        try:
            resp = proxy_client.post(
                f"{API}/proxy/{service_id}/post",
                headers=buyer["auth"],
                json={"image_url": f"https://example.com/img_{i}.jpg"},
            )
            # Extract billing info from response headers
            amount = resp.headers.get("X-ACF-Amount", "?")
            free = resp.headers.get("X-ACF-Free-Tier", "false")
            latency = resp.headers.get("X-ACF-Latency-Ms", "?")
            tier = "FREE" if free == "true" else "PAID"
            print(f"  Call {i}: HTTP {resp.status_code}  "
                  f"${amount} ({tier})  {latency}ms")
        except httpx.ReadTimeout:
            print(f"  Call {i}: Timeout (provider endpoint is a placeholder)")

    # ── Check buyer usage ───────────────────────────────────────
    print("\n=== Usage Summary ===")
    resp = client.get(f"{API}/usage/me", headers=buyer["auth"])
    if resp.status_code == 200:
        usage = resp.json()
        print(f"  Total calls:  {usage.get('total_calls', 0)}")
        print(f"  Total spent:  ${usage.get('total_spent_usd', '0.00')}")
        print(f"  Avg latency:  {usage.get('avg_latency_ms', 0)}ms")

    # ── Admin settles provider earnings ─────────────────────────
    print("\n=== Settlement ===")
    admin = create_key(client, "admin-pay-demo", "admin", auth_headers=bootstrap["auth"])

    # Create a settlement for the provider's earnings
    resp = client.post(f"{API}/settlements", json={
        "provider_id": "provider-pay-demo",
        "period_start": "2026-03-01T00:00:00Z",
        "period_end": "2026-03-31T23:59:59Z",
    }, headers=admin["auth"])

    if resp.status_code == 201:
        settlement = resp.json()
        print(f"  Settlement ID:  {settlement['id']}")
        print(f"  Gross amount:   ${settlement['total_amount']}")
        print(f"  Platform fee:   ${settlement['platform_fee']} (10%)")
        print(f"  Net payout:     ${settlement['net_amount']}")
        print(f"  Status:         {settlement['status']}")

        # Mark settlement as paid (simulates on-chain transfer)
        resp = client.patch(
            f"{API}/settlements/{settlement['id']}/pay",
            json={"payment_tx": "0xdemo_tx_hash_abc123"},
            headers=admin["auth"],
        )
        if resp.status_code == 200:
            print(f"  → Marked PAID (tx: 0xdemo_tx_hash_abc123)")
    else:
        print(f"  Settlement skipped (HTTP {resp.status_code})")
        print(f"  {resp.text}")

    # ── Verify settlement list ──────────────────────────────────
    print("\n=== Provider View ===")
    resp = client.get(f"{API}/settlements", headers=provider["auth"])
    if resp.status_code == 200:
        data = resp.json()
        for s in data.get("settlements", []):
            print(f"  [{s['status'].upper()}] ${s['net_amount']} "
                  f"({s['period_start'][:10]} ~ {s['period_end'][:10]})")

    print("\n✓ Payment flow complete!")


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print(f"Error: Cannot connect to {BASE_URL}")
        print("Start the server first:  uvicorn api.main:app --port 8092")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"HTTP Error: {e.response.status_code} — {e.response.text}")
        sys.exit(1)
