"""
Enhanced service discovery API routes.
Includes MCP Tool Descriptor endpoint for agent auto-discovery.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["discovery"])


def _empty_quality() -> dict:
    """Return a default quality dict when no health data is available."""
    return {
        "health_score": None,
        "uptime_pct": None,
        "avg_latency_ms": None,
        "sla_tier": "basic",
        "quality_tier": "Standard",
    }


def _build_quality_map(db, service_ids: list[str]) -> dict[str, dict]:
    """Build a mapping of service_id → quality signals.

    Queries health scores and SLA tiers in bulk for efficiency.
    Returns a dict keyed by service_id.
    """
    if not service_ids:
        return {}

    from marketplace.health_monitor import HealthMonitor

    monitor = HealthMonitor(db)
    quality_map: dict[str, dict] = {}

    # Fetch SLA tiers in bulk
    sla_tiers: dict[str, str] = {}
    try:
        with db.connect() as conn:
            placeholders = ",".join("?" for _ in service_ids)
            rows = conn.execute(
                f"SELECT service_id, sla_tier FROM service_sla "
                f"WHERE service_id IN ({placeholders})",
                service_ids,
            ).fetchall()
            sla_tiers = {row["service_id"]: row["sla_tier"] for row in rows}
    except Exception:
        pass  # Table may not exist yet

    for sid in service_ids:
        score = monitor.get_service_health_score(sid)
        if score:
            # Determine quality tier from health score
            if score.quality_score >= 95:
                quality_tier = "Premium"
            elif score.quality_score >= 80:
                quality_tier = "Verified"
            else:
                quality_tier = "Standard"

            quality_map[sid] = {
                "health_score": score.quality_score,
                "uptime_pct": score.uptime_pct,
                "avg_latency_ms": score.avg_latency_ms,
                "sla_tier": sla_tiers.get(sid, "basic"),
                "quality_tier": quality_tier,
            }
        else:
            quality_map[sid] = {
                **_empty_quality(),
                "sla_tier": sla_tiers.get(sid, "basic"),
            }

    return quality_map


@router.get("/mcp/descriptor")
async def mcp_descriptor(request: Request):
    """
    MCP Tool Descriptor — enables AI agents to auto-discover marketplace services.

    Returns a JSON descriptor conforming to MCP schema_version 1.0.
    Agents built on MCP-compatible frameworks can parse this descriptor
    to discover available tools, their parameters, and pricing.
    """
    registry = request.app.state.registry
    services = registry.search(status="active", limit=100)

    base_url = str(request.base_url).rstrip("/")

    # Build per-service tool descriptors
    service_tools = []
    for svc in services:
        tool_name = svc.name.lower().replace(" ", "_").replace("—", "").replace("-", "_")
        # Remove consecutive underscores
        while "__" in tool_name:
            tool_name = tool_name.replace("__", "_")
        tool_name = tool_name.strip("_")

        service_tools.append({
            "name": f"call_{tool_name}",
            "description": svc.description,
            "service_id": svc.id,
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "API path to call on the service (e.g. /api/scan).",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "DELETE"],
                        "description": "HTTP method.",
                        "default": "GET",
                    },
                    "body": {
                        "type": "object",
                        "description": "Request body (for POST/PUT).",
                    },
                },
            },
            "pricing": {
                "cost_usd": str(svc.pricing.price_per_call),
                "unit": "per_call",
                "free_tier_calls": svc.pricing.free_tier_calls,
                "payment_method": svc.pricing.payment_method,
            },
            "category": svc.category,
            "tags": svc.tags,
        })

    return {
        "schema_version": "1.0",
        "name": "agentictrade_marketplace",
        "description": (
            "AgenticTrade — AI Agent API marketplace. "
            "Discover, authenticate, and pay for APIs automatically. "
            "Proxy calls through the marketplace with built-in billing."
        ),
        "base_url": f"{base_url}/api/v1",
        "tools": [
            {
                "name": "marketplace_search",
                "description": "Search for services by query, category, or tags.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "q": {"type": "string", "description": "Search query."},
                        "category": {"type": "string", "description": "Category filter."},
                        "tags": {"type": "string", "description": "Comma-separated tags."},
                    },
                },
                "endpoint": "/discover",
                "method": "GET",
            },
            {
                "name": "marketplace_get_service",
                "description": "Get full details of a service by ID.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "service_id": {"type": "string", "description": "Service UUID."},
                    },
                    "required": ["service_id"],
                },
                "endpoint": "/services/{service_id}",
                "method": "GET",
            },
            {
                "name": "marketplace_proxy_call",
                "description": (
                    "Call a service through the payment proxy. "
                    "Requires Bearer authentication with key_id:secret."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "service_id": {"type": "string", "description": "Service UUID."},
                        "path": {"type": "string", "description": "API path on the service."},
                        "method": {
                            "type": "string",
                            "enum": ["GET", "POST"],
                            "description": "HTTP method.",
                        },
                    },
                    "required": ["service_id"],
                },
                "endpoint": "/proxy/{service_id}/{path}",
                "method": "dynamic",
            },
        ],
        "services": service_tools,
        "auth": {
            "type": "bearer",
            "format": "key_id:secret",
            "create_key_endpoint": "/keys",
            "hint": "Create a buyer key via POST /api/v1/keys, then use key_id:secret as Bearer token.",
        },
        "rate_limits": {
            "requests_per_minute": 60,
        },
    }


@router.get("/discover")
async def discover_services(
    request: Request,
    q: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[str] = None,
    min_price: Optional[str] = None,
    max_price: Optional[str] = None,
    payment_method: Optional[str] = None,
    has_free_tier: Optional[bool] = None,
    sort_by: str = "created_at",
    limit: int = 50,
    offset: int = 0,
):
    """
    Enhanced service discovery with filters.

    Query params:
    - q: text search
    - category: filter by category
    - tags: comma-separated tags
    - min_price/max_price: price range
    - payment_method: x402, stripe, or both
    - has_free_tier: true/false
    - sort_by: created_at, price, name
    """
    discovery = request.app.state.discovery
    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    # Clamp limit to prevent abuse
    limit = min(max(limit, 1), 100)

    try:
        result = discovery.search(
            query=q,
            category=category,
            tags=tag_list,
            min_price=min_price,
            max_price=max_price,
            payment_method=payment_method,
            has_free_tier=has_free_tier,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from .services import _service_response

    # Embed quality signals (SLA tier, health score, uptime)
    db = request.app.state.db
    quality_map = _build_quality_map(db, [s.id for s in result["services"]])

    services_out = []
    for s in result["services"]:
        svc = _service_response(s)
        svc["quality"] = quality_map.get(s.id, _empty_quality())
        services_out.append(svc)

    # AdCP: inject sponsored results at top of search results
    from marketplace.ad_serving import inject_into_service_list
    context = "api_search" if q else "category_browse" if category else "service_discovery"
    services_out = inject_into_service_list(
        services_out,
        context=context,
        category=category,
        max_ads=1,
        position=0,
    )

    return {
        "services": services_out,
        "total": result["total"],
        "offset": result["offset"],
        "limit": result["limit"],
    }


@router.get("/discover/categories")
async def list_categories(request: Request):
    """Get all service categories with counts."""
    discovery = request.app.state.discovery
    categories = discovery.get_categories()
    return {"categories": categories}


@router.get("/discover/trending")
async def trending_services(request: Request, limit: int = 10):
    """Get trending services by usage volume."""
    discovery = request.app.state.discovery

    from .services import _service_response
    trending = discovery.get_trending(limit=limit)
    trending_out = [
        {
            "service": _service_response(t["service"]),
            "call_count": t["call_count"],
            "avg_latency_ms": t["avg_latency_ms"],
        }
        for t in trending
    ]

    # AdCP: inject featured provider ads into trending
    from marketplace.ad_serving import get_sponsored_results
    sponsored = get_sponsored_results(
        campaigns={}, context="discovery_feed", limit=1,
    )
    for ad in sponsored:
        trending_out.insert(0, {
            "service": {
                "id": f"sponsored_{ad['campaign_id']}",
                "name": ad["creative_text"] or f"Sponsored: {ad['sponsored_by']}",
                "is_sponsored": True,
                "disclosure": "Sponsored",
                "sponsored_by": ad["sponsored_by"],
                "landing_url": ad["landing_url"],
            },
            "call_count": 0,
            "avg_latency_ms": 0,
            "is_sponsored": True,
        })

    return {
        "trending": trending_out,
        "count": len(trending),
    }


@router.get("/discover/recommendations/{agent_id}")
async def recommendations(agent_id: str, request: Request, limit: int = 5):
    """Get service recommendations for an agent based on usage history."""
    discovery = request.app.state.discovery

    from .services import _service_response
    recs = discovery.get_recommendations(agent_id, limit=limit)
    recs_out = [_service_response(s) for s in recs]

    # AdCP: inject sponsored intelligence into recommendations
    from marketplace.ad_serving import inject_into_service_list
    recs_out = inject_into_service_list(
        recs_out,
        context="mcp_discovery",
        max_ads=1,
        position=0,
    )

    return {
        "recommendations": recs_out,
        "count": len(recs),
    }
