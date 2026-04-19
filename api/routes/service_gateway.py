"""
Service Gateway — Public reverse proxy for platform-hosted services.

Exposes internal services (CoinSifter, JudyAI Tools, etc.) via public URLs
so that external agents can access them through the marketplace proxy.

Routes: /ext/{service_name}/{path}
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response

logger = logging.getLogger("service_gateway")

router = APIRouter(tags=["service-gateway"])

# Platform-hosted service registry: name → internal base URL
# Configure via environment variables; defaults to localhost for local dev.
HOSTED_SERVICES: dict[str, str] = {
    "coinsifter": os.environ.get("GATEWAY_COINSIFTER_URL", "http://localhost:8089"),
    "strategy": os.environ.get("GATEWAY_STRATEGY_URL", "http://localhost:8090"),
    "tools": os.environ.get("GATEWAY_TOOLS_URL", "http://localhost:8095"),
    "scanner": os.environ.get("GATEWAY_SCANNER_URL", "http://localhost:8094"),
    "legacy": os.environ.get("GATEWAY_LEGACY_URL", "http://localhost:8093"),
}


async def _forward(
    service_name: str,
    path: str,
    request: Request,
) -> Response:
    """Forward a request to an internal hosted service."""
    base_url = HOSTED_SERVICES.get(service_name)
    if not base_url:
        return Response(
            content='{"error":"Unknown service"}',
            status_code=404,
            media_type="application/json",
        )

    target = f"{base_url}/{path}" if path else base_url
    body = await request.body()

    # Forward headers, stripping hop-by-hop
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "connection", "transfer-encoding")
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(
                method=request.method,
                url=target,
                content=body if body else None,
                headers=fwd_headers,
                params=dict(request.query_params),
            )

        # Build response, forwarding relevant headers
        resp_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in ("transfer-encoding", "connection", "content-encoding")
        }

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=resp_headers,
            media_type=resp.headers.get("content-type", "application/json"),
        )
    except httpx.ConnectError:
        logger.warning("Service %s unreachable at %s", service_name, base_url)
        return Response(
            content='{"error":"Service temporarily unavailable"}',
            status_code=503,
            media_type="application/json",
        )
    except httpx.ReadTimeout:
        return Response(
            content='{"error":"Service timeout"}',
            status_code=504,
            media_type="application/json",
        )


@router.api_route(
    "/ext/{service_name}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def service_gateway(service_name: str, path: str, request: Request) -> Response:
    """Public gateway to platform-hosted services."""
    return await _forward(service_name, path, request)


@router.get("/ext/{service_name}")
async def service_gateway_root(service_name: str, request: Request) -> Response:
    """Public gateway root (no path)."""
    return await _forward(service_name, "", request)


@router.get("/ext")
async def list_hosted_services() -> dict[str, Any]:
    """List available platform-hosted services."""
    return {
        "hosted_services": list(HOSTED_SERVICES.keys()),
        "usage": "GET/POST https://agentictrade.io/ext/{service_name}/{path}",
    }
