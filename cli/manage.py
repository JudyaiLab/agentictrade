#!/usr/bin/env python3
"""
Agent Commerce Framework — CLI Management Tool

Usage:
    python -m cli.manage register --name "My API" --endpoint https://... --price 0.01
    python -m cli.manage list
    python -m cli.manage stats
    python -m cli.manage seed-coinsifter
    python -m cli.manage schema > openapi.json
    python -m cli.manage info
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from marketplace.db import Database
from marketplace.registry import ServiceRegistry, RegistryError


def get_db() -> Database:
    return Database(os.environ.get("DATABASE_PATH"))


def cmd_register(args):
    """Register a new service."""
    db = get_db()
    registry = ServiceRegistry(db)

    try:
        service = registry.register(
            provider_id=args.provider or "cli-user",
            name=args.name,
            description=args.description or "",
            endpoint=args.endpoint,
            price_per_call=args.price,
            category=args.category or "",
            tags=args.tags.split(",") if args.tags else [],
            payment_method=args.payment or "x402",
        )
        print(f"✅ Service registered: {service.id}")
        print(f"   Name: {service.name}")
        print(f"   Price: ${service.pricing.price_per_call}/call")
        print(f"   Endpoint: {service.endpoint}")
    except RegistryError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_list(args):
    """List all services."""
    db = get_db()
    registry = ServiceRegistry(db)
    services = registry.search(
        status=args.status or "active",
        query=args.query,
    )

    if not services:
        print("No services found.")
        return

    for s in services:
        status_icon = "🟢" if s.status == "active" else "⏸️"
        print(f"{status_icon} [{s.id[:8]}] {s.name}")
        print(f"   ${s.pricing.price_per_call}/call | {s.pricing.payment_method} | {s.category or 'uncategorized'}")
        print(f"   {s.endpoint}")
        print()


def cmd_stats(args):
    """Show marketplace statistics."""
    db = get_db()
    registry = ServiceRegistry(db)

    active = registry.search(status="active")
    paused = registry.search(status="paused")

    usage = db.get_usage_stats()

    print("📊 Marketplace Stats")
    print(f"   Active services: {len(active)}")
    print(f"   Paused services: {len(paused)}")
    print(f"   Total API calls: {usage['total_calls']}")
    print(f"   Total revenue: ${usage['total_revenue']}")
    print(f"   Avg latency: {usage['avg_latency_ms']}ms")


def cmd_seed_coinsifter(args):
    """Register CoinSifter as a seed service."""
    db = get_db()
    registry = ServiceRegistry(db)

    coinsifter_url = args.url or os.environ.get(
        "COINSIFTER_API_URL", "https://coinsifter-api.example.com"
    )

    try:
        service = registry.register(
            provider_id=os.environ.get("SEED_PROVIDER_ID", "demo-provider"),
            name="CoinSifter Crypto Scanner API",
            description=(
                "Real-time crypto market scanning, technical analysis signals, "
                "and per-coin reports. Covers 100+ USDT pairs on Binance."
            ),
            endpoint=coinsifter_url,
            price_per_call="0.01",
            category="data",
            tags=["crypto", "trading", "technical-analysis", "market-data"],
            payment_method="x402",
        )
        print(f"✅ CoinSifter API registered: {service.id}")
        print(f"   Endpoint: {coinsifter_url}")
    except RegistryError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_schema(args):
    """Export the OpenAPI JSON schema to stdout."""
    from api.main import app

    schema = app.openapi()
    print(json.dumps(schema, indent=2))


def cmd_info(args):
    """Print project info summary."""
    from api.main import app

    schema = app.openapi()
    version = schema.get("info", {}).get("version", "unknown")

    routes = [r for r in app.routes if hasattr(r, "methods")]
    route_count = len(routes)

    providers = []
    for name in ("x402_provider", "nowpayments_provider", "paypal_provider"):
        try:
            __import__(f"payments.{name}")
            providers.append(name.replace("_provider", "").replace("_", "-"))
        except ImportError:
            pass

    db_path = os.environ.get("DATABASE_PATH", "acf.db (default)")

    print(f"Agent Commerce Framework v{version}")
    print(f"  Routes:             {route_count}")
    print(f"  Payment providers:  {', '.join(providers) if providers else 'none'}")
    print(f"  Database:           {db_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Agent Commerce Framework CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # register
    reg = subparsers.add_parser("register", help="Register a service")
    reg.add_argument("--name", required=True)
    reg.add_argument("--endpoint", required=True)
    reg.add_argument("--price", required=True)
    reg.add_argument("--provider", default="cli-user")
    reg.add_argument("--description", default="")
    reg.add_argument("--category", default="")
    reg.add_argument("--tags", default="")
    reg.add_argument("--payment", default="x402")

    # list
    lst = subparsers.add_parser("list", help="List services")
    lst.add_argument("--status", default="active")
    lst.add_argument("--query", default=None)

    # stats
    subparsers.add_parser("stats", help="Show stats")

    # seed
    seed = subparsers.add_parser("seed-coinsifter", help="Seed CoinSifter API")
    seed.add_argument("--url", default=None)

    # schema
    subparsers.add_parser("schema", help="Export OpenAPI JSON schema to stdout")

    # info
    subparsers.add_parser("info", help="Print project info")

    args = parser.parse_args()

    commands = {
        "register": cmd_register,
        "list": cmd_list,
        "stats": cmd_stats,
        "seed-coinsifter": cmd_seed_coinsifter,
        "schema": cmd_schema,
        "info": cmd_info,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
