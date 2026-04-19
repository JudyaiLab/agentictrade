"""Tests for the ACF Python SDK client."""
from __future__ import annotations

import pytest
import httpx

from sdk.client import ACFClient, ACFError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_response(data: dict | list, status_code: int = 200) -> httpx.Response:
    """Build an httpx.Response with JSON content."""
    return httpx.Response(
        status_code=status_code,
        json=data,
        request=httpx.Request("GET", "http://test"),
    )


def _error_response(
    status_code: int = 400, detail: str = "Bad request"
) -> httpx.Response:
    """Build an httpx error response."""
    return httpx.Response(
        status_code=status_code,
        json={"detail": detail},
        request=httpx.Request("GET", "http://test"),
    )


# ---------------------------------------------------------------------------
# Client basics
# ---------------------------------------------------------------------------

class TestClientInit:
    def test_default_base_url(self):
        client = ACFClient()
        assert client.base_url == "http://localhost:8000"
        client.close()

    def test_custom_base_url_strips_trailing_slash(self):
        client = ACFClient(base_url="https://api.example.com/")
        assert client.base_url == "https://api.example.com"
        client.close()

    def test_api_key_stored(self):
        client = ACFClient(api_key="key_1:secret_abc")
        assert client.api_key == "key_1:secret_abc"
        client.close()

    def test_context_manager(self):
        with ACFClient() as client:
            assert client.base_url == "http://localhost:8000"

    def test_headers_include_auth_when_key_set(self):
        client = ACFClient(api_key="k:s")
        headers = client._headers()
        assert headers["Authorization"] == "Bearer k:s"
        client.close()

    def test_headers_no_auth_when_no_key(self):
        client = ACFClient()
        headers = client._headers()
        assert "Authorization" not in headers
        client.close()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_acf_error_attributes(self):
        err = ACFError(404, "Not found")
        assert err.status_code == 404
        assert err.message == "Not found"
        assert "404" in str(err)
        assert "Not found" in str(err)

    def test_request_raises_on_4xx(self, monkeypatch):
        client = ACFClient()

        def mock_request(*args, **kwargs):
            return _error_response(422, "Validation error")

        monkeypatch.setattr(client._client, "request", mock_request)
        with pytest.raises(ACFError) as exc_info:
            client._request("GET", "/api/v1/test")
        assert exc_info.value.status_code == 422
        assert "Validation error" in exc_info.value.message
        client.close()

    def test_request_raises_on_5xx(self, monkeypatch):
        client = ACFClient()

        def mock_request(*args, **kwargs):
            return _error_response(500, "Internal server error")

        monkeypatch.setattr(client._client, "request", mock_request)
        with pytest.raises(ACFError) as exc_info:
            client._request("GET", "/api/v1/test")
        assert exc_info.value.status_code == 500
        client.close()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:
    def test_create_key(self, monkeypatch):
        client = ACFClient()
        expected = {
            "key_id": "acf_abc123",
            "secret": "supersecret",
            "role": "buyer",
            "rate_limit": 60,
            "message": "Save the secret",
        }

        def mock_request(*args, **kwargs):
            return _json_response(expected, 201)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.create_key("owner-1")
        assert result["key_id"] == "acf_abc123"
        assert result["secret"] == "supersecret"
        client.close()

    def test_create_key_with_rate_limit(self, monkeypatch):
        client = ACFClient()
        expected = {
            "key_id": "acf_xyz",
            "secret": "s",
            "role": "provider",
            "rate_limit": 120,
            "message": "Save the secret",
        }
        captured = {}

        def mock_request(*args, **kwargs):
            captured["json"] = kwargs.get("json")
            return _json_response(expected, 201)

        monkeypatch.setattr(client._client, "request", mock_request)
        client.create_key("owner-2", role="provider", rate_limit=120)
        assert captured["json"]["rate_limit"] == 120
        assert captured["json"]["role"] == "provider"
        client.close()

    def test_validate_key(self, monkeypatch):
        client = ACFClient(api_key="mykey:mysecret")
        expected = {
            "valid": True,
            "owner_id": "owner-1",
            "role": "buyer",
            "rate_limit": 60,
        }

        def mock_request(*args, **kwargs):
            return _json_response(expected)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.validate_key()
        assert result["valid"] is True
        client.close()

    def test_validate_key_no_api_key(self):
        client = ACFClient()
        with pytest.raises(ACFError) as exc_info:
            client.validate_key()
        assert exc_info.value.status_code == 401
        client.close()

    def test_validate_key_bad_format(self):
        client = ACFClient(api_key="no_colon_here")
        with pytest.raises(ACFError) as exc_info:
            client.validate_key()
        assert exc_info.value.status_code == 401
        client.close()


