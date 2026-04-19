"""Tests for MCP Bridge — server + discovery modules."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from marketplace.db import Database
from marketplace.registry import ServiceRegistry
from mcp_bridge.discovery import ManifestGenerator
from mcp_bridge.server import (
    MCP_AVAILABLE,
    TOOL_DEFINITIONS,
    MarketplaceMCPServer,
    _json_response,
    _serialize,
    create_mcp_server,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    with TemporaryDirectory() as tmp:
        yield Database(Path(tmp) / "test.db")


@pytest.fixture
def registry(db):
    return ServiceRegistry(db)


@pytest.fixture
def populated_registry(registry):
    """Registry pre-loaded with three services in different categories."""
    registry.register(
        provider_id="prov-1",
        name="Sentiment Analysis API",
        description="Analyse text sentiment",
        endpoint="https://api.example.com/sentiment",
        price_per_call="0.005",
        category="ai",
        tags=["nlp", "sentiment"],
    )
    registry.register(
        provider_id="prov-2",
        name="Token Price Feed",
        description="Real-time crypto prices",
        endpoint="https://prices.example.com/v2",
        price_per_call="0.002",
        category="data",
        tags=["crypto", "market"],
        payment_method="both",
        free_tier_calls=50,
    )
    registry.register(
        provider_id="prov-3",
        name="Image Recognition",
        description="Classify images with AI",
        endpoint="https://vision.example.com",
        price_per_call="0.01",
        category="ai",
        tags=["vision", "image"],
    )
    return registry


@pytest.fixture
def generator(populated_registry):
    return ManifestGenerator(populated_registry)


@pytest.fixture
def empty_generator(registry):
    return ManifestGenerator(registry)


# ---------------------------------------------------------------------------
# ManifestGenerator tests
# ---------------------------------------------------------------------------

class TestManifestGenerate:
    def test_manifest_has_version(self, generator):
        manifest = generator.generate()
        assert manifest["version"] == "1.0"

    def test_manifest_has_generated_at(self, generator):
        manifest = generator.generate()
        assert "generated_at" in manifest
        # Should parse as ISO datetime
        dt = datetime.fromisoformat(manifest["generated_at"])
        assert dt.tzinfo is not None

    def test_manifest_services_count(self, generator):
        manifest = generator.generate()
        assert len(manifest["services"]) == 3

    def test_manifest_service_fields(self, generator):
        manifest = generator.generate()
        svc = manifest["services"][0]
        required_fields = {"id", "name", "description", "pricing", "category", "tags", "endpoint_hint"}
        assert required_fields.issubset(svc.keys())

    def test_manifest_pricing_fields(self, generator):
        manifest = generator.generate()
        pricing = manifest["services"][0]["pricing"]
        assert "price_per_call" in pricing
        assert "currency" in pricing
        assert "payment_method" in pricing
        assert "free_tier_calls" in pricing

    def test_manifest_tags_are_lists(self, generator):
        manifest = generator.generate()
        for svc in manifest["services"]:
            assert isinstance(svc["tags"], list)

    def test_manifest_price_is_string(self, generator):
        """Prices should be serialized as strings to avoid floating-point issues."""
        manifest = generator.generate()
        for svc in manifest["services"]:
            assert isinstance(svc["pricing"]["price_per_call"], str)


class TestManifestEmpty:
    def test_empty_registry_produces_empty_services(self, empty_generator):
        manifest = empty_generator.generate()
        assert manifest["services"] == []
        assert manifest["version"] == "1.0"

    def test_empty_manifest_has_generated_at(self, empty_generator):
        manifest = empty_generator.generate()
        assert "generated_at" in manifest


class TestManifestJSON:
    def test_to_json_returns_valid_json(self, generator):
        raw = generator.to_json()
        parsed = json.loads(raw)
        assert parsed["version"] == "1.0"

    def test_to_json_indent(self, generator):
        raw = generator.to_json(indent=4)
        # Indented output has newlines
        assert "\n" in raw
        parsed = json.loads(raw)
        assert len(parsed["services"]) == 3

    def test_empty_to_json(self, empty_generator):
        raw = empty_generator.to_json()
        parsed = json.loads(raw)
        assert parsed["services"] == []


class TestManifestFile:
    def test_to_file_creates_file(self, generator):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            generator.to_file(path)
            assert path.exists()
            content = json.loads(path.read_text())
            assert content["version"] == "1.0"
            assert len(content["services"]) == 3

    def test_to_file_creates_parent_dirs(self, generator):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "dir" / "manifest.json"
            generator.to_file(path)
            assert path.exists()

    def test_to_file_with_string_path(self, generator):
        with TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "out.json")
            generator.to_file(path)
            assert Path(path).exists()


# ---------------------------------------------------------------------------
# Serialization helper tests
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_serialize_decimal(self):
        assert _serialize(Decimal("0.005")) == "0.005"

    def test_serialize_datetime(self):
        dt = datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc)
        assert _serialize(dt) == "2026-03-19T12:00:00+00:00"

    def test_serialize_nested_dict(self):
        data = {"price": Decimal("1.5"), "items": [Decimal("2")]}
        result = _serialize(data)
        assert result == {"price": "1.5", "items": ["2"]}

    def test_json_response_produces_valid_json(self):
        data = {"value": Decimal("0.01")}
        raw = _json_response(data)
        parsed = json.loads(raw)
        assert parsed["value"] == "0.01"


# ---------------------------------------------------------------------------
# MCP Server tests — handles both MCP-available and stub cases
# ---------------------------------------------------------------------------

class TestMCPAvailableFlag:
    def test_mcp_available_is_bool(self):
        assert isinstance(MCP_AVAILABLE, bool)


class TestToolDefinitions:
    def test_tool_count(self):
        assert len(TOOL_DEFINITIONS) == 5

    def test_tool_names(self):
        names = {td["name"] for td in TOOL_DEFINITIONS}
        expected = {
            "marketplace_search",
            "marketplace_get_service",
            "marketplace_list_categories",
            "marketplace_get_agent",
            "marketplace_get_reputation",
        }
        assert names == expected

    def test_each_tool_has_description(self):
        for td in TOOL_DEFINITIONS:
            assert "description" in td
            assert len(td["description"]) > 10

    def test_each_tool_has_input_schema(self):
        for td in TOOL_DEFINITIONS:
            assert "inputSchema" in td
            assert td["inputSchema"]["type"] == "object"


class TestMCPServerCreation:
    """Test server creation — behaviour depends on whether MCP SDK is installed."""

    def test_create_server_or_raises(self, db, registry):
        if MCP_AVAILABLE:
            server = create_mcp_server(db, registry)
            assert isinstance(server, MarketplaceMCPServer)
        else:
            with pytest.raises(RuntimeError, match="MCP SDK not installed"):
                create_mcp_server(db, registry)

    def test_stub_constructor_raises_without_mcp(self, db, registry):
        if not MCP_AVAILABLE:
            with pytest.raises(RuntimeError, match="MCP SDK not installed"):
                MarketplaceMCPServer(db, registry)


# ---------------------------------------------------------------------------
# Tool handler tests (only when MCP SDK is NOT available, we test stubs;
# when it IS available, we test real handlers).
# We use a conditional approach so the test suite passes in both environments.
# ---------------------------------------------------------------------------

if not MCP_AVAILABLE:

    class TestStubServer:
        def test_stub_raises_on_init(self, db, registry):
            with pytest.raises(RuntimeError, match="MCP SDK not installed"):
                MarketplaceMCPServer(db, registry)

        def test_factory_raises(self, db, registry):
            with pytest.raises(RuntimeError, match="MCP SDK not installed"):
                create_mcp_server(db, registry)

else:

    class TestMCPServerTools:
        @pytest.fixture
        def server(self, db, populated_registry):
            return create_mcp_server(db, populated_registry)

        def test_get_tools_returns_five(self, server):
            tools = server.get_tools()
            assert len(tools) == 5

        def test_get_tools_names(self, server):
            names = {t.name for t in server.get_tools()}
            assert "marketplace_search" in names
            assert "marketplace_get_service" in names

        @pytest.mark.asyncio
        async def test_search_returns_services(self, server):
            result = await server.call_tool("marketplace_search", {})
            assert len(result) == 1
            data = json.loads(result[0].text)
            assert "services" in data
            assert data["total"] == 3

        @pytest.mark.asyncio
        async def test_search_by_category(self, server):
            result = await server.call_tool("marketplace_search", {"category": "ai"})
            data = json.loads(result[0].text)
            assert data["total"] == 2

        @pytest.mark.asyncio
        async def test_search_by_tags(self, server):
            result = await server.call_tool("marketplace_search", {"tags": ["crypto"]})
            data = json.loads(result[0].text)
            assert data["total"] == 1

        @pytest.mark.asyncio
        async def test_get_service_found(self, server, populated_registry):
            svc = populated_registry.search(query="Sentiment")[0]
            result = await server.call_tool("marketplace_get_service", {"service_id": svc.id})
            data = json.loads(result[0].text)
            assert "service" in data
            assert data["service"]["name"] == "Sentiment Analysis API"

        @pytest.mark.asyncio
        async def test_get_service_not_found(self, server):
            result = await server.call_tool("marketplace_get_service", {"service_id": "nope"})
            data = json.loads(result[0].text)
            assert "error" in data

        @pytest.mark.asyncio
        async def test_list_categories(self, server):
            result = await server.call_tool("marketplace_list_categories", {})
            data = json.loads(result[0].text)
            assert "categories" in data
            cats = {c["category"] for c in data["categories"]}
            assert "ai" in cats
            assert "data" in cats

        @pytest.mark.asyncio
        async def test_get_agent_not_found(self, server):
            result = await server.call_tool("marketplace_get_agent", {"agent_id": "missing"})
            data = json.loads(result[0].text)
            assert "error" in data

        @pytest.mark.asyncio
        async def test_get_agent_found(self, db, populated_registry):
            now = datetime.now(timezone.utc).isoformat()
            db.insert_agent({
                "agent_id": "agent-test-1",
                "display_name": "Test Bot",
                "owner_id": "owner-1",
                "created_at": now,
                "updated_at": now,
            })
            server = create_mcp_server(db, populated_registry)
            result = await server.call_tool("marketplace_get_agent", {"agent_id": "agent-test-1"})
            data = json.loads(result[0].text)
            assert "agent" in data
            assert data["agent"]["display_name"] == "Test Bot"

        @pytest.mark.asyncio
        async def test_get_reputation_requires_id(self, server):
            result = await server.call_tool("marketplace_get_reputation", {})
            data = json.loads(result[0].text)
            assert "error" in data

        @pytest.mark.asyncio
        async def test_get_reputation_by_agent(self, server):
            result = await server.call_tool(
                "marketplace_get_reputation", {"agent_id": "prov-1"}
            )
            data = json.loads(result[0].text)
            assert data["agent_id"] == "prov-1"
            assert "records" in data

        @pytest.mark.asyncio
        async def test_get_reputation_by_service(self, server, populated_registry):
            svc = populated_registry.search(query="Token")[0]
            result = await server.call_tool(
                "marketplace_get_reputation", {"service_id": svc.id}
            )
            data = json.loads(result[0].text)
            assert data["service_id"] == svc.id

        @pytest.mark.asyncio
        async def test_unknown_tool(self, server):
            result = await server.call_tool("nonexistent_tool", {})
            data = json.loads(result[0].text)
            assert "error" in data

        @pytest.mark.asyncio
        async def test_search_with_limit(self, server):
            result = await server.call_tool("marketplace_search", {"limit": 1})
            data = json.loads(result[0].text)
            assert len(data["services"]) <= 1

        @pytest.mark.asyncio
        async def test_search_with_query(self, server):
            result = await server.call_tool("marketplace_search", {"query": "Image"})
            data = json.loads(result[0].text)
            assert data["total"] == 1
            assert data["services"][0]["name"] == "Image Recognition"
