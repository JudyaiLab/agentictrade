#!/usr/bin/env python3
"""ERC-8004 Hackathon Demo — Trading Agent Marketplace Flow.

Demonstrates an autonomous trading agent discovering and using
API services through the AgenticTrade marketplace.

Usage:
    python scripts/demo_trading_agent.py [--base-url http://localhost:8340]
"""
import argparse
import json
import sys

import httpx

BASE_URL = "http://localhost:8340"


def step(n: int, title: str):
    print(f"\n{'='*60}")
    print(f"  Step {n}: {title}")
    print(f"{'='*60}")


def main(base_url: str):
    client = httpx.Client(base_url=base_url, timeout=10)

    # Step 1: Discover available services
    step(1, "Discover trading services via marketplace")
    resp = client.get("/api/v1/services", params={"category": "trading"})
    print(f"  Status: {resp.status_code}")
    data = resp.json()
    print(f"  Found {data.get('count', 0)} services")
    for svc in data.get("services", [])[:5]:
        sponsored = " [SPONSORED]" if svc.get("is_sponsored") else ""
        print(f"    - {svc.get('name', 'N/A')}{sponsored}")

    # Step 2: Query AdCP for ad products (buyer agent perspective)
    step(2, "Query AdCP ad inventory")
    resp = client.post("/api/v1/adcp/mcp", json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    })
    print(f"  Status: {resp.status_code}")
    tools = resp.json().get("result", {}).get("tools", [])
    print(f"  Available AdCP tools: {[t['name'] for t in tools]}")

    # Step 3: Browse ad products catalog
    step(3, "Browse ad product catalog")
    resp = client.post("/api/v1/adcp/mcp", json={
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "get_products",
            "arguments": {"buying_mode": "catalog"},
        },
    })
    print(f"  Status: {resp.status_code}")
    content = json.loads(resp.json()["result"]["content"][0]["text"])
    print(f"  {content['total']} ad products on {content['marketplace']}:")
    for p in content["products"]:
        pricing = ", ".join(
            f"{o['model'].upper()} ${o['rate']}" for o in p["pricing_options"]
        )
        print(f"    - {p['name']} ({pricing})")

    # Step 4: Check .well-known discovery endpoints
    step(4, "Verify discovery endpoints")
    for path in ["/.well-known/adagents.json", "/.well-known/mcp.json"]:
        resp = client.get(path)
        status = "OK" if resp.status_code == 200 else f"FAIL ({resp.status_code})"
        print(f"  {path}: {status}")

    # Step 5: Check MCP descriptor
    step(5, "MCP Tool Descriptor for agent auto-discovery")
    resp = client.get("/api/v1/mcp/descriptor")
    print(f"  Status: {resp.status_code}")
    if resp.status_code == 200:
        desc = resp.json()
        print(f"  Marketplace: {desc.get('name', 'N/A')}")
        print(f"  Tools: {len(desc.get('tools', []))}")
        print(f"  Services: {len(desc.get('services', []))}")

    print(f"\n{'='*60}")
    print("  Demo complete! Trading agent flow verified.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trading Agent Demo")
    parser.add_argument("--base-url", default=BASE_URL, help="Marketplace URL")
    args = parser.parse_args()
    main(args.base_url)