# ---------------------------------------------------------------------------
# Agent Identity
# ---------------------------------------------------------------------------

class TestAgentIdentity:
    def test_register_agent(self, monkeypatch):
        client = ACFClient(api_key="k:s")
        agent_data = {
            "agent_id": "agent-001",
            "display_name": "TestBot",
            "identity_type": "api_key_only",
            "capabilities": ["search"],
            "wallet_address": None,
            "verified": False,
            "reputation_score": 0.0,
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "owner_id": "owner-1",
        }

        def mock_request(*args, **kwargs):
            return _json_response(agent_data, 201)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.register_agent("TestBot", capabilities=["search"])
        assert result["agent_id"] == "agent-001"
        assert result["display_name"] == "TestBot"
        client.close()

    def test_get_agent(self, monkeypatch):
        client = ACFClient()
        agent_data = {
            "agent_id": "agent-002",
            "display_name": "FindMe",
            "status": "active",
        }

        def mock_request(*args, **kwargs):
            return _json_response(agent_data)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.get_agent("agent-002")
        assert result["display_name"] == "FindMe"
        client.close()

    def test_search_agents(self, monkeypatch):
        client = ACFClient()
        search_result = {
            "agents": [
                {"agent_id": "a1", "display_name": "Bot1"},
                {"agent_id": "a2", "display_name": "Bot2"},
            ],
            "count": 2,
        }

        def mock_request(*args, **kwargs):
            return _json_response(search_result)

        monkeypatch.setattr(client._client, "request", mock_request)
        results = client.search_agents("Bot")
        assert len(results) == 2
        assert results[0]["agent_id"] == "a1"
        client.close()

    def test_update_agent(self, monkeypatch):
        client = ACFClient(api_key="k:s")
        updated = {
            "agent_id": "agent-003",
            "display_name": "UpdatedBot",
            "status": "active",
        }
        captured = {}

        def mock_request(*args, **kwargs):
            captured["json"] = kwargs.get("json")
            return _json_response(updated)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.update_agent(
            "agent-003", display_name="UpdatedBot", status="active"
        )
        assert result["display_name"] == "UpdatedBot"
        assert captured["json"]["display_name"] == "UpdatedBot"
        client.close()


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

class TestServices:
    def test_register_service(self, monkeypatch):
        client = ACFClient(api_key="k:s")
        service_data = {
            "id": "svc-001",
            "name": "MyService",
            "status": "active",
            "pricing": {
                "price_per_call": "0.01",
                "currency": "USDC",
                "payment_method": "x402",
                "free_tier_calls": 10,
            },
        }
        captured = {}

        def mock_request(*args, **kwargs):
            captured["json"] = kwargs.get("json")
            return _json_response(service_data, 201)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.register_service(
            name="MyService",
            endpoint="https://api.example.com/v1",
            price_per_call="0.01",
            category="ai",
            tags=["nlp", "search"],
            free_tier_calls=10,
        )
        assert result["id"] == "svc-001"
        assert captured["json"]["category"] == "ai"
        assert captured["json"]["tags"] == ["nlp", "search"]
        client.close()

    def test_list_services(self, monkeypatch):
        client = ACFClient()
        list_data = {
            "services": [{"id": "s1"}, {"id": "s2"}],
            "count": 2,
            "offset": 0,
            "limit": 50,
        }

        def mock_request(*args, **kwargs):
            return _json_response(list_data)

        monkeypatch.setattr(client._client, "request", mock_request)
        results = client.list_services()
        assert len(results) == 2
        client.close()

    def test_list_services_with_status_filter(self, monkeypatch):
        client = ACFClient()
        captured = {}

        def mock_request(*args, **kwargs):
            captured["params"] = kwargs.get("params")
            return _json_response({"services": [], "count": 0})

        monkeypatch.setattr(client._client, "request", mock_request)
        client.list_services(status="inactive")
        assert captured["params"]["status"] == "inactive"
        client.close()

    def test_get_service(self, monkeypatch):
        client = ACFClient()
        service = {"id": "svc-99", "name": "TestSvc", "status": "active"}

        def mock_request(*args, **kwargs):
            return _json_response(service)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.get_service("svc-99")
        assert result["name"] == "TestSvc"
        client.close()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

