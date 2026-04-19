"""Tests for the AgenticTrade API client."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from agentictrade_mcp.client import AgenticTradeClient, AgenticTradeError

BASE = "https://agentictrade.io"


@pytest.fixture
def client():
    return AgenticTradeClient(base_url=BASE, timeout=5.0)


# ---------------------------------------------------------------------------
# discover_services
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_discover_services_basic(client):
    """discover_services sends correct query params and returns parsed JSON."""
    mock_response = {
        "services": [
            {
                "id": "svc-1",
                "name": "Test Scanner",
                "description": "Scans things",
                "pricing": {"price_per_call": "0.01", "currency": "USDC"},
                "status": "active",
                "category": "crypto",
            }
        ],
        "total": 1,
        "offset": 0,
        "limit": 20,
    }
    route = respx.get(f"{BASE}/api/v1/discover").mock(
        return_value=httpx.Response(200, json=mock_response)
    )

    result = await client.discover_services(query="scanner", max_results=20)

    assert route.called
    assert result["total"] == 1
    assert result["services"][0]["id"] == "svc-1"
    # Verify query params
    request = route.calls[0].request
    assert "q=scanner" in str(request.url)
    assert "limit=20" in str(request.url)


@respx.mock
@pytest.mark.asyncio
async def test_discover_services_with_category(client):
    """discover_services passes category filter."""
    respx.get(f"{BASE}/api/v1/discover").mock(
        return_value=httpx.Response(200, json={"services": [], "total": 0})
    )

    result = await client.discover_services(category="crypto")
    assert result["total"] == 0


@respx.mock
@pytest.mark.asyncio
async def test_discover_services_clamps_max_results(client):
    """max_results is clamped to [1, 100]."""
    route = respx.get(f"{BASE}/api/v1/discover").mock(
        return_value=httpx.Response(200, json={"services": [], "total": 0})
    )

    await client.discover_services(max_results=999)
    request = route.calls[0].request
    assert "limit=100" in str(request.url)

    await client.discover_services(max_results=-5)
    request = route.calls[1].request
    assert "limit=1" in str(request.url)


# ---------------------------------------------------------------------------
# get_service_details
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_get_service_details(client):
    """get_service_details returns full service info."""
    mock_svc = {
        "id": "svc-abc",
        "name": "Backtest Engine",
        "description": "Run backtests",
        "provider_id": "provider-1",
        "pricing": {
            "price_per_call": "0.05",
            "currency": "USDC",
            "payment_method": "x402",
            "free_tier_calls": 10,
        },
        "status": "active",
        "category": "crypto",
        "tags": ["backtest", "trading"],
    }
    respx.get(f"{BASE}/api/v1/services/svc-abc").mock(
        return_value=httpx.Response(200, json=mock_svc)
    )

    result = await client.get_service_details("svc-abc")
    assert result["id"] == "svc-abc"
    assert result["pricing"]["free_tier_calls"] == 10


@respx.mock
@pytest.mark.asyncio
async def test_get_service_details_not_found(client):
    """get_service_details raises on 404."""
    respx.get(f"{BASE}/api/v1/services/nonexistent").mock(
        return_value=httpx.Response(404, json={"detail": "Service not found"})
    )

    with pytest.raises(AgenticTradeError) as exc_info:
        await client.get_service_details("nonexistent")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_service_details_empty_id(client):
    """get_service_details raises ValueError for empty service_id."""
    with pytest.raises(ValueError, match="service_id is required"):
        await client.get_service_details("")


# ---------------------------------------------------------------------------
# call_service
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_call_service(client):
    """call_service forwards request and extracts billing headers."""
    respx.post(f"{BASE}/api/v1/proxy/svc-1").mock(
        return_value=httpx.Response(
            200,
            json={"result": "ok"},
            headers={
                "x-acf-usage-id": "usage-abc",
                "x-acf-amount": "0.01",
                "x-acf-free-tier": "false",
                "x-acf-latency-ms": "142",
            },
        )
    )

    result = await client.call_service(
        service_id="svc-1",
        api_key="key1:secret1",
        payload={"symbol": "BTC"},
    )

    assert result["status_code"] == 200
    assert result["body"]["result"] == "ok"
    assert result["billing"]["usage_id"] == "usage-abc"
    assert result["billing"]["amount_usd"] == "0.01"


@respx.mock
@pytest.mark.asyncio
async def test_call_service_with_path(client):
    """call_service appends sub-path to proxy URL."""
    route = respx.post(f"{BASE}/api/v1/proxy/svc-1/api/scan").mock(
        return_value=httpx.Response(200, json={"scanned": True})
    )

    result = await client.call_service(
        service_id="svc-1",
        api_key="key1:secret1",
        path="/api/scan",
    )

    assert route.called
    assert result["status_code"] == 200


@pytest.mark.asyncio
async def test_call_service_missing_key(client):
    """call_service raises ValueError when api_key is empty."""
    with pytest.raises(ValueError, match="api_key is required"):
        await client.call_service("svc-1", api_key="", payload={})


# ---------------------------------------------------------------------------
# get_balance
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_get_balance(client):
    """get_balance returns balance info."""
    mock_balance = {
        "buyer_id": "buyer-1",
        "balance": 42.50,
        "total_deposited": 100.0,
        "total_spent": 57.50,
    }
    respx.get(f"{BASE}/api/v1/balance/buyer-1").mock(
        return_value=httpx.Response(200, json=mock_balance)
    )

    result = await client.get_balance("key1:secret1", "buyer-1")
    assert result["balance"] == 42.50
    assert result["total_spent"] == 57.50


@pytest.mark.asyncio
async def test_get_balance_missing_buyer(client):
    """get_balance raises ValueError when buyer_id is empty."""
    with pytest.raises(ValueError, match="buyer_id is required"):
        await client.get_balance("key1:secret1", "")


# ---------------------------------------------------------------------------
# list_categories
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_categories(client):
    """list_categories returns category list."""
    mock_cats = {
        "categories": [
            {"name": "crypto", "count": 5},
            {"name": "data", "count": 3},
        ]
    }
    respx.get(f"{BASE}/api/v1/discover/categories").mock(
        return_value=httpx.Response(200, json=mock_cats)
    )

    result = await client.list_categories()
    assert len(result["categories"]) == 2
    assert result["categories"][0]["name"] == "crypto"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_api_error_parsing(client):
    """AgenticTradeError is raised with status and detail for API errors."""
    respx.get(f"{BASE}/api/v1/discover").mock(
        return_value=httpx.Response(500, json={"detail": "Internal error"})
    )

    with pytest.raises(AgenticTradeError) as exc_info:
        await client.discover_services()

    assert exc_info.value.status_code == 500
    assert "Internal error" in exc_info.value.detail


@respx.mock
@pytest.mark.asyncio
async def test_close_client(client):
    """close() shuts down the HTTP client cleanly."""
    respx.get(f"{BASE}/api/v1/discover/categories").mock(
        return_value=httpx.Response(200, json={"categories": []})
    )
    await client.list_categories()  # force client creation
    await client.close()
    assert client._http is None
