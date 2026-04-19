"""Tests for Agent Provider registration and lifecycle management."""
from __future__ import annotations

import json
import pytest
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from marketplace.db import Database
from marketplace.identity import IdentityManager
from marketplace.agent_provider import AgentProviderManager, AgentProviderError


VALID_WALLET = "0x" + "a" * 40
VALID_DID = "did:web:example.com"
VALID_EMAIL = "test@example.com"


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(Path(tmp) / "test.db")


@pytest.fixture
def identity_mgr(db):
    return IdentityManager(db)


@pytest.fixture
def mgr(db):
    return AgentProviderManager(db)


@pytest.fixture
def agent(identity_mgr):
    """Create a default active agent identity for provider tests."""
    return identity_mgr.register("TestBot", "owner-1")


def _register_provider(mgr, identity_mgr, **overrides):
    """Helper to register an agent identity and then a provider in one call."""
    name = overrides.pop("display_name", "HelperBot")
    owner = overrides.pop("owner_id", "owner-helper")
    agent = identity_mgr.register(name, owner)
    defaults = {
        "agent_id": agent.agent_id,
        "owner_email": VALID_EMAIL,
        "wallet_address": VALID_WALLET,
        "did": VALID_DID,
    }
    defaults.update(overrides)
    return mgr.register(**defaults)


# ---------------------------------------------------------------------------
# 1. Registration
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_basic(self, mgr, agent):
        provider = mgr.register(
            agent_id=agent.agent_id,
            owner_email=VALID_EMAIL,
            wallet_address=VALID_WALLET,
            did=VALID_DID,
        )
        assert provider["agent_id"] == agent.agent_id
        assert provider["owner_email"] == VALID_EMAIL
        assert provider["wallet_address"] == VALID_WALLET
        assert provider["did"] == VALID_DID
        assert provider["status"] == "pending_review"
        assert provider["reputation_score"] == 0.0
        assert provider["daily_tx_cap"] == 500.0
        assert provider["daily_tx_used"] == 0.0
        assert provider["total_reports"] == 0

    def test_register_with_declaration(self, mgr, agent):
        provider = mgr.register(
            agent_id=agent.agent_id,
            owner_email=VALID_EMAIL,
            wallet_address=VALID_WALLET,
            did=VALID_DID,
            declaration="I comply with all rules.",
        )
        assert provider["declaration"] == "I comply with all rules."

    def test_register_default_declaration_empty(self, mgr, agent):
        provider = mgr.register(
            agent_id=agent.agent_id,
            owner_email=VALID_EMAIL,
            wallet_address=VALID_WALLET,
            did=VALID_DID,
        )
        assert provider["declaration"] == ""

    def test_register_returns_uuid_id(self, mgr, agent):
        provider = mgr.register(
            agent_id=agent.agent_id,
            owner_email=VALID_EMAIL,
            wallet_address=VALID_WALLET,
            did=VALID_DID,
        )
        # UUID format: 8-4-4-4-12 hex chars
        assert len(provider["id"]) == 36
        assert provider["id"].count("-") == 4

    def test_register_sets_probation_ends_at(self, mgr, agent):
        provider = mgr.register(
            agent_id=agent.agent_id,
            owner_email=VALID_EMAIL,
            wallet_address=VALID_WALLET,
            did=VALID_DID,
        )
        probation_end = datetime.fromisoformat(provider["probation_ends_at"])
        created = datetime.fromisoformat(provider["created_at"])
        delta = probation_end - created
        assert 29 <= delta.days <= 31  # approximately 30 days

    def test_register_duplicate_fails(self, mgr, agent):
        mgr.register(
            agent_id=agent.agent_id,
            owner_email=VALID_EMAIL,
            wallet_address=VALID_WALLET,
            did=VALID_DID,
        )
        with pytest.raises(AgentProviderError, match="already registered"):
            mgr.register(
                agent_id=agent.agent_id,
                owner_email=VALID_EMAIL,
                wallet_address=VALID_WALLET,
                did=VALID_DID,
            )

    def test_register_missing_agent_identity_fails(self, mgr):
        with pytest.raises(AgentProviderError, match="does not exist"):
            mgr.register(
                agent_id="nonexistent-agent-id",
                owner_email=VALID_EMAIL,
                wallet_address=VALID_WALLET,
                did=VALID_DID,
            )

    def test_register_deactivated_agent_fails(self, mgr, identity_mgr):
        agent = identity_mgr.register("DeadBot", "owner-1")
        identity_mgr.deactivate(agent.agent_id, "owner-1")
        with pytest.raises(AgentProviderError, match="not active"):
            mgr.register(
                agent_id=agent.agent_id,
                owner_email=VALID_EMAIL,
                wallet_address=VALID_WALLET,
                did=VALID_DID,
            )

    def test_register_empty_agent_id_fails(self, mgr):
        with pytest.raises(AgentProviderError, match="agent_id is required"):
            mgr.register(
                agent_id="",
                owner_email=VALID_EMAIL,
                wallet_address=VALID_WALLET,
                did=VALID_DID,
            )

    def test_register_whitespace_agent_id_fails(self, mgr):
        with pytest.raises(AgentProviderError, match="agent_id is required"):
            mgr.register(
                agent_id="   ",
                owner_email=VALID_EMAIL,
                wallet_address=VALID_WALLET,
                did=VALID_DID,
            )

    def test_register_invalid_email_fails(self, mgr, agent):
        with pytest.raises(AgentProviderError, match="owner_email format is invalid"):
            mgr.register(
                agent_id=agent.agent_id,
                owner_email="not-an-email",
                wallet_address=VALID_WALLET,
                did=VALID_DID,
            )

    def test_register_empty_email_fails(self, mgr, agent):
        with pytest.raises(AgentProviderError, match="owner_email is required"):
            mgr.register(
                agent_id=agent.agent_id,
                owner_email="",
                wallet_address=VALID_WALLET,
                did=VALID_DID,
            )

    def test_register_invalid_wallet_fails(self, mgr, agent):
        with pytest.raises(AgentProviderError, match="wallet_address must match"):
            mgr.register(
                agent_id=agent.agent_id,
                owner_email=VALID_EMAIL,
                wallet_address="bad-wallet",
                did=VALID_DID,
            )

    def test_register_invalid_did_fails(self, mgr, agent):
        with pytest.raises(AgentProviderError, match="did must match"):
            mgr.register(
                agent_id=agent.agent_id,
                owner_email=VALID_EMAIL,
                wallet_address=VALID_WALLET,
                did="not:a:valid:did",
            )

    def test_register_email_normalized_lowercase(self, mgr, agent):
        provider = mgr.register(
            agent_id=agent.agent_id,
            owner_email="  TEST@Example.COM  ",
            wallet_address=VALID_WALLET,
            did=VALID_DID,
        )
        assert provider["owner_email"] == "test@example.com"

    def test_register_agent_id_stripped(self, mgr, agent):
        provider = mgr.register(
            agent_id=f"  {agent.agent_id}  ",
            owner_email=VALID_EMAIL,
            wallet_address=VALID_WALLET,
            did=VALID_DID,
        )
        assert provider["agent_id"] == agent.agent_id


