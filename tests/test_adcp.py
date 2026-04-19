"""Tests for AdCP (Ad Context Protocol) seller agent."""
import json
import pytest
from unittest.mock import patch


@pytest.fixture
def client():
    """Create test client."""
    from api.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestAdCPDiscovery:
    """Test /.well-known/adagents.json discovery endpoint."""

    def test_adagents_json(self, client):
        resp = client.get("/.well-known/adagents.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "contact" in data
        assert data["contact"]["email"] == "ads@agentictrade.io"
        assert len(data["properties"]) >= 1
        assert data["properties"][0]["property_id"] == "marketplace"
        assert len(data["authorized_agents"]) >= 1
        assert "/adcp/mcp" in data["authorized_agents"][0]["url"]


class TestAdCPMCP:
    """Test AdCP MCP endpoint (JSON-RPC 2.0)."""

    def test_tools_list(self, client):
        resp = client.post("/api/v1/adcp/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["jsonrpc"] == "2.0"
        tools = data["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "get_products" in tool_names
        assert "create_media_buy" in tool_names
        assert "get_media_buy_delivery" in tool_names

    def test_get_products_catalog(self, client):
        resp = client.post("/api/v1/adcp/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "get_products",
                "arguments": {"buying_mode": "catalog"},
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        content = json.loads(data["result"]["content"][0]["text"])
        assert content["total"] == 3
        assert content["marketplace"] == "AgenticTrade"
        product_ids = [p["product_id"] for p in content["products"]]
        assert "sponsored_search_result" in product_ids
        assert "featured_provider" in product_ids
        assert "sponsored_intelligence" in product_ids

    def test_get_products_brief(self, client):
        resp = client.post("/api/v1/adcp/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "get_products",
                "arguments": {
                    "buying_mode": "brief",
                    "brief": "developer_tools",
                },
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        content = json.loads(data["result"]["content"][0]["text"])
        assert content["total"] >= 1

    def test_create_media_buy(self, client):
        resp = client.post("/api/v1/adcp/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "create_media_buy",
                "arguments": {
                    "product_id": "sponsored_search_result",
                    "pricing_option_id": "cpc_100",
                    "budget": 100.0,
                    "brand_domain": "example.com",
                    "creative_text": "Try ExampleAPI — fastest NLP service",
                    "landing_url": "https://example.com/api",
                },
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        content = json.loads(data["result"]["content"][0]["text"])
        assert "campaign_id" in content
        assert content["status"] == "active"
        assert content["budget"] == 100.0
        assert content["campaign_id"].startswith("camp_")

    def test_create_media_buy_invalid_product(self, client):
        resp = client.post("/api/v1/adcp/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "create_media_buy",
                "arguments": {
                    "product_id": "nonexistent",
                    "pricing_option_id": "cpc_100",
                    "budget": 100.0,
                    "brand_domain": "example.com",
                },
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_get_delivery_not_found(self, client):
        resp = client.post("/api/v1/adcp/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "get_media_buy_delivery",
                "arguments": {"campaign_id": "nonexistent"},
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_full_campaign_flow(self, client):
        """Test: create campaign -> log impression -> log click -> check delivery."""
        # Create campaign
        resp = client.post("/api/v1/adcp/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "create_media_buy",
                "arguments": {
                    "product_id": "featured_provider",
                    "pricing_option_id": "cpm_25",
                    "budget": 50.0,
                    "brand_domain": "testbrand.com",
                },
            },
        })
        content = json.loads(resp.json()["result"]["content"][0]["text"])
        campaign_id = content["campaign_id"]

        # Log impressions
        for _ in range(10):
            client.post("/api/v1/adcp/impression", json={
                "campaign_id": campaign_id,
                "context": "category_page",
            })

        # Log clicks
        for _ in range(2):
            client.post("/api/v1/adcp/click", json={
                "campaign_id": campaign_id,
            })

        # Check delivery
        resp = client.post("/api/v1/adcp/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "get_media_buy_delivery",
                "arguments": {"campaign_id": campaign_id},
            },
        })
        delivery = json.loads(resp.json()["result"]["content"][0]["text"])
        assert delivery["impressions"] == 10
        assert delivery["clicks"] == 2
        assert delivery["budget_spent"] > 0  # CPM billing
        assert delivery["ctr"] == 20.0  # 2/10 * 100

    def test_unknown_tool(self, client):
        resp = client.post("/api/v1/adcp/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_unknown_method(self, client):
        resp = client.post("/api/v1/adcp/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "unknown/method",
            "params": {},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data


class TestAdCPInternal:
    """Test internal AdCP endpoints."""

    def test_active_campaigns_empty(self, client):
        resp = client.get("/api/v1/adcp/active-campaigns")
        assert resp.status_code == 200
        data = resp.json()
        assert "campaigns" in data
        assert "count" in data


class TestAdServing:
    """Test ad injection into discovery/service endpoints."""

    def _create_campaign(self, client, product_id="sponsored_search_result",
                         pricing_id="cpc_100", budget=100.0,
                         brand="testads.com", creative="Test Ad"):
        """Helper: create a campaign and return campaign_id."""
        resp = client.post("/api/v1/adcp/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "create_media_buy",
                "arguments": {
                    "product_id": product_id,
                    "pricing_option_id": pricing_id,
                    "budget": budget,
                    "brand_domain": brand,
                    "creative_text": creative,
                    "landing_url": f"https://{brand}",
                },
            },
        })
        content = json.loads(resp.json()["result"]["content"][0]["text"])
        return content["campaign_id"]

    def test_ad_serving_no_campaigns(self):
        """With no active campaigns, ad_serving returns empty list."""
        from marketplace.ad_serving import get_sponsored_results
        # Clear campaigns
        from api.routes.adcp import _campaigns
        _campaigns.clear()
        results = get_sponsored_results({}, context="api_search")
        assert results == []

    def test_ad_serving_with_campaign(self, client):
        """Active campaign produces sponsored result in matching context."""
        from api.routes.adcp import _campaigns
        _campaigns.clear()

        campaign_id = self._create_campaign(client)

        from marketplace.ad_serving import get_sponsored_results
        results = get_sponsored_results({}, context="api_search")
        assert len(results) == 1
        assert results[0]["is_sponsored"] is True
        assert results[0]["disclosure"] == "Sponsored"
        assert results[0]["sponsored_by"] == "testads.com"
        assert results[0]["campaign_id"] == campaign_id

    def test_ad_serving_context_mismatch(self, client):
        """Campaign targeting api_search should not appear in homepage."""
        from api.routes.adcp import _campaigns
        _campaigns.clear()

        self._create_campaign(client)

        from marketplace.ad_serving import get_sponsored_results
        results = get_sponsored_results({}, context="homepage")
        assert results == []

    def test_ad_serving_budget_exhausted(self, client):
        """Exhausted-budget campaign should not be served."""
        from api.routes.adcp import _campaigns
        _campaigns.clear()

        campaign_id = self._create_campaign(client, budget=0.01)

        # Exhaust budget by logging many impressions (CPC doesn't bill on impression)
        # Use CPM campaign instead
        _campaigns.clear()
        campaign_id = self._create_campaign(
            client, product_id="featured_provider",
            pricing_id="cpm_25", budget=0.001,
        )
        # Manually set budget_spent >= budget
        _campaigns[campaign_id]["budget_spent"] = 1.0

        from marketplace.ad_serving import get_sponsored_results
        results = get_sponsored_results({}, context="category_page")
        assert results == []

    def test_inject_into_service_list(self, client):
        """inject_into_service_list adds sponsored entry at position 0."""
        from api.routes.adcp import _campaigns
        _campaigns.clear()

        self._create_campaign(client)

        from marketplace.ad_serving import inject_into_service_list
        services = [{"id": "svc1", "name": "Real Service"}]
        result = inject_into_service_list(services, context="api_search")
        assert len(result) == 2
        assert result[0]["is_sponsored"] is True
        assert result[1]["id"] == "svc1"

    def test_inject_does_not_mutate_original(self, client):
        """Original list should not be modified."""
        from api.routes.adcp import _campaigns
        _campaigns.clear()

        self._create_campaign(client)

        from marketplace.ad_serving import inject_into_service_list
        original = [{"id": "svc1"}]
        result = inject_into_service_list(original, context="api_search")
        assert len(original) == 1  # Not mutated
        assert len(result) == 2

    def test_impression_auto_logged(self, client):
        """Serving an ad should automatically increment impressions."""
        from api.routes.adcp import _campaigns
        _campaigns.clear()

        campaign_id = self._create_campaign(
            client, product_id="featured_provider",
            pricing_id="cpm_25", budget=100.0,
        )

        from marketplace.ad_serving import get_sponsored_results
        initial_impressions = _campaigns[campaign_id]["impressions"]
        get_sponsored_results({}, context="category_page")
        assert _campaigns[campaign_id]["impressions"] == initial_impressions + 1

    def test_cpm_budget_deducted(self, client):
        """CPM campaign should deduct rate/1000 per impression."""
        from api.routes.adcp import _campaigns
        _campaigns.clear()

        campaign_id = self._create_campaign(
            client, product_id="featured_provider",
            pricing_id="cpm_25", budget=100.0,
        )

        from marketplace.ad_serving import get_sponsored_results
        get_sponsored_results({}, context="category_page")
        # CPM rate is $25.00, so per impression = $0.025
        assert _campaigns[campaign_id]["budget_spent"] == pytest.approx(0.025)