class TestDiscovery:
    def test_search(self, monkeypatch):
        client = ACFClient()
        discover_result = {
            "services": [{"id": "s1"}],
            "total": 1,
            "offset": 0,
            "limit": 50,
        }

        def mock_request(*args, **kwargs):
            return _json_response(discover_result)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.search(query="nlp", category="ai")
        assert result["total"] == 1
        assert len(result["services"]) == 1
        client.close()

    def test_search_with_price_filter(self, monkeypatch):
        client = ACFClient()
        captured = {}

        def mock_request(*args, **kwargs):
            captured["params"] = kwargs.get("params")
            return _json_response(
                {"services": [], "total": 0, "offset": 0, "limit": 50}
            )

        monkeypatch.setattr(client._client, "request", mock_request)
        client.search(min_price="0.01", max_price="1.00", has_free_tier=True)
        assert captured["params"]["min_price"] == "0.01"
        assert captured["params"]["max_price"] == "1.00"
        assert captured["params"]["has_free_tier"] is True
        client.close()

    def test_get_categories(self, monkeypatch):
        client = ACFClient()
        cats = {"categories": [{"name": "ai", "count": 5}]}

        def mock_request(*args, **kwargs):
            return _json_response(cats)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.get_categories()
        assert len(result) == 1
        assert result[0]["name"] == "ai"
        client.close()

    def test_get_trending(self, monkeypatch):
        client = ACFClient()
        trending_data = {
            "trending": [
                {
                    "service": {"id": "s1"},
                    "call_count": 1000,
                    "avg_latency_ms": 50,
                }
            ],
            "count": 1,
        }

        def mock_request(*args, **kwargs):
            return _json_response(trending_data)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.get_trending(limit=5)
        assert len(result) == 1
        assert result[0]["call_count"] == 1000
        client.close()

    def test_get_recommendations(self, monkeypatch):
        client = ACFClient()
        recs = {"recommendations": [{"id": "s1"}, {"id": "s2"}], "count": 2}

        def mock_request(*args, **kwargs):
            return _json_response(recs)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.get_recommendations("agent-001", limit=3)
        assert len(result) == 2
        client.close()


# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------

class TestProxy:
    def test_call_service_get(self, monkeypatch):
        client = ACFClient(api_key="k:s")
        response_data = {"result": "ok"}

        def mock_request(*args, **kwargs):
            return _json_response(response_data)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.call_service("svc-001", method="GET", path="/status")
        assert result["result"] == "ok"
        client.close()

    def test_call_service_post_with_body(self, monkeypatch):
        client = ACFClient(api_key="k:s")
        captured = {}

        def mock_request(*args, **kwargs):
            captured["method"] = args[0] if args else kwargs.get("method")
            captured["json"] = kwargs.get("json")
            return _json_response({"processed": True})

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.call_service(
            "svc-002",
            method="POST",
            path="/analyze",
            body={"text": "hello"},
        )
        assert result["processed"] is True
        assert captured["method"] == "POST"
        assert captured["json"] == {"text": "hello"}
        client.close()

    def test_call_service_strips_leading_slash(self, monkeypatch):
        client = ACFClient(api_key="k:s")
        captured = {}

        def mock_request(method, url, **kwargs):
            captured["url"] = url
            return _json_response({"ok": True})

        monkeypatch.setattr(client._client, "request", mock_request)
        client.call_service("svc-003", path="/data/fetch")
        assert captured["url"] == "/api/v1/proxy/svc-003/data/fetch"
        client.close()

    def test_call_service_error(self, monkeypatch):
        client = ACFClient(api_key="k:s")

        def mock_request(*args, **kwargs):
            return _error_response(404, "Service not found")

        monkeypatch.setattr(client._client, "request", mock_request)
        with pytest.raises(ACFError) as exc_info:
            client.call_service("nonexistent")
        assert exc_info.value.status_code == 404
        client.close()