# ---------------------------------------------------------------------------
# 2. Activate
# ---------------------------------------------------------------------------

class TestActivate:
    def test_activate_pending_provider(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        assert provider["status"] == "pending_review"

        activated = mgr.activate(provider["id"])
        assert activated["status"] == "active"

    def test_activate_already_active_is_noop(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        activated = mgr.activate(provider["id"])
        assert activated["status"] == "active"

        # Activate again -- should return current state without error
        reactivated = mgr.activate(provider["id"])
        assert reactivated["status"] == "active"

    def test_activate_updates_timestamp(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        original_updated = provider["updated_at"]

        activated = mgr.activate(provider["id"])
        assert activated["updated_at"] >= original_updated

    def test_activate_not_found_raises(self, mgr):
        with pytest.raises(AgentProviderError, match="not found"):
            mgr.activate("nonexistent-provider-id")

    def test_activate_empty_id_raises(self, mgr):
        with pytest.raises(AgentProviderError, match="agent_provider_id is required"):
            mgr.activate("")

    def test_activate_suspended_provider(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        mgr.activate(provider["id"])
        mgr.suspend(provider["id"], "test suspension")

        reactivated = mgr.activate(provider["id"])
        assert reactivated["status"] == "active"


# ---------------------------------------------------------------------------
# 3. Suspend
# ---------------------------------------------------------------------------

class TestSuspend:
    def test_suspend_with_reason(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        mgr.activate(provider["id"])

        suspended = mgr.suspend(provider["id"], "Violated terms")
        assert suspended["status"] == "suspended"
        assert suspended["metadata"]["suspension_reason"] == "Violated terms"
        assert "suspended_at" in suspended["metadata"]

    def test_suspend_empty_reason_fails(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        with pytest.raises(AgentProviderError, match="Suspension reason is required"):
            mgr.suspend(provider["id"], "")

    def test_suspend_whitespace_reason_fails(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        with pytest.raises(AgentProviderError, match="Suspension reason is required"):
            mgr.suspend(provider["id"], "   ")

    def test_suspend_not_found_raises(self, mgr):
        with pytest.raises(AgentProviderError, match="not found"):
            mgr.suspend("nonexistent-id", "Some reason")

    def test_suspend_reason_stripped(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        suspended = mgr.suspend(provider["id"], "  Trimmed reason  ")
        assert suspended["metadata"]["suspension_reason"] == "Trimmed reason"

    def test_suspend_preserves_existing_metadata(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        # Manually inject some existing metadata
        mgr.db.update_agent_provider(
            provider["id"],
            {"metadata": json.dumps({"custom_key": "custom_value"})},
        )

        suspended = mgr.suspend(provider["id"], "Bad behavior")
        assert suspended["metadata"]["custom_key"] == "custom_value"
        assert suspended["metadata"]["suspension_reason"] == "Bad behavior"

    def test_suspend_pending_provider(self, mgr, identity_mgr):
        """Can suspend a provider even in pending_review status."""
        provider = _register_provider(mgr, identity_mgr)
        assert provider["status"] == "pending_review"

        suspended = mgr.suspend(provider["id"], "Fraud detected during review")
        assert suspended["status"] == "suspended"


# ---------------------------------------------------------------------------
# 4. Daily Transaction Limit
# ---------------------------------------------------------------------------

class TestDailyTxLimit:
    def test_within_cap(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        assert mgr.check_daily_tx_limit(provider["id"], 100.0) is True

    def test_at_exact_cap(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        # Default cap is 500.0
        assert mgr.check_daily_tx_limit(provider["id"], 500.0) is True

    def test_exceeds_cap(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        assert mgr.check_daily_tx_limit(provider["id"], 501.0) is False

    def test_accumulated_exceeds_cap(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        mgr.record_transaction(provider["id"], 300.0)
        assert mgr.check_daily_tx_limit(provider["id"], 200.0) is True
        assert mgr.check_daily_tx_limit(provider["id"], 201.0) is False

    def test_record_transaction_increments_used(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        mgr.record_transaction(provider["id"], 50.0)
        mgr.record_transaction(provider["id"], 75.0)

        updated = mgr.db.get_agent_provider(provider["id"])
        assert updated["daily_tx_used"] == 125.0

    def test_record_transaction_not_found_raises(self, mgr):
        with pytest.raises(AgentProviderError, match="not found"):
            mgr.record_transaction("nonexistent-id", 10.0)

    def test_check_daily_tx_limit_not_found_raises(self, mgr):
        with pytest.raises(AgentProviderError, match="not found"):
            mgr.check_daily_tx_limit("nonexistent-id", 10.0)

    def test_auto_reset_on_new_day(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        mgr.record_transaction(provider["id"], 400.0)

        # Verify it used 400
        assert mgr.check_daily_tx_limit(provider["id"], 101.0) is False

        # Simulate yesterday's reset timestamp
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        mgr.db.update_agent_provider(
            provider["id"],
            {"daily_tx_reset_at": yesterday},
        )

        # After auto-reset, full cap should be available
        assert mgr.check_daily_tx_limit(provider["id"], 500.0) is True

    def test_record_transaction_auto_reset_on_new_day(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        mgr.record_transaction(provider["id"], 400.0)

        # Simulate yesterday's reset timestamp
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        mgr.db.update_agent_provider(
            provider["id"],
            {"daily_tx_reset_at": yesterday},
        )

        # Record a new transaction -- should reset and only count the new one
        mgr.record_transaction(provider["id"], 50.0)
        updated = mgr.db.get_agent_provider(provider["id"])
        assert updated["daily_tx_used"] == 50.0

    def test_zero_amount_within_cap(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        assert mgr.check_daily_tx_limit(provider["id"], 0.0) is True


# ---------------------------------------------------------------------------
# 5. Fast Track Eligibility
# ---------------------------------------------------------------------------

class TestFastTrack:
    def _make_eligible_provider(self, mgr, identity_mgr):
        """Create a provider that meets all fast-track criteria."""
        provider = _register_provider(mgr, identity_mgr)
        mgr.activate(provider["id"])

        # Set reputation >= 80
        mgr.db.update_agent_provider(
            provider["id"],
            {"reputation_score": 85.0},
        )

        # Set probation_ends_at to the past
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        mgr.db.update_agent_provider(
            provider["id"],
            {"probation_ends_at": past},
        )

        return provider

    def test_eligible_all_conditions_met(self, mgr, identity_mgr):
        provider = self._make_eligible_provider(mgr, identity_mgr)
        assert mgr.is_fast_track_eligible(provider["id"]) is True

    def test_not_eligible_low_reputation(self, mgr, identity_mgr):
        provider = self._make_eligible_provider(mgr, identity_mgr)
        mgr.db.update_agent_provider(
            provider["id"],
            {"reputation_score": 79.9},
        )
        assert mgr.is_fast_track_eligible(provider["id"]) is False

    def test_eligible_at_exact_threshold(self, mgr, identity_mgr):
        provider = self._make_eligible_provider(mgr, identity_mgr)
        mgr.db.update_agent_provider(
            provider["id"],
            {"reputation_score": 80.0},
        )
        assert mgr.is_fast_track_eligible(provider["id"]) is True

    def test_not_eligible_still_in_probation(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        mgr.activate(provider["id"])
        mgr.db.update_agent_provider(
            provider["id"],
            {"reputation_score": 90.0},
        )
        # Default probation_ends_at is ~30 days from now -- still in probation
        assert mgr.is_fast_track_eligible(provider["id"]) is False

    def test_not_eligible_not_active(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        # Status is pending_review, not active
        mgr.db.update_agent_provider(
            provider["id"],
            {"reputation_score": 90.0},
        )
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        mgr.db.update_agent_provider(
            provider["id"],
            {"probation_ends_at": past},
        )
        assert mgr.is_fast_track_eligible(provider["id"]) is False

    def test_not_eligible_suspended(self, mgr, identity_mgr):
        provider = self._make_eligible_provider(mgr, identity_mgr)
        mgr.suspend(provider["id"], "test suspension")
        assert mgr.is_fast_track_eligible(provider["id"]) is False

    def test_not_found_raises(self, mgr):
        with pytest.raises(AgentProviderError, match="not found"):
            mgr.is_fast_track_eligible("nonexistent-id")


# ---------------------------------------------------------------------------
# 6. Validation (static methods)
# ---------------------------------------------------------------------------

class TestValidation:
    # --- Wallet address ---

    def test_wallet_valid_lowercase(self):
        assert AgentProviderManager.validate_wallet_address("0x" + "a" * 40) is True

    def test_wallet_valid_uppercase(self):
        assert AgentProviderManager.validate_wallet_address("0x" + "A" * 40) is True

    def test_wallet_valid_mixed_case(self):
        assert AgentProviderManager.validate_wallet_address("0x" + "aB1c2D" * 6 + "aB1c") is True

    def test_wallet_valid_all_digits(self):
        assert AgentProviderManager.validate_wallet_address("0x" + "1" * 40) is True

    def test_wallet_too_short(self):
        assert AgentProviderManager.validate_wallet_address("0x" + "a" * 39) is False

    def test_wallet_too_long(self):
        assert AgentProviderManager.validate_wallet_address("0x" + "a" * 41) is False

    def test_wallet_missing_0x_prefix(self):
        assert AgentProviderManager.validate_wallet_address("a" * 42) is False

    def test_wallet_invalid_chars(self):
        assert AgentProviderManager.validate_wallet_address("0x" + "g" * 40) is False

    def test_wallet_empty_string(self):
        assert AgentProviderManager.validate_wallet_address("") is False

    def test_wallet_none(self):
        assert AgentProviderManager.validate_wallet_address(None) is False

    def test_wallet_uppercase_prefix(self):
        assert AgentProviderManager.validate_wallet_address("0X" + "a" * 40) is False

    # --- DID ---

    def test_did_valid_web(self):
        assert AgentProviderManager.validate_did("did:web:example.com") is True

    def test_did_valid_key(self):
        assert AgentProviderManager.validate_did("did:key:z6MkhaXgBZD") is True

    def test_did_valid_ethr(self):
        assert AgentProviderManager.validate_did("did:ethr:0x1234abcd") is True

    def test_did_valid_with_colons_in_id(self):
        assert AgentProviderManager.validate_did("did:web:example.com:path:sub") is True

    def test_did_valid_with_dots_dashes_underscores(self):
        assert AgentProviderManager.validate_did("did:ion:long_id-with.dots") is True

    def test_did_missing_prefix(self):
        assert AgentProviderManager.validate_did("web:example.com") is False

    def test_did_uppercase_method(self):
        assert AgentProviderManager.validate_did("did:Web:example.com") is False

    def test_did_empty_method(self):
        assert AgentProviderManager.validate_did("did::example.com") is False

    def test_did_empty_id(self):
        assert AgentProviderManager.validate_did("did:web:") is False

    def test_did_empty_string(self):
        assert AgentProviderManager.validate_did("") is False

    def test_did_none(self):
        assert AgentProviderManager.validate_did(None) is False

    def test_did_spaces_invalid(self):
        assert AgentProviderManager.validate_did("did:web:has space") is False

    # --- Email (tested via register, but covers edge cases) ---

    def test_email_valid_basic(self, mgr, agent):
        """Valid email does not raise during register."""
        provider = mgr.register(
            agent_id=agent.agent_id,
            owner_email="alice@domain.co",
            wallet_address=VALID_WALLET,
            did=VALID_DID,
        )
        assert provider["owner_email"] == "alice@domain.co"

    def test_email_no_at_sign_fails(self, mgr, agent):
        with pytest.raises(AgentProviderError, match="owner_email format is invalid"):
            mgr.register(
                agent_id=agent.agent_id,
                owner_email="nodomain",
                wallet_address=VALID_WALLET,
                did=VALID_DID,
            )

    def test_email_no_domain_fails(self, mgr, agent):
        with pytest.raises(AgentProviderError, match="owner_email format is invalid"):
            mgr.register(
                agent_id=agent.agent_id,
                owner_email="user@",
                wallet_address=VALID_WALLET,
                did=VALID_DID,
            )


# ---------------------------------------------------------------------------
# 7. Lookup
# ---------------------------------------------------------------------------

class TestLookup:
    def test_get_by_agent_id(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        found = mgr.get_by_agent_id(provider["agent_id"])
        assert found is not None
        assert found["id"] == provider["id"]
        assert found["agent_id"] == provider["agent_id"]

    def test_get_by_agent_id_not_found(self, mgr):
        result = mgr.get_by_agent_id("nonexistent-agent-id")
        assert result is None

    def test_get_by_agent_id_empty_returns_none(self, mgr):
        result = mgr.get_by_agent_id("")
        assert result is None

    def test_get_by_agent_id_whitespace_returns_none(self, mgr):
        result = mgr.get_by_agent_id("   ")
        assert result is None

    def test_get_by_agent_id_strips_whitespace(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        found = mgr.get_by_agent_id(f"  {provider['agent_id']}  ")
        assert found is not None
        assert found["id"] == provider["id"]

    def test_get_by_agent_id_returns_full_record(self, mgr, identity_mgr):
        provider = _register_provider(mgr, identity_mgr)
        found = mgr.get_by_agent_id(provider["agent_id"])
        # Verify all key fields are present
        assert "id" in found
        assert "agent_id" in found
        assert "owner_email" in found
        assert "wallet_address" in found
        assert "did" in found
        assert "status" in found
        assert "reputation_score" in found
        assert "daily_tx_cap" in found
        assert "created_at" in found


# ---------------------------------------------------------------------------
# 8. Constants / Configuration
# ---------------------------------------------------------------------------

class TestConstants:
    def test_probation_days(self):
        assert AgentProviderManager.PROBATION_DAYS == 30

    def test_default_daily_tx_cap(self):
        assert AgentProviderManager.DEFAULT_DAILY_TX_CAP == 500.0

    def test_fast_track_reputation_threshold(self):
        assert AgentProviderManager.FAST_TRACK_REPUTATION_THRESHOLD == 80.0

    def test_report_delist_threshold(self):
        assert AgentProviderManager.REPORT_DELIST_THRESHOLD == 3

    def test_allowed_statuses(self):
        assert AgentProviderManager.ALLOWED_STATUSES == {
            "pending_review", "active", "suspended",
        }


# ---------------------------------------------------------------------------
# 9. Integration / Lifecycle Flow
# ---------------------------------------------------------------------------

class TestLifecycleFlow:
    def test_full_lifecycle(self, mgr, identity_mgr):
        """Register -> Activate -> Transact -> Suspend -> Reactivate."""
        provider = _register_provider(mgr, identity_mgr)
        assert provider["status"] == "pending_review"

        activated = mgr.activate(provider["id"])
        assert activated["status"] == "active"

        assert mgr.check_daily_tx_limit(provider["id"], 100.0) is True
        mgr.record_transaction(provider["id"], 100.0)

        suspended = mgr.suspend(provider["id"], "Under investigation")
        assert suspended["status"] == "suspended"

        reactivated = mgr.activate(provider["id"])
        assert reactivated["status"] == "active"

    def test_multiple_providers_independent(self, mgr, identity_mgr):
        """Two providers have independent transaction counters."""
        p1 = _register_provider(
            mgr, identity_mgr, display_name="Bot1", owner_id="o1",
        )
        p2 = _register_provider(
            mgr, identity_mgr, display_name="Bot2", owner_id="o2",
        )

        mgr.record_transaction(p1["id"], 400.0)
        # p1 near cap, p2 untouched
        assert mgr.check_daily_tx_limit(p1["id"], 101.0) is False
        assert mgr.check_daily_tx_limit(p2["id"], 500.0) is True


# ---------------------------------------------------------------------------
# 9. Configurable Probation
# ---------------------------------------------------------------------------

class TestConfigurableProbation:
    """Tests for configurable probation period and fast-track reduced probation."""

    def test_default_probation_30_days(self, db, identity_mgr):
        """Default probation is 30 days."""
        mgr = AgentProviderManager(db)
        provider = _register_provider(mgr, identity_mgr)
        ends = datetime.fromisoformat(provider["probation_ends_at"])
        created = datetime.fromisoformat(provider["created_at"])
        delta = ends - created
        assert delta.days == 30

    def test_custom_probation_days(self, db, identity_mgr):
        """Custom probation days are respected."""
        mgr = AgentProviderManager(db, probation_days=14)
        provider = _register_provider(mgr, identity_mgr)
        ends = datetime.fromisoformat(provider["probation_ends_at"])
        created = datetime.fromisoformat(provider["created_at"])
        delta = ends - created
        assert delta.days == 14

    def test_minimum_probation_enforced(self, db, identity_mgr):
        """Probation cannot be set below minimum (7 days)."""
        mgr = AgentProviderManager(db, probation_days=3)
        assert mgr.PROBATION_DAYS == 7
        provider = _register_provider(mgr, identity_mgr)
        ends = datetime.fromisoformat(provider["probation_ends_at"])
        created = datetime.fromisoformat(provider["created_at"])
        delta = ends - created
        assert delta.days == 7

    def test_fast_track_reduced_probation(self, db, identity_mgr):
        """Agents with reputation >= 80 get reduced probation (14 days)."""
        mgr = AgentProviderManager(db)
        # Create an agent with high reputation
        agent = identity_mgr.register("HighRepBot", "owner-frp")
        # Manually set high reputation on the agent identity
        db.update_agent(agent.agent_id, {"reputation_score": 85.0})

        provider = mgr.register(
            agent_id=agent.agent_id,
            owner_email=VALID_EMAIL,
            wallet_address=VALID_WALLET,
            did=VALID_DID,
        )

        ends = datetime.fromisoformat(provider["probation_ends_at"])
        created = datetime.fromisoformat(provider["created_at"])
        delta = ends - created
        assert delta.days == 14  # FAST_TRACK_PROBATION_DAYS

    def test_low_reputation_full_probation(self, db, identity_mgr):
        """Agents with reputation < 80 get full probation."""
        mgr = AgentProviderManager(db)
        agent = identity_mgr.register("LowRepBot", "owner-lrp")
        db.update_agent(agent.agent_id, {"reputation_score": 50.0})

        provider = mgr.register(
            agent_id=agent.agent_id,
            owner_email=VALID_EMAIL,
            wallet_address=VALID_WALLET,
            did=VALID_DID,
        )

        ends = datetime.fromisoformat(provider["probation_ends_at"])
        created = datetime.fromisoformat(provider["created_at"])
        delta = ends - created
        assert delta.days == 30

    def test_constants(self):
        """Verify probation constants."""
        assert AgentProviderManager.PROBATION_DAYS == 30
        assert AgentProviderManager.PROBATION_DAYS_MIN == 7
        assert AgentProviderManager.FAST_TRACK_PROBATION_DAYS == 14
