"""Consume a service through the AgenticTrade marketplace proxy.

Usage:
    python consume_service.py

Demonstrates: create buyer key → browse services → call service via proxy.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sdk.client import ACFClient

BASE_URL = os.environ.get("ACF_BASE_URL", "http://localhost:8092")


def main() -> None:
    # 1. Create a buyer API key
    client = ACFClient(base_url=BASE_URL)
    key_info = client.create_key(owner_id="buyer-demo", role="buyer")
    print(f"Buyer key: {key_info['key_id']}")

    # 2. Authenticate (format: key_id:secret)
    buyer = ACFClient(
        base_url=BASE_URL,
        api_key=f"{key_info['key_id']}:{key_info['secret']}",
    )

    # 3. Browse available services
    result = buyer.search()
    services = result.get("services", [])
    print(f"\nAvailable services ({len(services)}):")
    for svc in services:
        pricing = svc["pricing"]
        print(f"  {svc['name']} — ${pricing['price_per_call']}/call, {pricing['free_tier_calls']} free")

    # 4. Call a service through the proxy (pick the first available)
    if services:
        target = services[0]
        print(f"\nCalling {target['name']} via proxy...")
        try:
            result = buyer.call_service(target["id"], method="GET", path="/")
            print(f"  Response: {result}")
        except Exception as e:
            print(f"  Error: {e}")
    else:
        print("\nNo services available to call.")


if __name__ == "__main__":
    main()