# ---------------------------------------------------------------------------
# Reputation
# ---------------------------------------------------------------------------

class TestReputation:
    def test_get_agent_reputation(self, monkeypatch):
        client = ACFClient()
        rep = {
            "agent_id": "agent-001",
            "period": "all-time",
            "records": [{"metric": "uptime", "score": 99.5}],
        }

        def mock_request(*args, **kwargs):
            return _json_response(rep)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.get_agent_reputation("agent-001")
        assert result["agent_id"] == "agent-001"
        client.close()

    def test_get_agent_reputation_with_period(self, monkeypatch):
        client = ACFClient()
        captured = {}

        def mock_request(*args, **kwargs):
            captured["params"] = kwargs.get("params")
            return _json_response({"agent_id": "a1", "period": "2026-03"})

        monkeypatch.setattr(client._client, "request", mock_request)
        client.get_agent_reputation("a1", period="2026-03")
        assert captured["params"]["period"] == "2026-03"
        client.close()

    def test_get_leaderboard(self, monkeypatch):
        client = ACFClient()
        lb = {
            "leaderboard": [
                {"agent_id": "a1", "score": 95.0},
                {"agent_id": "a2", "score": 90.0},
            ],
            "count": 2,
        }

        def mock_request(*args, **kwargs):
            return _json_response(lb)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.get_leaderboard(limit=5)
        assert len(result) == 2
        assert result[0]["score"] == 95.0
        client.close()


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

class TestTeams:
    def test_create_team(self, monkeypatch):
        client = ACFClient(api_key="k:s")
        team = {"id": "team-001", "name": "My Team", "owner_id": "owner-1"}

        def mock_request(*args, **kwargs):
            return _json_response(team, 201)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.create_team("My Team", description="Test team")
        assert result["id"] == "team-001"
        client.close()

    def test_add_member(self, monkeypatch):
        client = ACFClient(api_key="k:s")
        member = {
            "id": "member-001",
            "team_id": "team-001",
            "agent_id": "agent-001",
        }
        captured = {}

        def mock_request(*args, **kwargs):
            captured["json"] = kwargs.get("json")
            return _json_response(member, 201)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.add_member(
            "team-001", "agent-001", role="leader", skills=["python"]
        )
        assert result["agent_id"] == "agent-001"
        assert captured["json"]["role"] == "leader"
        assert captured["json"]["skills"] == ["python"]
        client.close()

    def test_add_routing_rule(self, monkeypatch):
        client = ACFClient(api_key="k:s")
        rule = {"id": "rule-001", "name": "NLP Router"}
        captured = {}

        def mock_request(*args, **kwargs):
            captured["json"] = kwargs.get("json")
            return _json_response(rule, 201)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.add_routing_rule(
            "team-001",
            name="NLP Router",
            keywords=["nlp", "text"],
            target_agent_id="agent-002",
            priority=20,
        )
        assert result["name"] == "NLP Router"
        assert captured["json"]["priority"] == 20
        assert captured["json"]["keywords"] == ["nlp", "text"]
        client.close()


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

