"""
Proxy API routes — the core payment proxy endpoint.
Buyers call the marketplace, marketplace handles payment + forwarding.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from marketplace.auth import APIKeyManager, AuthError
from marketplace.proxy import PaymentProxy, ProxyError

router = APIRouter(tags=["proxy"])


async def _get_auth(request: Request) -> tuple[APIKeyManager, str, dict]:
    """Extract and validate API key from request headers.

    Uses ``db.arun()`` for DB-backed operations (validate + rate limit)
    to avoid blocking the asyncio event loop.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing API key")

    token = auth_header[7:]  # Strip "Bearer "
    parts = token.split(":", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=401,
            detail="Invalid key format. Use key_id:secret",
        )

    key_id, secret = parts
    key_mgr: APIKeyManager = request.app.state.auth
    db = request.app.state.db
    try:
        key_record = await db.arun(key_mgr.validate, key_id, secret)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))

    # Rate limit check (DB-backed for horizontal scaling)
    limit = key_record.get("rate_limit", 60)
    rl_result = await db.arun(key_mgr.check_rate_limit, key_id, limit)
    if rl_result is not True:
        retry_after = str(rl_result) if isinstance(rl_result, int) else "60"
        raise HTTPException(
            status_code=429,
            detail="API key rate limit exceeded. Try again later.",
            headers={"Retry-After": retry_after},
        )

    return key_mgr, key_record["owner_id"], key_record


@router.api_route(
    "/proxy/{service_id}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy_request(
    service_id: str,
    path: str,
    request: Request,
):
    """
    Proxy a request to a service provider with automatic payment.

    Usage:
        POST /api/v1/proxy/{service_id}/scan
        Authorization: Bearer {key_id}:{secret}

    The marketplace:
    1. Validates your API key
    2. Looks up the service
    3. Forwards your request to the provider
    4. Records usage + handles payment
    5. Returns the provider's response
    """
    _, buyer_id, key_record = await _get_auth(request)

    # Look up service
    registry = request.app.state.registry
    service = registry.get(service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    # Convert ServiceListing to dict for proxy
    service_dict = {
        "id": service.id,
        "provider_id": service.provider_id,
        "endpoint": service.endpoint,
        "price_per_call": str(service.pricing.price_per_call),
        "payment_method": service.pricing.payment_method,
        "free_tier_calls": service.pricing.free_tier_calls,
        "status": service.status,
    }

    # Read request body
    body = await request.body()

    # Detect if x402 middleware already verified payment for this request
    x402_paid = hasattr(request.state, "payment_payload") and request.state.payment_payload is not None

    # Extract idempotency key from X-Request-ID header (enables retry safety)
    request_id = request.headers.get("x-request-id")

    # ── Capability Probe Protocol ──
    # Agents can send X-Probe: true to test service compatibility before
    # committing to a full (paid) transaction. Probes are zero-cost,
    # forward a real request, and return a standardized compatibility report.
    is_probe = request.headers.get("x-probe", "").lower() == "true"

    if is_probe:
        proxy: PaymentProxy = request.app.state.proxy
        try:
            probe_result = await proxy.forward_probe(
                service=service_dict,
                buyer_id=buyer_id,
                method=request.method,
                path=f"/{path}" if path else "",
                headers=dict(request.headers),
                body=body if body else None,
                query_params=dict(request.query_params),
            )
        except ProxyError as e:
            raise HTTPException(status_code=400, detail=str(e))

        return {
            "probe": True,
            "service_id": service_id,
            "compatible": probe_result["compatible"],
            "latency_ms": probe_result["latency_ms"],
            "status_code": probe_result["status_code"],
            "response_preview": probe_result["response_preview"],
            "schema": {
                "price_per_call": str(service.pricing.price_per_call),
                "currency": service.pricing.currency,
                "free_tier_calls": service.pricing.free_tier_calls,
                "payment_method": service.pricing.payment_method,
            },
            "message": "Probe successful. No charge applied.",
        }

    # Forward through proxy
    proxy: PaymentProxy = request.app.state.proxy
    try:
        result = proxy_result = await proxy.forward_request(
            service=service_dict,
            buyer_id=buyer_id,
            method=request.method,
            path=f"/{path}" if path else "",
            headers=dict(request.headers),
            body=body if body else None,
            query_params=dict(request.query_params),
            x402_paid=x402_paid,
            request_id=request_id,
        )
    except ProxyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Build response with billing headers
    response_headers = {
        "X-ACF-Usage-Id": result.billing.usage_id,
        "X-ACF-Amount": str(result.billing.amount),
        "X-ACF-Free-Tier": str(result.billing.free_tier).lower(),
        "X-ACF-Latency-Ms": str(result.latency_ms),
    }

    # Filter provider response headers (remove hop-by-hop)
    skip_headers = {
        "transfer-encoding", "connection", "keep-alive",
        "proxy-authenticate", "proxy-authorization", "te",
        "trailer", "upgrade", "content-length", "content-encoding",
    }
    for key, value in result.headers.items():
        if key.lower() not in skip_headers:
            response_headers[key] = value

    content_type = result.headers.get("content-type", "application/octet-stream")

    return Response(
        content=result.body,
        status_code=result.status_code,
        headers=response_headers,
        media_type=content_type,
    )


@router.get("/usage/me")
async def my_usage(request: Request):
    """Get usage statistics for the authenticated buyer."""
    _, buyer_id, _ = await _get_auth(request)

    db = request.app.state.db
    stats = db.get_usage_stats(buyer_id=buyer_id)

    return {
        "buyer_id": buyer_id,
        "total_calls": stats["total_calls"],
        "total_spent_usd": str(stats["total_revenue"]),
        "avg_latency_ms": stats["avg_latency_ms"],
    }
