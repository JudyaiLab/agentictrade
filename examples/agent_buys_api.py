#!/usr/bin/env python3
"""
End-to-End Demo: Agent Buys an API Service

Demonstrates the complete flow:
1. Register as a buyer agent on the marketplace
2. Discover available services
3. Call a paid API (x402 payment handled automatically)
4. Check payment history and balance

Prerequisites:
  - ACF server running: cd agent-commerce-framework && uvicorn api.main:app
  - CDP API keys for wallet: export CDP_API_KEY_ID=... CDP_API_KEY_SECRET=...
  - (Optional) For testnet USDC: use faucet at https://faucet.circle.com

Usage:
  python examples/agent_buys_api.py
"""
from __future__ import annotations

import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from sdk.buyer import BuyerAgent, BuyerAgentError
    from sdk.client import ACFClient

    marketplace_url = os.environ.get("ACF_URL", "http://localhost:8000")

    # ── Step 1: Register buyer agent ──
    print("=== Step 1: Register Buyer Agent ===")
    sync_client = ACFClient(base_url=marketplace_url)

    try:
        key_data = sync_client.create_key(owner_id="demo-buyer", role="buyer")
        api_key = f"{key_data['key_id']}:{key_data['secret']}"
        print(f"API key created: {key_data['key_id']}")
    except Exception as e:
        print(f"Key creation failed (may already exist): {e}")
        api_key = None

    # ── Step 2: Initialize BuyerAgent with wallet ──
    print("\n=== Step 2: Initialize Buyer Agent ===")
    cdp_key_id = os.environ.get("CDP_API_KEY_ID")
    cdp_key_secret = os.environ.get("CDP_API_KEY_SECRET")

    if not cdp_key_id or not cdp_key_secret:
        print("CDP_API_KEY_ID and CDP_API_KEY_SECRET not set.")
        print("Running in simulation mode (no real payments).")

    async with BuyerAgent(
        marketplace_url=marketplace_url,
        api_key=api_key,
        cdp_api_key_id=cdp_key_id,
        cdp_api_key_secret=cdp_key_secret,
        wallet_name="demo-buyer-wallet",
        network="base-sepolia",
    ) as buyer:
        if buyer.wallet_address:
            print(f"Wallet address: {buyer.wallet_address}")
            balance = await buyer.get_balance()
            print(f"USDC balance: {balance}")
        else:
            print("No wallet configured — x402 payments will be simulated")

        # ── Step 3: Discover services ──
        print("\n=== Step 3: Discover Services ===")
        try:
            services = await buyer.discover_services(max_price="0.10")
            if services:
                for svc in services[:5]:
                    print(f"  [{svc.get('id', '?')}] {svc.get('name', '?')} "
                          f"— ${svc.get('price_per_call', '?')}/call")
            else:
                print("  No services found (marketplace may be empty)")
        except Exception as e:
            print(f"  Discovery failed: {e}")

        # ── Step 4: Call a paid service ──
        print("\n=== Step 4: Call Paid Service ===")
        service_id = os.environ.get("DEMO_SERVICE_ID")

        if not service_id:
            print("  Set DEMO_SERVICE_ID to call a specific service.")
            print("  Skipping paid call demo.")
        else:
            try:
                result = await buyer.call_service(
                    service_id=service_id,
                    method="GET",
                    path="/",
                )
                print(f"  Response: {result}")
            except BuyerAgentError as e:
                print(f"  Payment/call error: {e}")
            except Exception as e:
                print(f"  Unexpected error: {e}")

        # ── Step 5: Payment history ──
        print("\n=== Step 5: Payment History ===")
        for record in buyer.payment_history:
            print(f"  {record.service_id}: {record.amount} {record.currency} "
                  f"(tx: {record.tx_hash or 'pending'})")

        if not buyer.payment_history:
            print("  No payments made this session.")

    sync_client.close()
    print("\nDemo complete.")


if __name__ == "__main__":
    asyncio.run(main())
