"""Generate MCP-compatible service discovery manifests."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from marketplace.registry import ServiceRegistry

logger = logging.getLogger("mcp_bridge.discovery")


def _safe_serialize(obj: Any) -> Any:
    """Convert Decimal/datetime/dataclass values to JSON-safe types."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _safe_serialize(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    return obj


class ManifestGenerator:
    """
    Generates an MCP-compatible service discovery manifest
    from all active services in a ServiceRegistry.

    Manifest schema:
    {
        "version": "1.0",
        "services": [
            {
                "id": "...",
                "name": "...",
                "description": "...",
                "pricing": { ... },
                "category": "...",
                "tags": [...],
                "endpoint_hint": "..."
            },
            ...
        ],
        "generated_at": "ISO-8601 timestamp"
    }
    """

    MANIFEST_VERSION = "1.0"

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry

    def generate(self) -> dict[str, Any]:
        """
        Generate the manifest dict from all active services.

        Returns a dict following the manifest schema.
        """
        services = self.registry.search(status="active", limit=1000)
        entries = []
        for svc in services:
            entries.append({
                "id": svc.id,
                "name": svc.name,
                "description": svc.description,
                "pricing": {
                    "price_per_call": str(svc.pricing.price_per_call),
                    "currency": svc.pricing.currency,
                    "payment_method": svc.pricing.payment_method,
                    "free_tier_calls": svc.pricing.free_tier_calls,
                },
                "category": svc.category,
                "tags": list(svc.tags),
                "endpoint_hint": svc.endpoint,
            })

        return {
            "version": self.MANIFEST_VERSION,
            "services": entries,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def to_json(self, indent: int = 2) -> str:
        """Generate the manifest and return it as a JSON string."""
        return json.dumps(self.generate(), indent=indent, ensure_ascii=False)

    def to_file(self, path: str | Path) -> None:
        """Generate the manifest and write it to a file."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")
        logger.info("Manifest written to %s", target)