class TestWebhooks:
    def test_subscribe(self, monkeypatch):
        client = ACFClient(api_key="k:s")
        webhook = {
            "id": "wh-001",
            "owner_id": "owner-1",
            "url": "https://example.com/hook",
            "events": ["service.called"],
            "active": True,
            "created_at": "2026-01-01T00:00:00Z",
        }
        captured = {}

        def mock_request(*args, **kwargs):
            captured["json"] = kwargs.get("json")
            return _json_response(webhook, 201)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.subscribe(
            "https://example.com/hook", ["service.called"], "my_secret"
        )
        assert result["id"] == "wh-001"
        assert captured["json"]["secret"] == "my_secret"
        client.close()

    def test_list_subscriptions(self, monkeypatch):
        client = ACFClient(api_key="k:s")
        data = {
            "webhooks": [{"id": "wh-1"}, {"id": "wh-2"}],
            "count": 2,
        }

        def mock_request(*args, **kwargs):
            return _json_response(data)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.list_subscriptions()
        assert len(result) == 2
        client.close()

    def test_unsubscribe(self, monkeypatch):
        client = ACFClient(api_key="k:s")

        def mock_request(*args, **kwargs):
            return _json_response({"status": "deleted", "webhook_id": "wh-1"})

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.unsubscribe("wh-1")
        assert result is True
        client.close()


# ---------------------------------------------------------------------------
# Settlements
# ---------------------------------------------------------------------------

class TestSettlements:
    def test_create_settlement(self, monkeypatch):
        client = ACFClient(api_key="admin_key:secret")
        settlement = {
            "id": "stl-001",
            "provider_id": "prov-1",
            "total_amount": "100.00",
            "platform_fee": "10.00",
            "net_amount": "90.00",
            "call_count": 500,
            "status": "pending",
        }
        captured = {}

        def mock_request(*args, **kwargs):
            captured["json"] = kwargs.get("json")
            return _json_response(settlement, 201)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.create_settlement(
            provider_id="prov-1",
            period_start="2026-01-01T00:00:00Z",
            period_end="2026-01-31T23:59:59Z",
        )
        assert result["id"] == "stl-001"
        assert result["net_amount"] == "90.00"
        assert captured["json"]["provider_id"] == "prov-1"
        client.close()

    def test_list_settlements(self, monkeypatch):
        client = ACFClient(api_key="k:s")
        data = {
            "settlements": [
                {"id": "stl-1", "status": "pending"},
                {"id": "stl-2", "status": "completed"},
            ],
            "count": 2,
        }

        def mock_request(*args, **kwargs):
            return _json_response(data)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.list_settlements()
        assert len(result) == 2
        assert result[1]["status"] == "completed"
        client.close()


# ---------------------------------------------------------------------------
# Provider Self-Service
# ---------------------------------------------------------------------------

class TestProviderDashboardSDK:
    def test_get_dashboard(self, monkeypatch):
        client = ACFClient(api_key="prov:secret")
        dashboard = {
            "provider_id": "prov-1",
            "total_services": 3,
            "total_calls": 1500,
            "total_revenue": 750.00,
            "total_settled": 600.00,
            "pending_settlement": 150.00,
        }

        def mock_request(*args, **kwargs):
            return _json_response(dashboard)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.provider_dashboard()
        assert result["provider_id"] == "prov-1"
        assert result["total_services"] == 3
        assert result["total_revenue"] == 750.00
        client.close()


class TestProviderServicesSDK:
    def test_list_provider_services(self, monkeypatch):
        client = ACFClient(api_key="prov:secret")
        data = {
            "services": [
                {"id": "s1", "name": "SvcA", "total_calls": 100},
                {"id": "s2", "name": "SvcB", "total_calls": 50},
            ]
        }

        def mock_request(*args, **kwargs):
            return _json_response(data)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.provider_services()
        assert len(result) == 2
        assert result[0]["name"] == "SvcA"
        client.close()

    def test_service_analytics(self, monkeypatch):
        client = ACFClient(api_key="prov:secret")
        analytics = {
            "service_id": "svc-001",
            "service_name": "MySvc",
            "total_calls": 500,
            "total_revenue": 250.00,
            "avg_latency_ms": 45.3,
            "success_rate": 99.2,
            "unique_buyers": 12,
            "daily": [{"date": "2026-03-19", "calls": 30, "revenue": 15.00}],
        }

        def mock_request(*args, **kwargs):
            return _json_response(analytics)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.service_analytics("svc-001")
        assert result["total_calls"] == 500
        assert result["success_rate"] == 99.2
        assert len(result["daily"]) == 1
        client.close()


