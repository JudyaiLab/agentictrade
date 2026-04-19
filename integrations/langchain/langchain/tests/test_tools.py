"""
Tests for agentictrade-langchain tools.

Uses pytest-httpx to mock HTTP requests so no real API calls are made.
"""

from __future__ import annotations

import json

import httpx
import pytest

from agentictrade_langchain.tool import (
    AgenticTradeBalanceTool,
    AgenticTradeCallTool,
    AgenticTradeClient,
    AgenticTradeSearchTool,
    AgenticTradeServiceDetailTool,
)
from agentictrade_langchain.toolkit import AgenticTradeToolkit


# ---------------------------------------------------------------------------
# Client tests
# ---------------------------------------------------------------------------


class TestAgenticTradeClient:
    def test_invalid_api_key_no_colon(self):
        with pytest.raises(ValueError, match="key_id:secret"):
            AgenticTradeClient(api_key="no_colon_here")

    def test_invalid_api_key_empty(self):
        with pytest.raises(ValueError, match="key_id:secret"):
            AgenticTradeClient(api_key="")

    def test_valid_api_key(self):
        client = AgenticTradeClient(api_key="kid:sec")
        assert client.api_key == "kid:sec"
        assert client.base_url == "https://agentictrade.io"

    def test_custom_base_url(self):
        client = AgenticTradeClient(
            api_key="kid:sec", base_url="https://staging.example.com/"
        )
        assert client.base_url == "https://staging.example.com"

    def test_client_lazy_init(self):
        client = AgenticTradeClient(api_key="kid:sec")
        assert client._client is None
        _ = client.client
        assert client._client is not None

    def test_close(self):
        client = AgenticTradeClient(api_key="kid:sec")
        _ = client.client  # trigger creation
        client.close()
        assert client._client.is_closed


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_client(monkeypatch):
    """Return an AgenticTradeClient that uses a mocked transport."""
    client = AgenticTradeClient(api_key="test_key:test_secret")
    return client


def _make_mock_client(responses: dict[str, tuple[int, dict]]):
    """Create a client whose underlying httpx.Client uses a mock transport.

    ``responses`` maps URL path suffixes to (status_code, json_body) tuples.
    """

    class MockTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            path = request.url.path
            for suffix, (status, body) in responses.items():
                if path.endswith(suffix) or suffix in path:
                    return httpx.Response(
                        status_code=status,
                        json=body,
                        request=request,
                    )
            return httpx.Response(status_code=404, json={"detail": "Not found"}, request=request)

    client = AgenticTradeClient(api_key="test_key:test_secret")
    client._client = httpx.Client(
        base_url=client.base_url,
        headers={
            "Authorization": f"Bearer {client.api_key}",
            "User-Agent": "test",
        },
        transport=MockTransport(),
    )
    return client


# ---------------------------------------------------------------------------
# Search tool tests
# ---------------------------------------------------------------------------


