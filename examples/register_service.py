"""Register a new service on AgenticTrade marketplace.

Usage:
    python register_service.py

Demonstrates: create provider API key → register service → test endpoint → verify listing.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sdk.client import ACFClient

BASE_URL = os.environ.get("ACF_BASE_URL", "http://localhost:8092")


def main() -> None:
    # 1. Create a provider API key
    client = ACFClient(base_url=BASE_URL)
    key_info = client.create_key(owner_id="my-company", role="provider")
    print(f"Provider key created: {key_info['key_id']}")
    print(f"Secret (save this!): {key_info['secret']}")

    # 2. Authenticate with the new key (format: key_id:secret)
    provider = ACFClient(
        base_url=BASE_URL,
        api_key=f"{key_info['key_id']}:{key_info['secret']}",
    )

    # 3. Register a service
    service = provider.register_service(
        name="My Weather API",
        description="Real-time weather data for any city worldwide.",
        endpoint="https://my-weather-api.example.com/api",
        price_per_call="0.05",
        free_tier_calls=10,
        category="data",
        tags=["weather", "api", "data"],
    )
    print(f"Service registered: {service['id']}")

    # 4. Test the endpoint
    test_result = provider.test_service(service["id"])
    print(f"Endpoint test: {'OK' if test_result.get('reachable') else 'FAILED'}")

    # 5. Check onboarding progress
    onboarding = provider.provider_onboarding()
    for step_key, step in onboarding.get("steps", {}).items():
        status = "✓" if step["completed"] else "○"
        print(f"  {status} {step['label']}")

    print(f"\nService is live at: {BASE_URL}/api/v1/services/{service['id']}")


if __name__ == "__main__":
    main()
