"""Tests for the MCP server tool functions.

These tests mock the AgenticTrade API client and verify that the MCP tool
functions correctly serialize/deserialize JSON and handle errors.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from agentictrade_mcp.client import AgenticTradeError


# ---------------------------------------------------------------------------
# discover_services tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_services_tool():
    """discover_services MCP tool returns JSON string."""
    mock_result = {
        "services": [{"id": "svc-1", "name": "Test"}],
        "total": 1,
        "offset": 0,
        "limit": 20,
    }
    mock_client = AsyncMock()
    mock_client.discover_services.return_value = mock_result

    with patch("agentictrade_mcp.server._get_client", return_value=mock_client):
        from agentictrade_mcp.server import discover_services
        result = await discover_services(query="test", max_results=5)

    parsed = json.loads(result)
    assert parsed["total"] == 1
    assert parsed["services"][0]["id"] == "svc-1"
    mock_client.discover_services.assert_called_once_with(
        query="test", category=None, max_results=5,
    )


@pytest.mark.asyncio
async def test_discover_services_tool_error():
    """discover_services MCP tool returns error JSON on API failure."""
    mock_client = AsyncMock()
    mock_client.discover_services.side_effect = AgenticTradeError(503, "Unavailable")

    with patch("agentictrade_mcp.server._get_client", return_value=mock_client):
        from agentictrade_mcp.server import discover_services
        result = await discover_services()

    parsed = json.loads(result)
    assert parsed["error"] is True
    assert parsed["status_code"] == 503


# ---------------------------------------------------------------------------
# get_service_details tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_service_details_tool():
    """get_service_details MCP tool returns service JSON."""
    mock_svc = {"id": "svc-abc", "name": "Scanner", "status": "active"}
    mock_client = AsyncMock()
    mock_client.get_service_details.return_value = mock_svc

    with patch("agentictrade_mcp.server._get_client", return_value=mock_client):
        from agentictrade_mcp.server import get_service_details
        result = await get_service_details(service_id="svc-abc")

    parsed = json.loads(result)
    assert parsed["id"] == "svc-abc"


@pytest.mark.asyncio
async def test_get_service_details_tool_empty_id():
    """get_service_details returns error for empty service_id."""
    from agentictrade_mcp.server import get_service_details
    result = await get_service_details(service_id="")

    parsed = json.loads(result)
    assert parsed["error"] is True
    assert "required" in parsed["detail"]


# ---------------------------------------------------------------------------
# call_service tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_service_tool():
    """call_service MCP tool forwards to client and returns JSON."""
    mock_result = {
        "status_code": 200,
        "body": {"data": "hello"},
        "billing": {"usage_id": "u-1", "amount_usd": "0.01"},
    }
    mock_client = AsyncMock()
    mock_client.call_service.return_value = mock_result

    with patch("agentictrade_mcp.server._get_client", return_value=mock_client):
        with patch("agentictrade_mcp.server._API_KEY", "k:s"):
            from agentictrade_mcp.server import call_service
            result = await call_service(
                service_id="svc-1",
                payload='{"symbol": "BTC"}',
            )

    parsed = json.loads(result)
    assert parsed["status_code"] == 200
    assert parsed["body"]["data"] == "hello"


@pytest.mark.asyncio
async def test_call_service_tool_invalid_json_payload():
    """call_service returns error for malformed JSON payload."""
    with patch("agentictrade_mcp.server._API_KEY", "k:s"):
        from agentictrade_mcp.server import call_service
        result = await call_service(
            service_id="svc-1",
            payload="not valid json{",
        )

    parsed = json.loads(result)
    assert parsed["error"] is True
    assert "Invalid JSON" in parsed["detail"]


@pytest.mark.asyncio
async def test_call_service_tool_no_api_key():
    """call_service returns error when no API key is available."""
    with patch("agentictrade_mcp.server._API_KEY", ""):
        from agentictrade_mcp.server import call_service
        result = await call_service(service_id="svc-1", api_key="")

    parsed = json.loads(result)
    assert parsed["error"] is True
    assert "API key required" in parsed["detail"]


# ---------------------------------------------------------------------------
# get_balance tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_balance_tool():
    """get_balance MCP tool returns balance JSON."""
    mock_balance = {
        "buyer_id": "b-1",
        "balance": 50.0,
        "total_deposited": 100.0,
        "total_spent": 50.0,
    }
    mock_client = AsyncMock()
    mock_client.get_balance.return_value = mock_balance

    with patch("agentictrade_mcp.server._get_client", return_value=mock_client):
        with patch("agentictrade_mcp.server._API_KEY", "k:s"):
            with patch("agentictrade_mcp.server._BUYER_ID", "b-1"):
                from agentictrade_mcp.server import get_balance
                result = await get_balance()

    parsed = json.loads(result)
    assert parsed["balance"] == 50.0


@pytest.mark.asyncio
async def test_get_balance_tool_no_key():
    """get_balance returns error when API key is missing."""
    with patch("agentictrade_mcp.server._API_KEY", ""):
        from agentictrade_mcp.server import get_balance
        result = await get_balance()

    parsed = json.loads(result)
    assert parsed["error"] is True
    assert "API key required" in parsed["detail"]


@pytest.mark.asyncio
async def test_get_balance_tool_no_buyer():
    """get_balance returns error when buyer_id is missing."""
    with patch("agentictrade_mcp.server._API_KEY", "k:s"):
        with patch("agentictrade_mcp.server._BUYER_ID", ""):
            from agentictrade_mcp.server import get_balance
            result = await get_balance()

    parsed = json.loads(result)
    assert parsed["error"] is True
    assert "buyer_id required" in parsed["detail"]


# ---------------------------------------------------------------------------
# list_categories tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_categories_tool():
    """list_categories MCP tool returns category JSON."""
    mock_cats = {"categories": [{"name": "crypto", "count": 3}]}
    mock_client = AsyncMock()
    mock_client.list_categories.return_value = mock_cats

    with patch("agentictrade_mcp.server._get_client", return_value=mock_client):
        from agentictrade_mcp.server import list_categories
        result = await list_categories()

    parsed = json.loads(result)
    assert len(parsed["categories"]) == 1
    assert parsed["categories"][0]["name"] == "crypto"
