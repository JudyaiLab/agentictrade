"""Check provider analytics and earnings on AgenticTrade.

Usage:
    ACF_PROVIDER_KEY=key_id:secret python check_analytics.py

Demonstrates: provider dashboard → service analytics → earnings summary.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sdk.client import ACFClient

BASE_URL = os.environ.get("ACF_BASE_URL", "http://localhost:8092")
API_KEY = os.environ.get("ACF_PROVIDER_KEY", "")


def main() -> None:
    if not API_KEY:
        print("Set ACF_PROVIDER_KEY environment variable (format: key_id:secret).")
        print("Example: ACF_PROVIDER_KEY=acf_xxx:yyy python check_analytics.py")
        sys.exit(1)

    provider = ACFClient(
        base_url=BASE_URL,
        api_key=API_KEY,
    )

    # 1. Provider dashboard overview
    dashboard = provider.provider_dashboard()
    print("=== Provider Dashboard ===")
    print(f"  Services: {dashboard.get('total_services', 0)}")
    print(f"  Total calls: {dashboard.get('total_calls', 0)}")
    print(f"  Revenue: ${dashboard.get('total_revenue', 0):.2f}")

    # 2. List services with stats
    services = provider.provider_services()
    print(f"\n=== My Services ({len(services)}) ===")
    for svc in services:
        print(f"  {svc['name']} — {svc.get('total_calls', 0)} calls, ${svc.get('total_revenue', 0):.2f}")

    # 3. Earnings summary
    earnings = provider.provider_earnings()
    print("\n=== Earnings ===")
    print(f"  Total earned: ${earnings.get('total_earned', 0):.2f}")
    print(f"  Pending settlement: ${earnings.get('pending_settlement', 0):.2f}")
    print(f"  Settled: ${earnings.get('total_settled', 0):.2f}")

    # 4. Milestones
    milestones = provider.provider_milestones()
    milestone_list = milestones.get("milestones", [])
    if milestone_list:
        print("\n=== Milestones ===")
        for ms in milestone_list:
            status = "🏆" if ms.get("achieved") else "○"
            print(f"  {status} {ms['name']} — {ms.get('description', '')}")


if __name__ == "__main__":
    main()