class TestSearchTool:
    def test_search_returns_results(self):
        client = _make_mock_client({
            "/discover": (200, {
                "services": [
                    {
                        "id": "svc-001",
                        "name": "Sentiment API",
                        "description": "Analyze sentiment of text",
                        "category": "nlp",
                        "tags": ["sentiment", "nlp"],
                        "pricing": {
                            "price_per_call": "0.01",
                            "free_tier_calls": 100,
                        },
                        "quality": {
                            "health_score": 95,
                            "quality_tier": "Premium",
                        },
                    }
                ],
                "total": 1,
                "offset": 0,
                "limit": 10,
            }),
        })
        tool = AgenticTradeSearchTool(client=client)
        result = tool.invoke({"query": "sentiment", "limit": 10})

        assert "Sentiment API" in result
        assert "svc-001" in result
        assert "$0.01/call" in result
        assert "Premium" in result
        assert "100 calls" in result

    def test_search_no_results(self):
        client = _make_mock_client({
            "/discover": (200, {
                "services": [],
                "total": 0,
                "offset": 0,
                "limit": 10,
            }),
        })
        tool = AgenticTradeSearchTool(client=client)
        result = tool.invoke({"query": "nonexistent_service_xyz"})
        assert "No services found" in result

    def test_search_error_handling(self):
        client = _make_mock_client({
            "/discover": (500, {"detail": "Internal server error"}),
        })
        tool = AgenticTradeSearchTool(client=client)
        result = tool.invoke({"query": "test"})
        assert "Error" in result

    def test_search_with_filters(self):
        client = _make_mock_client({
            "/discover": (200, {
                "services": [
                    {
                        "id": "svc-002",
                        "name": "Cheap API",
                        "description": "Affordable",
                        "category": "crypto",
                        "tags": ["crypto"],
                        "pricing": {"price_per_call": "0.001", "free_tier_calls": 0},
                        "quality": {"health_score": None, "quality_tier": "Standard"},
                    }
                ],
                "total": 1,
                "offset": 0,
                "limit": 5,
            }),
        })
        tool = AgenticTradeSearchTool(client=client)
        result = tool.invoke({
            "query": "",
            "category": "crypto",
            "max_price": "0.01",
            "limit": 5,
        })
        assert "Cheap API" in result

    def test_search_rate_limited(self):
        """Verify the tool surfaces rate limit errors gracefully."""
        client = _make_mock_client({
            "/discover": (429, {"detail": "Rate limit exceeded"}),
        })
        # Override to return 429 with Retry-After header
        class RateLimitTransport(httpx.BaseTransport):
            def handle_request(self, request):
                return httpx.Response(
                    status_code=429,
                    json={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": "30"},
                    request=request,
                )

        client._client = httpx.Client(
            base_url=client.base_url,
            transport=RateLimitTransport(),
        )
        tool = AgenticTradeSearchTool(client=client)
        result = tool.invoke({"query": "test"})
        assert "Rate limited" in result


# ---------------------------------------------------------------------------
# Service detail tool tests
# ---------------------------------------------------------------------------


class TestServiceDetailTool:
    def test_get_service_detail(self):
        client = _make_mock_client({
            "/services/svc-001": (200, {
                "id": "svc-001",
                "provider_id": "provider-abc",
                "name": "Sentiment API",
                "description": "Analyze text sentiment",
                "status": "active",
                "category": "nlp",
                "tags": ["sentiment"],
                "pricing": {
                    "price_per_call": "0.01",
                    "currency": "USD",
                    "payment_method": "x402",
                    "free_tier_calls": 100,
                },
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-03-15T00:00:00",
            }),
        })
        tool = AgenticTradeServiceDetailTool(client=client)
        result = tool.invoke({"service_id": "svc-001"})

        assert "Sentiment API" in result
        assert "provider-abc" in result
        assert "$0.01" in result
        assert "x402" in result
        assert "100" in result

    def test_service_not_found(self):
        client = _make_mock_client({})
        tool = AgenticTradeServiceDetailTool(client=client)
        result = tool.invoke({"service_id": "nonexistent"})
        assert "Error" in result or "not found" in result.lower()


# ---------------------------------------------------------------------------
# Call tool tests
# ---------------------------------------------------------------------------


class TestCallTool:
    def test_call_post(self):
        client = _make_mock_client({
            "/proxy/svc-001/analyze": (200, {
                "sentiment": "positive",
                "confidence": 0.95,
            }),
        })
        tool = AgenticTradeCallTool(client=client)
        result = tool.invoke({
            "service_id": "svc-001",
            "path": "analyze",
            "method": "POST",
            "body": '{"text": "Great product!"}',
        })
        data = json.loads(result)
        assert data["sentiment"] == "positive"
        assert data["confidence"] == 0.95

    def test_call_get(self):
        client = _make_mock_client({
            "/proxy/svc-002/status": (200, {"status": "healthy"}),
        })
        tool = AgenticTradeCallTool(client=client)
        result = tool.invoke({
            "service_id": "svc-002",
            "path": "status",
            "method": "GET",
        })
        data = json.loads(result)
        assert data["status"] == "healthy"

    def test_call_invalid_method(self):
        client = _make_mock_client({})
        tool = AgenticTradeCallTool(client=client)
        result = tool.invoke({
            "service_id": "svc-001",
            "path": "test",
            "method": "INVALID",
        })
        assert "Invalid HTTP method" in result

    def test_call_invalid_json_body(self):
        client = _make_mock_client({})
        tool = AgenticTradeCallTool(client=client)
        result = tool.invoke({
            "service_id": "svc-001",
            "path": "test",
            "method": "POST",
            "body": "not valid json{{{",
        })
        assert "Invalid JSON body" in result

    def test_call_service_not_found(self):
        client = _make_mock_client({})
        tool = AgenticTradeCallTool(client=client)
        result = tool.invoke({
            "service_id": "nonexistent",
            "path": "test",
            "method": "POST",
            "body": "{}",
        })
        assert "Error" in result

    def test_call_auth_failure(self):
        class AuthFailTransport(httpx.BaseTransport):
            def handle_request(self, request):
                return httpx.Response(
                    status_code=401,
                    json={"detail": "Invalid key format"},
                    request=request,
                )

        client = AgenticTradeClient(api_key="bad:key")
        client._client = httpx.Client(
            base_url=client.base_url,
            transport=AuthFailTransport(),
        )
        tool = AgenticTradeCallTool(client=client)
        result = tool.invoke({
            "service_id": "svc-001",
            "path": "test",
            "method": "POST",
        })
        assert "Authentication failed" in result

    def test_call_no_path(self):
        """Call with empty path should hit /proxy/{service_id}."""
        client = _make_mock_client({
            "/proxy/svc-003": (200, {"ok": True}),
        })
        tool = AgenticTradeCallTool(client=client)
        result = tool.invoke({
            "service_id": "svc-003",
            "method": "GET",
        })
        data = json.loads(result)
        assert data["ok"] is True


# ---------------------------------------------------------------------------
# Balance tool tests
# ---------------------------------------------------------------------------


class TestBalanceTool:
    def test_check_balance(self):
        client = _make_mock_client({
            "/balance/buyer-123": (200, {
                "buyer_id": "buyer-123",
                "balance": 42.5,
                "total_deposited": 100.0,
                "total_spent": 57.5,
            }),
        })
        tool = AgenticTradeBalanceTool(client=client)
        result = tool.invoke({"buyer_id": "buyer-123"})

        assert "buyer-123" in result
        assert "42.5000" in result
        assert "100.0000" in result
        assert "57.5000" in result

    def test_balance_zero(self):
        client = _make_mock_client({
            "/balance/new-buyer": (200, {
                "buyer_id": "new-buyer",
                "balance": 0,
                "total_deposited": 0,
                "total_spent": 0,
            }),
        })
        tool = AgenticTradeBalanceTool(client=client)
        result = tool.invoke({"buyer_id": "new-buyer"})
        assert "0.0000" in result

    def test_balance_auth_error(self):
        client = _make_mock_client({
            "/balance/other": (403, {"detail": "Access denied"}),
        })
        tool = AgenticTradeBalanceTool(client=client)
        result = tool.invoke({"buyer_id": "other"})
        assert "Error" in result


# ---------------------------------------------------------------------------
# Toolkit tests
# ---------------------------------------------------------------------------


class TestToolkit:
    def test_get_all_tools(self):
        toolkit = AgenticTradeToolkit(api_key="kid:sec")
        tools = toolkit.get_tools()
        assert len(tools) == 4
        names = {t.name for t in tools}
        assert names == {
            "agentictrade_search",
            "agentictrade_service_detail",
            "agentictrade_call",
            "agentictrade_balance",
        }

    def test_selective_tools(self):
        toolkit = AgenticTradeToolkit(
            api_key="kid:sec",
            include_balance=False,
            include_service_detail=False,
        )
        tools = toolkit.get_tools()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "agentictrade_balance" not in names
        assert "agentictrade_service_detail" not in names

    def test_custom_base_url(self):
        toolkit = AgenticTradeToolkit(
            api_key="kid:sec",
            base_url="https://staging.example.com",
        )
        tools = toolkit.get_tools()
        # All tools should share a client with the custom base URL
        for tool in tools:
            assert tool.client.base_url == "https://staging.example.com"

    def test_invalid_api_key_raises_on_get_tools(self):
        """API key without colon raises ValueError when get_tools() creates the client."""
        toolkit = AgenticTradeToolkit(api_key="no_colon")
        with pytest.raises(ValueError, match="key_id:secret"):
            toolkit.get_tools()

    def test_tools_share_client(self):
        toolkit = AgenticTradeToolkit(api_key="kid:sec")
        tools = toolkit.get_tools()
        clients = {id(t.client) for t in tools}
        assert len(clients) == 1, "All tools should share the same client instance"


# ---------------------------------------------------------------------------
# Tool schema tests (LLM-facing metadata)
# ---------------------------------------------------------------------------


class TestToolSchemas:
    def test_search_tool_schema(self):
        client = _make_mock_client({})
        tool = AgenticTradeSearchTool(client=client)
        schema = tool.args_schema.model_json_schema()
        props = schema["properties"]
        assert "query" in props
        assert "category" in props
        assert "limit" in props
        assert "description" in props["query"]

    def test_call_tool_schema(self):
        client = _make_mock_client({})
        tool = AgenticTradeCallTool(client=client)
        schema = tool.args_schema.model_json_schema()
        props = schema["properties"]
        assert "service_id" in props
        assert "path" in props
        assert "method" in props
        assert "body" in props
        assert "service_id" in schema.get("required", [])

    def test_balance_tool_schema(self):
        client = _make_mock_client({})
        tool = AgenticTradeBalanceTool(client=client)
        schema = tool.args_schema.model_json_schema()
        assert "buyer_id" in schema["properties"]
        assert "buyer_id" in schema.get("required", [])

    def test_tool_descriptions_not_empty(self):
        client = _make_mock_client({})
        tools = [
            AgenticTradeSearchTool(client=client),
            AgenticTradeServiceDetailTool(client=client),
            AgenticTradeCallTool(client=client),
            AgenticTradeBalanceTool(client=client),
        ]
        for tool in tools:
            assert tool.description, f"{tool.name} has empty description"
            assert len(tool.description) > 20, f"{tool.name} description too short"
