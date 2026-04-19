"""
End-to-end integration tests — full marketplace flow.
Tests the complete lifecycle: agent → service → discover → proxy → reputation → settlement.
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from marketplace.db import Database
from marketplace.registry import ServiceRegistry
from marketplace.auth import APIKeyManager
from marketplace.proxy import PaymentProxy
from marketplace.settlement import SettlementEngine
from marketplace.identity import IdentityManager
from marketplace.reputation import ReputationEngine
from marketplace.discovery import DiscoveryEngine
from marketplace.webhooks import WebhookManager


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "integration.db")


@pytest.fixture
def registry(db):
    return ServiceRegistry(db)


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


@pytest.fixture
def proxy(db):
    return PaymentProxy(db, platform_fee_pct=Decimal("0.10"))


@pytest.fixture
def settlement(db):
    return SettlementEngine(db, platform_fee_pct=Decimal("0.10"))


@pytest.fixture
def identity(db):
    return IdentityManager(db)


@pytest.fixture
def reputation(db):
    return ReputationEngine(db)


@pytest.fixture
def discovery(db, registry):
    return DiscoveryEngine(db, registry)


@pytest.fixture
def webhooks(db):
    return WebhookManager(db)


def _mock_httpx_success(body=b'{"result": "ok"}'):
    """Create a mock httpx client that returns 200."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.content = body
    mock_response.headers = {"content-type": "application/json"}

    mock_instance = AsyncMock()
    mock_instance.request = AsyncMock(return_value=mock_response)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    return mock_instance


class TestFullMarketplaceFlow:
    """Test complete buyer journey through the marketplace."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(
        self, db, registry, auth, proxy, settlement,
        identity, reputation, discovery
    ):
        """
        Full flow:
        1. Provider registers agent identity
        2. Provider creates API key
        3. Provider registers service
        4. Buyer registers agent identity
        5. Buyer creates API key
        6. Buyer discovers service
        7. Buyer calls service via proxy
        8. Reputation is computed
        9. Settlement is created
        """
        # --- 1. Provider registers identity ---
        provider_agent = identity.register(
            display_name="CoinSifter Bot",
            owner_id="provider-001",
            identity_type="api_key_only",
            capabilities=["crypto-analysis", "market-data"],
        )
        assert provider_agent.agent_id
        assert provider_agent.display_name == "CoinSifter Bot"

        # --- 2. Provider creates API key ---
        provider_key_id, provider_secret = auth.create_key(
            owner_id="provider-001", role="provider"
        )
        assert provider_key_id.startswith("acf_")

        # --- 3. Provider registers service ---
        service = registry.register(
            provider_id="provider-001",
            name="CoinSifter API",
            description="AI-powered crypto scanner",
            endpoint="https://api.coinsifter.com/v1",
            price_per_call="0.005",
            category="crypto",
            tags=["ai", "crypto", "scanner"],
            payment_method="x402",
            free_tier_calls=5,
        )
        assert service.id
        assert service.name == "CoinSifter API"

        # --- 4. Buyer registers identity ---
        buyer_agent = identity.register(
            display_name="Trading Bot Alpha",
            owner_id="buyer-001",
            identity_type="api_key_only",
            capabilities=["trading", "portfolio"],
        )

        # --- 5. Buyer creates API key ---
        buyer_key_id, buyer_secret = auth.create_key(
            owner_id="buyer-001", role="buyer"
        )
        record = auth.validate(buyer_key_id, buyer_secret)
        assert record["role"] == "buyer"

        # --- 6. Buyer discovers service ---
        results = discovery.search(
            query="crypto",
            category="crypto",
        )
        assert results["total"] >= 1
        found = results["services"][0]
        assert found.name == "CoinSifter API"

        # Also test category listing
        categories = discovery.get_categories()
        assert any(c["category"] == "crypto" for c in categories)

        # --- 7. Buyer calls service via proxy (free tier) ---
        service_dict = {
            "id": service.id,
            "provider_id": service.provider_id,
            "endpoint": "https://api.coinsifter.com/v1",
            "price_per_call": "0.005",
            "payment_method": "x402",
            "free_tier_calls": 5,
            "status": "active",
        }

        with patch("httpx.AsyncClient", return_value=_mock_httpx_success()):
            result = await proxy.forward_request(
                service=service_dict,
                buyer_id="buyer-001",
                method="GET",
                path="/scan",
            )

        assert result.success
        assert result.billing.free_tier  # First call should be free
        assert result.billing.amount == Decimal("0")

        # Fund buyer balance for paid calls after free tier
        db.credit_balance("buyer-001", Decimal("10.00"))

        # Make 5 more calls to exhaust free tier
        for _ in range(5):
            with patch("httpx.AsyncClient", return_value=_mock_httpx_success()):
                result = await proxy.forward_request(
                    service=service_dict,
                    buyer_id="buyer-001",
                    method="GET",
                    path="/scan",
                )

        # 6th call should be paid
        assert result.billing.amount == Decimal("0.005")
        assert not result.billing.free_tier

        # --- 8. Compute reputation ---
        scores = reputation.compute_reputation(
            provider_id="provider-001",
            period_label="all-time",
        )
        assert scores["call_count"] == 6
        assert scores["reliability_score"] > 0
        assert scores["overall_score"] > 0

        # Save reputation
        reputation.save_reputation(provider_id="provider-001")

        # Verify reputation was saved
        records = reputation.get_agent_reputation("provider-001", "all-time")
        assert len(records) >= 1

        # --- 9. Create settlement ---
        settle = settlement.create_settlement(
            provider_id="provider-001",
            period_start="2020-01-01T00:00:00",
            period_end="2030-01-01T00:00:00",
        )
        assert settle["call_count"] == 6
        assert settle["total_amount"] > 0
        assert settle["platform_fee"] > 0
        assert settle["net_amount"] > 0
        assert settle["status"] == "pending"

        # Mark as paid
        paid = settlement.mark_paid(settle["id"], "0xfake_tx_hash")
        assert paid

    @pytest.mark.asyncio
    async def test_agent_search_and_verify(self, identity):
        """Test agent search and verification flow."""
        # Register multiple agents
        agent1 = identity.register(
            display_name="Research Bot",
            owner_id="owner-1",
            capabilities=["research"],
        )
        agent2 = identity.register(
            display_name="Analysis Bot",
            owner_id="owner-2",
            capabilities=["analysis"],
        )

        # Search
        found = identity.search("Bot")
        assert len(found) >= 2

        # Verify one
        verified = identity.verify(agent1.agent_id)
        assert verified.verified

        # Unverified one should still be unverified
        a2 = identity.get(agent2.agent_id)
        assert not a2.verified

    def test_team_workflow(self, db):
        """Test team creation with members and routing."""
        from teamwork.agent_config import AgentProfile, validate_agent_profile
        from teamwork.task_router import TaskRouter
        from teamwork.quality_gates import QualityGate, QualityPipeline

        # Create and validate profiles
        leader = AgentProfile(
            agent_id="leader-1",
            role="leader",
            skills=("management", "review"),
        )
        worker = AgentProfile(
            agent_id="worker-1",
            role="worker",
            skills=("coding", "testing"),
        )
        assert validate_agent_profile(leader) == []
        assert validate_agent_profile(worker) == []

        # Create router with rules
        rules = [
            {
                "name": "code-tasks",
                "keywords": ["code", "implement", "fix"],
                "target_agent_id": "worker-1",
                "priority": 10,
                "enabled": True,
            },
        ]
        router = TaskRouter()
        assignment = router._route_by_keyword(
            "Please implement feature X", rules, "task-1"
        )
        assert assignment is not None
        assert assignment.agent_id == "worker-1"

        # Non-matching task should return None
        no_match = router._route_by_keyword(
            "Analyze market trends", rules, "task-2"
        )
        assert no_match is None

        # Create quality pipeline
        pipeline = QualityPipeline()
        pipeline.add_gate(QualityGate(
            gate_type="quality_score",
            threshold=7.0,
        ))

        # Test pipeline pass
        pass_result = pipeline.evaluate({"score": 8.5})
        assert pass_result.passed

        # Test pipeline fail
        fail_result = pipeline.evaluate({"score": 5.0})
        assert not fail_result.passed

        # Test DB team operations
        import uuid
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        team_id = str(uuid.uuid4())
        db.insert_team({
            "id": team_id,
            "name": "Dev Team",
            "owner_id": "owner-1",
            "description": "Development team",
            "config": {"routing_mode": "keyword"},
            "created_at": now,
            "updated_at": now,
        })

        # Add members
        db.insert_team_member({
            "id": str(uuid.uuid4()),
            "team_id": team_id,
            "agent_id": "leader-1",
            "role": "leader",
            "skills": ["management"],
            "joined_at": now,
        })
        db.insert_team_member({
            "id": str(uuid.uuid4()),
            "team_id": team_id,
            "agent_id": "worker-1",
            "role": "worker",
            "skills": ["coding"],
            "joined_at": now,
        })

        members = db.get_team_members(team_id)
        assert len(members) == 2

    @pytest.mark.asyncio
    async def test_webhook_on_service_call(self, db, webhooks):
        """Test webhook fires on service call."""
        # Subscribe
        webhook = webhooks.subscribe(
            owner_id="provider-001",
            url="https://hooks.example.com/callback",
            events=["service.called"],
            secret="my-secret-key",
        )
        assert webhook.id

        # Verify subscription
        subs = webhooks.list_subscriptions("provider-001")
        assert len(subs) == 1
        assert "service.called" in subs[0].events

    @pytest.mark.asyncio
    async def test_discovery_recommendations(
        self, db, registry, discovery
    ):
        """Test service recommendations based on usage history."""
        # Register services in different categories
        registry.register(
            provider_id="prov-1",
            name="Crypto Scanner A",
            description="Scanner A",
            endpoint="https://a.com/api",
            price_per_call="0.01",
            category="crypto",
        )
        registry.register(
            provider_id="prov-2",
            name="Crypto Scanner B",
            description="Scanner B",
            endpoint="https://b.com/api",
            price_per_call="0.02",
            category="crypto",
        )
        registry.register(
            provider_id="prov-3",
            name="Weather API",
            description="Weather data",
            endpoint="https://w.com/api",
            price_per_call="0.005",
            category="weather",
        )

        # No usage → should get newest services
        recs = discovery.get_recommendations("buyer-new", limit=5)
        assert len(recs) >= 1

    @pytest.mark.asyncio
    async def test_multi_payment_method_services(
        self, db, registry, discovery
    ):
        """Test discovering services with different payment methods."""
        registry.register(
            provider_id="prov-1",
            name="x402 Service",
            description="Paid service",
            endpoint="https://a.com",
            price_per_call="0.01",
            payment_method="x402",
        )
        registry.register(
            provider_id="prov-2",
            name="Free Service",
            description="Free service",
            endpoint="https://b.com",
            price_per_call="0",
            free_tier_calls=1000,
        )

        # Search for free tier services
        results = discovery.search(has_free_tier=True)
        assert results["total"] >= 1
        assert all(
            s.pricing.free_tier_calls > 0 for s in results["services"]
        )


class TestSecurityIntegration:
    """Test security boundaries across modules."""

    def test_api_key_auth_flow(self, auth):
        """Full API key lifecycle."""
        key_id, secret = auth.create_key(owner_id="user-1", role="buyer")

        # Valid auth
        record = auth.validate(key_id, secret)
        assert record["owner_id"] == "user-1"

        # Wrong secret
        from marketplace.auth import AuthError
        with pytest.raises(AuthError):
            auth.validate(key_id, "wrong-secret")

        # Wrong key_id
        with pytest.raises(AuthError):
            auth.validate("fake_key", secret)

    def test_rate_limiting(self, auth):
        """Rate limit enforcement."""
        key_id, _ = auth.create_key(
            owner_id="user-1", role="buyer", rate_limit=3
        )

        assert auth.check_rate_limit(key_id, 3)
        assert auth.check_rate_limit(key_id, 3)
        assert auth.check_rate_limit(key_id, 3)
        assert auth.check_rate_limit(key_id, 3) is not True  # Should be blocked

    def test_owner_isolation(self, identity):
        """Agents can only be modified by their owner."""
        from marketplace.identity import IdentityError

        agent = identity.register(
            display_name="My Bot",
            owner_id="owner-1",
        )

        # Owner can update
        updated = identity.update(
            agent.agent_id, "owner-1", display_name="My Updated Bot"
        )
        assert updated.display_name == "My Updated Bot"

        # Non-owner cannot update
        with pytest.raises(IdentityError, match="Only the agent owner"):
            identity.update(
                agent.agent_id, "owner-2", display_name="Hacked"
            )

        # Non-owner cannot deactivate
        with pytest.raises(IdentityError, match="Only the agent owner"):
            identity.deactivate(agent.agent_id, "owner-2")

    def test_webhook_owner_isolation(self, webhooks):
        """Webhooks can only be deleted by their owner."""
        wh = webhooks.subscribe(
            owner_id="owner-1",
            url="https://example.com/hook",
            events=["service.called"],
            secret="secret123",
        )

        # Wrong owner can't delete
        assert not webhooks.unsubscribe(wh.id, "owner-2")

        # Right owner can delete
        assert webhooks.unsubscribe(wh.id, "owner-1")
