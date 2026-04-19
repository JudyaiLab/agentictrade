"""
MCP Bridge — Exposes marketplace capabilities as MCP tools.
Requires: pip install mcp>=1.0
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from marketplace.db import Database
from marketplace.registry import ServiceRegistry

logger = logging.getLogger("mcp_bridge")

try:
    from mcp.server import Server
    from mcp.types import Tool, TextContent
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    logger.info("MCP SDK not installed. MCP bridge disabled.")


def _serialize(obj: Any) -> Any:
    """JSON-safe serialization for marketplace objects."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def _json_response(data: Any) -> str:
    """Convert data to a JSON string, handling Decimal and datetime."""
    return json.dumps(_serialize(data), indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool definitions (shared by real server and stub)
# ---------------------------------------------------------------------------

_READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "openWorldHint": False,
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "marketplace_search",
        "description": (
            "Search marketplace services by query string, category, or tags. "
            "Returns a list of matching active service listings."
        ),
        "annotations": _READ_ONLY_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-text search term for service name or description.",
                },
                "category": {
                    "type": "string",
                    "description": "Filter by service category.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by one or more tags.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default 20, max 100).",
                    "default": 20,
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default 0).",
                    "default": 0,
                },
            },
        },
    },
    {
        "name": "marketplace_get_service",
        "description": "Get full details of a single marketplace service by its ID.",
        "annotations": _READ_ONLY_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "service_id": {
                    "type": "string",
                    "description": "The unique service ID.",
                },
            },
            "required": ["service_id"],
        },
    },
    {
        "name": "marketplace_list_categories",
        "description": (
            "List all service categories with the number of active services in each."
        ),
        "annotations": _READ_ONLY_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "marketplace_get_agent",
        "description": "Get an agent identity by agent ID.",
        "annotations": _READ_ONLY_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The unique agent ID.",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "marketplace_get_reputation",
        "description": (
            "Get reputation records for an agent or service. "
            "Provide agent_id for agent reputation, service_id for service reputation."
        ),
        "annotations": _READ_ONLY_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID to look up reputation for.",
                },
                "service_id": {
                    "type": "string",
                    "description": "Service ID to look up reputation for.",
                },
                "period": {
                    "type": "string",
                    "description": "Period filter, e.g. '2026-03' or 'all-time'.",
                    "default": "all-time",
                },
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Real MCP server (only when mcp SDK is available)
# ---------------------------------------------------------------------------

if MCP_AVAILABLE:

    class MarketplaceMCPServer:
        """Wraps marketplace DB + registry and exposes MCP tools."""

        def __init__(self, db: Database, registry: ServiceRegistry) -> None:
            self.db = db
            self.registry = registry
            self.server = Server("agent-commerce-marketplace")
            self._register_tools()

        # -- public helpers --------------------------------------------------

        def get_tools(self) -> list[Tool]:
            """Return the list of Tool objects exposed by this server."""
            return [
                Tool(
                    name=td["name"],
                    description=td["description"],
                    inputSchema=td["inputSchema"],
                )
                for td in TOOL_DEFINITIONS
            ]

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> list[TextContent]:
            """Dispatch a tool call and return TextContent results."""
            handler = self._handlers.get(name)
            if handler is None:
                return [TextContent(
                    type="text",
                    text=_json_response({"error": f"Unknown tool: {name}"}),
                )]
            result = handler(arguments)
            return [TextContent(type="text", text=_json_response(result))]

        # -- internal --------------------------------------------------------

        def _register_tools(self) -> None:
            self._handlers: dict[str, Any] = {
                "marketplace_search": self._handle_search,
                "marketplace_get_service": self._handle_get_service,
                "marketplace_list_categories": self._handle_list_categories,
                "marketplace_get_agent": self._handle_get_agent,
                "marketplace_get_reputation": self._handle_get_reputation,
            }

        def _handle_search(self, args: dict[str, Any]) -> dict:
            query = args.get("query")
            category = args.get("category")
            tags = args.get("tags")
            limit = min(int(args.get("limit", 20)), 100)
            offset = max(int(args.get("offset", 0)), 0)

            services = self.registry.search(
                query=query,
                category=category,
                status="active",
                limit=limit,
                offset=offset,
            )

            # Apply tag filter client-side (registry.search doesn't support tags)
            if tags:
                tag_set = {t.lower() for t in tags}
                services = [
                    s for s in services
                    if tag_set.intersection(t.lower() for t in s.tags)
                ]

            return {
                "services": [_serialize(s) for s in services],
                "total": len(services),
            }

        def _handle_get_service(self, args: dict[str, Any]) -> dict:
            service_id = args.get("service_id", "")
            service = self.registry.get(service_id)
            if service is None:
                return {"error": "Service not found", "service_id": service_id}
            return {"service": _serialize(service)}

        def _handle_list_categories(self, args: dict[str, Any]) -> dict:
            all_services = self.registry.search(status="active", limit=1000)
            category_counts: dict[str, int] = {}
            for s in all_services:
                cat = s.category or "uncategorized"
                category_counts[cat] = category_counts.get(cat, 0) + 1

            categories = sorted(
                [{"category": k, "count": v} for k, v in category_counts.items()],
                key=lambda x: x["count"],
                reverse=True,
            )
            return {"categories": categories}

        def _handle_get_agent(self, args: dict[str, Any]) -> dict:
            agent_id = args.get("agent_id", "")
            agent = self.db.get_agent(agent_id)
            if agent is None:
                return {"error": "Agent not found", "agent_id": agent_id}
            return {"agent": _serialize(agent)}

        def _handle_get_reputation(self, args: dict[str, Any]) -> dict:
            agent_id = args.get("agent_id")
            service_id = args.get("service_id")
            period = args.get("period", "all-time")

            if agent_id:
                records = self.db.get_reputation(agent_id, period)
                return {"agent_id": agent_id, "period": period, "records": records}

            if service_id:
                records = self.db.get_service_reputation(service_id, period)
                return {"service_id": service_id, "period": period, "records": records}

            return {"error": "Provide agent_id or service_id"}

    def create_mcp_server(db: Database, registry: ServiceRegistry) -> MarketplaceMCPServer:
        """Factory function to create a MarketplaceMCPServer."""
        return MarketplaceMCPServer(db, registry)

else:
    # Stub when MCP SDK is not installed

    class MarketplaceMCPServer:  # type: ignore[no-redef]
        """Stub — MCP SDK not installed."""

        def __init__(self, db: Database, registry: ServiceRegistry) -> None:
            raise RuntimeError("MCP SDK not installed")

        def get_tools(self) -> list:
            raise RuntimeError("MCP SDK not installed")

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> list:
            raise RuntimeError("MCP SDK not installed")

    def create_mcp_server(db: Database, registry: ServiceRegistry) -> MarketplaceMCPServer:  # type: ignore[no-redef]
        """Factory stub — MCP SDK not installed."""
        raise RuntimeError("MCP SDK not installed")