class TestProviderEarningsSDK:
    def test_get_earnings(self, monkeypatch):
        client = ACFClient(api_key="prov:secret")
        earnings = {
            "total_earned": 1000.00,
            "total_settled": 800.00,
            "pending_settlement": 200.00,
            "settlements": [
                {
                    "id": "stl-1",
                    "total_amount": 500.00,
                    "platform_fee": 50.00,
                    "net_amount": 450.00,
                    "status": "completed",
                }
            ],
        }

        def mock_request(*args, **kwargs):
            return _json_response(earnings)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.provider_earnings()
        assert result["total_earned"] == 1000.00
        assert result["pending_settlement"] == 200.00
        assert len(result["settlements"]) == 1
        client.close()


class TestProviderKeysSDK:
    def test_list_keys(self, monkeypatch):
        client = ACFClient(api_key="prov:secret")
        data = {
            "keys": [
                {"key_id": "k1", "role": "provider", "rate_limit": 60},
                {"key_id": "k2", "role": "provider", "rate_limit": 120},
            ]
        }

        def mock_request(*args, **kwargs):
            return _json_response(data)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.provider_keys()
        assert len(result) == 2
        assert result[0]["key_id"] == "k1"
        client.close()

    def test_revoke_key(self, monkeypatch):
        client = ACFClient(api_key="prov:secret")
        captured = {}

        def mock_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            return _json_response({"status": "revoked", "key_id": "k1"})

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.revoke_provider_key("k1")
        assert result is True
        assert captured["method"] == "DELETE"
        client.close()


class TestServiceTestSDK:
    def test_test_endpoint(self, monkeypatch):
        client = ACFClient(api_key="prov:secret")
        test_result = {
            "service_id": "svc-001",
            "endpoint": "https://api.example.com",
            "reachable": True,
            "latency_ms": 120,
            "status_code": 200,
            "error": "",
        }

        def mock_request(*args, **kwargs):
            return _json_response(test_result)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.test_service("svc-001")
        assert result["reachable"] is True
        assert result["latency_ms"] == 120
        client.close()

    def test_test_endpoint_unreachable(self, monkeypatch):
        client = ACFClient(api_key="prov:secret")
        test_result = {
            "service_id": "svc-002",
            "endpoint": "https://dead.example.com",
            "reachable": False,
            "latency_ms": 10000,
            "status_code": 0,
            "error": "Connection timed out",
        }

        def mock_request(*args, **kwargs):
            return _json_response(test_result)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.test_service("svc-002")
        assert result["reachable"] is False
        assert result["error"] == "Connection timed out"
        client.close()


class TestOnboardingSDK:
    def test_get_onboarding(self, monkeypatch):
        client = ACFClient(api_key="prov:secret")
        onboarding = {
            "provider_id": "prov-1",
            "steps": {
                "create_api_key": {"completed": True, "label": "Create API key"},
                "register_service": {"completed": True, "label": "Register your first service"},
                "activate_service": {"completed": False, "label": "Activate a service"},
                "first_traffic": {"completed": False, "label": "Receive first API call"},
                "first_settlement": {"completed": False, "label": "Complete first settlement"},
            },
            "completed_steps": 2,
            "total_steps": 5,
            "completion_pct": 40.0,
        }

        def mock_request(*args, **kwargs):
            return _json_response(onboarding)

        monkeypatch.setattr(client._client, "request", mock_request)
        result = client.provider_onboarding()
        assert result["completed_steps"] == 2
        assert result["completion_pct"] == 40.0
        assert result["steps"]["create_api_key"]["completed"] is True
        client.close()
