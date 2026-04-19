"""Tests for MCP Descriptor endpoint."""
from __future__ import annotations

import pytest
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient

from marketplace.db import Database
from marketplace.registry import ServiceRegistry
from api.main import app


@pytest.fixture
def client():
    """Create test client with temporary database."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)
        registry = ServiceRegistry(db)
        
        app.state.db = db
        app.state.registry = registry
        
        # Register test services
        registry.register(
            provider_id="test-provider",
            name="TestScanner",
            description="Test scanning service",
            endpoint="https://api.test.com/scan",
            price_per_call="0.10",
            category="crypto",
            tags=["test", "scanner"],
            payment_method="x402",
            free_tier_calls=10,
        )
        registry.register(
            provider_id="test-provider",
            name="FreeDemo",
            description="Free demo service",
            endpoint="https://api.test.com/demo",
            price_per_call="0.00",
            category="crypto",
            tags=["demo", "free"],
            payment_method="x402",
            free_tier_calls=0,
        )
        
        with TestClient(app) as c:
            yield c


def test_mcp_descriptor_returns_200(client):
    """Endpoint returns 200 OK."""
    response = client.get("/api/v1/mcp/descriptor")
    assert response.status_code == 200


def test_mcp_descriptor_schema_version(client):
    """Response contains schema_version '1.0'."""
    response = client.get("/api/v1/mcp/descriptor")
    data = response.json()
    assert data.get("schema_version") == "1.0"


def test_mcp_descriptor_has_tools(client):
    """Response contains tools array with at least 1 entry."""
    response = client.get("/api/v1/mcp/descriptor")
    data = response.json()
    assert "tools" in data
    assert isinstance(data["tools"], list)
    assert len(data["tools"]) >= 1


def test_mcp_descriptor_has_services(client):
    """Response contains services array."""
    response = client.get("/api/v1/mcp/descriptor")
    data = response.json()
    assert "services" in data
    assert isinstance(data["services"], list)


def test_mcp_descriptor_service_has_pricing(client):
    """Each service contains pricing field."""
    response = client.get("/api/v1/mcp/descriptor")
    data = response.json()
    services = data.get("services", [])
    assert len(services) >= 1
    for svc in services:
        assert "pricing" in svc
        assert "cost_usd" in svc["pricing"]
        assert "free_tier_calls" in svc["pricing"]
        assert "payment_method" in svc["pricing"]


def test_mcp_descriptor_auth_info(client):
    """Response contains auth with type 'bearer'."""
    response = client.get("/api/v1/mcp/descriptor")
    data = response.json()
    assert "auth" in data
    assert data["auth"].get("type") == "bearer"


def test_mcp_descriptor_tools_have_required_fields(client):
    """Each tool has name, description, input_schema."""
    response = client.get("/api/v1/mcp/descriptor")
    data = response.json()
    tools = data.get("tools", [])
    assert len(tools) >= 1
    for tool in tools:
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool
