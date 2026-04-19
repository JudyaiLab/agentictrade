"""Tests for Agent Identity management."""
from __future__ import annotations

import pytest
import tempfile
from pathlib import Path

from marketplace.db import Database
from marketplace.identity import IdentityManager, IdentityError


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(Path(tmp) / "test.db")


@pytest.fixture
def mgr(db):
    return IdentityManager(db)


# --- Registration ---

class TestRegister:
    def test_register_basic(self, mgr):
        agent = mgr.register("TestBot", "owner-1")
        assert agent.display_name == "TestBot"
        assert agent.owner_id == "owner-1"
        assert agent.identity_type == "api_key_only"
        assert agent.status == "active"
        assert agent.verified is False
        assert agent.reputation_score == 0.0

    def test_register_with_capabilities(self, mgr):
        agent = mgr.register(
            "DataBot",
            "owner-1",
            capabilities=["data-analysis", "web-scraping"],
        )
        assert agent.capabilities == ("data-analysis", "web-scraping")

    def test_register_with_wallet(self, mgr):
        agent = mgr.register(
            "PayBot", "owner-1",
            wallet_address="0xabc123",
        )
        assert agent.wallet_address == "0xabc123"

    def test_register_with_metadata(self, mgr):
        agent = mgr.register(
            "MetaBot", "owner-1",
            metadata={"version": "1.0"},
        )
        assert agent.metadata == {"version": "1.0"}

    def test_register_kya_jwt_type(self, mgr):
        agent = mgr.register(
            "JWTBot", "owner-1", identity_type="kya_jwt",
        )
        assert agent.identity_type == "kya_jwt"

    def test_register_did_vc_type(self, mgr):
        agent = mgr.register(
            "DIDBot", "owner-1", identity_type="did_vc",
        )
        assert agent.identity_type == "did_vc"

    def test_register_empty_name_fails(self, mgr):
        with pytest.raises(IdentityError, match="Display name"):
            mgr.register("", "owner-1")

    def test_register_whitespace_name_fails(self, mgr):
        with pytest.raises(IdentityError, match="Display name"):
            mgr.register("   ", "owner-1")

    def test_register_empty_owner_fails(self, mgr):
        with pytest.raises(IdentityError, match="Owner ID"):
            mgr.register("Bot", "")

    def test_register_invalid_type_fails(self, mgr):
        with pytest.raises(IdentityError, match="Identity type"):
            mgr.register("Bot", "owner-1", identity_type="invalid")

    def test_register_strips_whitespace(self, mgr):
        agent = mgr.register("  SpaceBot  ", "  owner-1  ")
        assert agent.display_name == "SpaceBot"
        assert agent.owner_id == "owner-1"


# --- Get ---

class TestGet:
    def test_get_existing(self, mgr):
        created = mgr.register("GetBot", "owner-1")
        fetched = mgr.get(created.agent_id)
        assert fetched is not None
        assert fetched.agent_id == created.agent_id
        assert fetched.display_name == "GetBot"

    def test_get_nonexistent(self, mgr):
        assert mgr.get("nonexistent-id") is None


# --- List ---

class TestList:
    def test_list_empty(self, mgr):
        agents = mgr.list_agents()
        assert agents == []

    def test_list_multiple(self, mgr):
        mgr.register("Bot1", "owner-1")
        mgr.register("Bot2", "owner-1")
        mgr.register("Bot3", "owner-2")
        agents = mgr.list_agents()
        assert len(agents) == 3

    def test_list_by_owner(self, mgr):
        mgr.register("Bot1", "owner-1")
        mgr.register("Bot2", "owner-2")
        agents = mgr.list_agents(owner_id="owner-1")
        assert len(agents) == 1
        assert agents[0].owner_id == "owner-1"

    def test_list_limit(self, mgr):
        for i in range(5):
            mgr.register(f"Bot{i}", "owner-1")
        agents = mgr.list_agents(limit=3)
        assert len(agents) == 3


# --- Update ---

class TestUpdate:
    def test_update_name(self, mgr):
        agent = mgr.register("OldName", "owner-1")
        updated = mgr.update(agent.agent_id, "owner-1", display_name="NewName")
        assert updated.display_name == "NewName"

    def test_update_capabilities(self, mgr):
        agent = mgr.register("Bot", "owner-1")
        updated = mgr.update(
            agent.agent_id, "owner-1",
            capabilities=["new-skill"],
        )
        assert updated.capabilities == ("new-skill",)

    def test_update_wrong_owner_fails(self, mgr):
        agent = mgr.register("Bot", "owner-1")
        with pytest.raises(IdentityError, match="owner"):
            mgr.update(agent.agent_id, "owner-2", display_name="Hacked")

    def test_update_nonexistent(self, mgr):
        result = mgr.update("nonexistent", "owner-1", display_name="X")
        assert result is None

    def test_update_empty_name_fails(self, mgr):
        agent = mgr.register("Bot", "owner-1")
        with pytest.raises(IdentityError, match="Display name"):
            mgr.update(agent.agent_id, "owner-1", display_name="")

    def test_update_invalid_status_fails(self, mgr):
        agent = mgr.register("Bot", "owner-1")
        with pytest.raises(IdentityError, match="Status"):
            mgr.update(agent.agent_id, "owner-1", status="invalid")


# --- Deactivate ---

class TestDeactivate:
    def test_deactivate(self, mgr):
        agent = mgr.register("Bot", "owner-1")
        result = mgr.deactivate(agent.agent_id, "owner-1")
        assert result is True
        fetched = mgr.get(agent.agent_id)
        assert fetched.status == "deactivated"

    def test_deactivate_wrong_owner(self, mgr):
        agent = mgr.register("Bot", "owner-1")
        with pytest.raises(IdentityError, match="owner"):
            mgr.deactivate(agent.agent_id, "owner-2")

    def test_deactivate_nonexistent(self, mgr):
        assert mgr.deactivate("nonexistent", "owner-1") is False


# --- Search ---

class TestSearch:
    def test_search_by_name(self, mgr):
        mgr.register("AlphaBot", "owner-1")
        mgr.register("BetaBot", "owner-1")
        results = mgr.search("Alpha")
        assert len(results) == 1
        assert results[0].display_name == "AlphaBot"

    def test_search_empty_query(self, mgr):
        mgr.register("Bot", "owner-1")
        results = mgr.search("")
        assert results == []

    def test_search_no_match(self, mgr):
        mgr.register("Bot", "owner-1")
        results = mgr.search("xyz123")
        assert results == []


# --- Verify ---

class TestVerify:
    def test_verify_agent(self, mgr):
        agent = mgr.register("Bot", "owner-1")
        assert agent.verified is False
        verified = mgr.verify(agent.agent_id)
        assert verified.verified is True

    def test_verify_nonexistent(self, mgr):
        result = mgr.verify("nonexistent")
        assert result is None
