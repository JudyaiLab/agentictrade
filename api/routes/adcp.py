"""AdCP (Ad Context Protocol) seller agent endpoint.

Exposes AgenticTrade marketplace ad inventory to buyer agents
using the AdCP standard (JSON-RPC 2.0 over MCP transport).

Reference: https://docs.adcontextprotocol.org
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/adcp", tags=["adcp"])
log = logging.getLogger("adcp")

# ── Ad Products (available placements) ──────────────────────────────────────

AD_PRODUCTS = [
    {
        "product_id": "sponsored_search_result",
        "name": "Sponsored API Search Result",
        "description": (
            "Promoted position in marketplace search results. "
            "Contextually matched to the buyer agent's query. "
            "Labeled as 'Sponsored' for transparency."
        ),
        "channel": "display",
        "format": "native",
        "pricing_options": [
            {"id": "cpc_100", "model": "cpc", "rate": 1.00, "currency": "USD"},
            {"id": "cpm_15", "model": "cpm", "rate": 15.00, "currency": "USD"},
        ],
        "targeting": {
            "categories": ["ai_apis", "developer_tools", "saas", "ml_models"],
            "contexts": ["api_search", "service_discovery", "category_browse"],
        },
        "forecast": {
            "daily_impressions_estimate": 500,
            "avg_ctr": 0.035,
        },
    },
    {
        "product_id": "featured_provider",
        "name": "Featured API Provider",
        "description": (
            "Premium placement on category pages, discovery feeds, "
            "and the marketplace homepage. Includes provider logo "
            "and description highlight."
        ),
        "channel": "display",
        "format": "native",
        "pricing_options": [
            {"id": "cpm_25", "model": "cpm", "rate": 25.00, "currency": "USD"},
            {"id": "cpa_signup", "model": "cpa", "rate": 5.00, "currency": "USD"},
        ],
        "targeting": {
            "categories": ["all"],
            "contexts": ["homepage", "category_page", "discovery_feed"],
        },
        "forecast": {
            "daily_impressions_estimate": 1000,
            "avg_ctr": 0.02,
        },
    },
    {
        "product_id": "sponsored_intelligence",
        "name": "Sponsored Intelligence — API Recommendation",
        "description": (
            "Contextual brand mention when AI agents query the marketplace "
            "via MCP tools. The recommendation appears alongside organic "
            "results with clear 'Sponsored' disclosure."
        ),
        "channel": "sponsored_intelligence",
        "format": "conversational",
        "pricing_options": [
            {"id": "cpm_40", "model": "cpm", "rate": 40.00, "currency": "USD"},
        ],
        "targeting": {
            "categories": ["ai_apis", "developer_tools"],
            "contexts": ["mcp_discovery", "agent_query"],
        },
        "forecast": {
            "daily_impressions_estimate": 200,
            "avg_ctr": 0.05,
        },
    },
]

# ── In-memory campaign store (replace with DB in production) ────────────────

_campaigns: dict[str, dict] = {}
_impressions: list[dict] = []


# ── MCP JSON-RPC 2.0 Handler ───────────────────────────────────────────────

@router.post("/mcp")
async def adcp_mcp(request: Request):
    """Handle AdCP MCP tool calls (JSON-RPC 2.0 format).

    Supported tools:
    - get_products: Discover available ad placements
    - create_media_buy: Create an ad campaign
    - get_media_buy_delivery: Get campaign performance metrics
    """
    try:
        body = await request.json()
    except Exception:
        return _jsonrpc_error(-32700, "Parse error")

    req_id = body.get("id", 1)
    method = body.get("method", "")
    params = body.get("params", {})

    if method == "tools/list":
        return _jsonrpc_ok(req_id, {
            "tools": [
                {
                    "name": "get_products",
                    "description": "Discover available ad placements on AgenticTrade marketplace",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "buying_mode": {
                                "type": "string",
                                "enum": ["brief", "catalog"],
                                "description": "brief=natural language query, catalog=list all products",
                            },
                            "brief": {
                                "type": "string",
                                "description": "Natural language description of what you want to advertise",
                            },
                        },
                    },
                },
                {
                    "name": "create_media_buy",
                    "description": "Create an advertising campaign on the marketplace",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "pricing_option_id": {"type": "string"},
                            "budget": {"type": "number"},
                            "start_time": {"type": "string", "format": "date-time"},
                            "end_time": {"type": "string", "format": "date-time"},
                            "brand_domain": {"type": "string"},
                            "creative_text": {"type": "string"},
                            "landing_url": {"type": "string"},
                        },
                        "required": ["product_id", "pricing_option_id", "budget", "brand_domain"],
                    },
                },
                {
                    "name": "get_media_buy_delivery",
                    "description": "Get performance report for an ad campaign",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "campaign_id": {"type": "string"},
                        },
                        "required": ["campaign_id"],
                    },
                },
            ]
        })

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "get_products":
            return _handle_get_products(req_id, arguments)
        elif tool_name == "create_media_buy":
            return _handle_create_media_buy(req_id, arguments)
        elif tool_name == "get_media_buy_delivery":
            return _handle_get_delivery(req_id, arguments)
        else:
            return _jsonrpc_error(-32601, f"Unknown tool: {tool_name}", req_id)

    return _jsonrpc_error(-32601, "Method not found", req_id)


# ── Tool Handlers ───────────────────────────────────────────────────────────

def _handle_get_products(req_id: int, args: dict) -> JSONResponse:
    """Return available ad placements."""
    mode = args.get("buying_mode", "catalog")
    brief = args.get("brief", "")

    products = AD_PRODUCTS

    if mode == "brief" and brief:
        brief_lower = brief.lower()
        products = [
            p for p in AD_PRODUCTS
            if any(
                kw in brief_lower
                for kw in (p.get("targeting", {}).get("categories", [])
                           + p.get("targeting", {}).get("contexts", []))
            )
        ]
        if not products:
            products = AD_PRODUCTS

    return _jsonrpc_ok(req_id, {
        "content": [{
            "type": "text",
            "text": json.dumps({
                "products": products,
                "total": len(products),
                "marketplace": "AgenticTrade",
                "marketplace_url": "https://agentictrade.io",
            }),
        }],
    })


def _handle_create_media_buy(req_id: int, args: dict) -> JSONResponse:
    """Create an ad campaign."""
    product_id = args.get("product_id", "")
    pricing_id = args.get("pricing_option_id", "")
    budget = args.get("budget", 0)
    brand = args.get("brand_domain", "")

    product = next((p for p in AD_PRODUCTS if p["product_id"] == product_id), None)
    if not product:
        return _jsonrpc_error(-32602, f"Unknown product: {product_id}", req_id)

    pricing = next((o for o in product["pricing_options"] if o["id"] == pricing_id), None)
    if not pricing:
        return _jsonrpc_error(-32602, f"Unknown pricing option: {pricing_id}", req_id)

    if budget <= 0:
        return _jsonrpc_error(-32602, "Budget must be positive", req_id)

    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    campaign = {
        "campaign_id": campaign_id,
        "product_id": product_id,
        "pricing": pricing,
        "budget": budget,
        "budget_spent": 0.0,
        "brand_domain": brand,
        "creative_text": args.get("creative_text", ""),
        "landing_url": args.get("landing_url", ""),
        "start_time": args.get("start_time", datetime.now(timezone.utc).isoformat()),
        "end_time": args.get("end_time"),
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "impressions": 0,
        "clicks": 0,
    }

    _campaigns[campaign_id] = campaign
    log.info(f"AdCP campaign created: {campaign_id} for {brand} (${budget})")

    return _jsonrpc_ok(req_id, {
        "content": [{
            "type": "text",
            "text": json.dumps({
                "campaign_id": campaign_id,
                "status": "active",
                "product": product["name"],
                "pricing_model": pricing["model"],
                "rate": pricing["rate"],
                "budget": budget,
                "message": "Campaign created successfully. Ads will start serving immediately.",
            }),
        }],
    })


def _handle_get_delivery(req_id: int, args: dict) -> JSONResponse:
    """Get campaign performance report."""
    campaign_id = args.get("campaign_id", "")
    campaign = _campaigns.get(campaign_id)

    if not campaign:
        return _jsonrpc_error(-32602, f"Campaign not found: {campaign_id}", req_id)

    return _jsonrpc_ok(req_id, {
        "content": [{
            "type": "text",
            "text": json.dumps({
                "campaign_id": campaign_id,
                "status": campaign["status"],
                "budget": campaign["budget"],
                "budget_spent": campaign["budget_spent"],
                "impressions": campaign["impressions"],
                "clicks": campaign["clicks"],
                "ctr": (campaign["clicks"] / max(campaign["impressions"], 1)) * 100,
                "created_at": campaign["created_at"],
            }),
        }],
    })


# ── Internal API for ad serving ─────────────────────────────────────────────

@router.get("/active-campaigns")
async def list_active_campaigns():
    """Internal: list active campaigns for ad injection."""
    active = [c for c in _campaigns.values() if c["status"] == "active"]
    return {"campaigns": active, "count": len(active)}


@router.post("/impression")
async def log_impression(request: Request):
    """Internal: log an ad impression."""
    body = await request.json()
    campaign_id = body.get("campaign_id", "")
    if campaign_id in _campaigns:
        _campaigns[campaign_id]["impressions"] += 1
        pricing = _campaigns[campaign_id]["pricing"]
        if pricing["model"] == "cpm":
            _campaigns[campaign_id]["budget_spent"] += pricing["rate"] / 1000
    _impressions.append({
        "campaign_id": campaign_id,
        "ts": time.time(),
        "context": body.get("context", ""),
    })
    return {"ok": True}


@router.post("/click")
async def log_click(request: Request):
    """Internal: log an ad click."""
    body = await request.json()
    campaign_id = body.get("campaign_id", "")
    if campaign_id in _campaigns:
        _campaigns[campaign_id]["clicks"] += 1
        pricing = _campaigns[campaign_id]["pricing"]
        if pricing["model"] == "cpc":
            _campaigns[campaign_id]["budget_spent"] += pricing["rate"]
    return {"ok": True}


# ── JSON-RPC helpers ────────────────────────────────────────────────────────

def _jsonrpc_ok(req_id: int, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _jsonrpc_error(code: int, message: str, req_id: int = 1) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
        status_code=200,  # JSON-RPC errors use 200 HTTP
    )
