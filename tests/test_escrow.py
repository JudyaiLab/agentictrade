"""Tests for EscrowManager — tiered escrow hold system with structured disputes."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from marketplace.db import Database
from marketplace.escrow import (
    DISPUTE_CATEGORIES,
    RESOLUTION_OUTCOMES,
    EscrowError,
    EscrowManager,
    _validate_evidence_urls,
)
from marketplace.identity import IdentityManager
from marketplace.agent_provider import AgentProviderManager


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def escrow(db):
    return EscrowManager(db)


def _setup_provider(db) -> dict:
    """Register an active agent provider and return its details.

    Returns dict with keys: agent_id, provider_id, service_id.
    """
    agent = IdentityManager(db).register("TestBot", "owner-1")
    provider = AgentProviderManager(db).register(
        agent.agent_id,
        "test@example.com",
        "0x" + "a" * 40,
        "did:web:example.com",
    )
    AgentProviderManager(db).activate(provider["id"])

    now = datetime.now(timezone.utc).isoformat()
    svc_id = f"svc_{uuid.uuid4().hex[:12]}"
    db.insert_service({
        "id": svc_id,
        "provider_id": provider["id"],
        "name": "Test Service",
        "description": "A test service",
        "endpoint": "https://api.test.com/v1",
        "price_per_call": 0.01,
        "currency": "USDC",
        "payment_method": "x402",
        "free_tier_calls": 0,
        "status": "active",
        "category": "ai",
        "tags": [],
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    })
    return {
        "agent_id": agent.agent_id,
        "provider_id": provider["id"],
        "service_id": svc_id,
    }


# ── TestCreateHold ──


class TestCreateHold:
    def test_valid_hold(self, db, escrow):
        """Creating a hold with valid inputs returns a proper record."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-001",
        )

        assert hold["id"] is not None
        assert hold["provider_id"] == p["provider_id"]
        assert hold["service_id"] == p["service_id"]
        assert hold["buyer_id"] == "buyer-1"
        assert hold["amount"] == 50.0
        assert hold["currency"] == "USDC"
        assert hold["status"] == "held"
        assert hold["usage_record_id"] == "usage-001"
        assert hold["held_at"] is not None
        assert hold["release_at"] is not None
        assert hold["released_at"] is None

        # Verify release_at matches tiered hold ($50 → 3 days)
        held_at = datetime.fromisoformat(hold["held_at"])
        release_at = datetime.fromisoformat(hold["release_at"])
        delta = release_at - held_at
        assert delta.days == 3

    def test_valid_hold_custom_currency(self, db, escrow):
        """Creating a hold with custom currency stores it correctly."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=10.0,
            usage_record_id="usage-002",
            currency="ETH",
        )
        assert hold["currency"] == "ETH"

    def test_hold_persisted_in_db(self, db, escrow):
        """Created hold can be retrieved from the database."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=25.0,
            usage_record_id="usage-003",
        )
        fetched = db.get_escrow_hold(hold["id"])
        assert fetched is not None
        assert fetched["id"] == hold["id"]
        assert fetched["amount"] == 25.0
        assert fetched["status"] == "held"

    def test_decimal_amount(self, db, escrow):
        """Amount as Decimal is handled correctly."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=Decimal("99.99"),
            usage_record_id="usage-004",
        )
        assert hold["amount"] == 99.99

    def test_invalid_amount_zero(self, db, escrow):
        """Amount of zero raises EscrowError."""
        p = _setup_provider(db)
        with pytest.raises(EscrowError, match="amount must be positive"):
            escrow.create_hold(
                provider_id=p["provider_id"],
                service_id=p["service_id"],
                buyer_id="buyer-1",
                amount=0,
                usage_record_id="usage-005",
            )

    def test_invalid_amount_negative(self, db, escrow):
        """Negative amount raises EscrowError."""
        p = _setup_provider(db)
        with pytest.raises(EscrowError, match="amount must be positive"):
            escrow.create_hold(
                provider_id=p["provider_id"],
                service_id=p["service_id"],
                buyer_id="buyer-1",
                amount=-10.0,
                usage_record_id="usage-006",
            )

    def test_non_provider_fails(self, db, escrow):
        """Unregistered provider_id raises EscrowError."""
        with pytest.raises(EscrowError, match="not a registered agent provider"):
            escrow.create_hold(
                provider_id="fake-provider-id",
                service_id="svc-fake",
                buyer_id="buyer-1",
                amount=10.0,
                usage_record_id="usage-007",
            )

    def test_missing_provider_id(self, db, escrow):
        """Empty provider_id raises EscrowError."""
        with pytest.raises(EscrowError, match="provider_id, service_id, and buyer_id are required"):
            escrow.create_hold(
                provider_id="",
                service_id="svc-1",
                buyer_id="buyer-1",
                amount=10.0,
                usage_record_id="usage-008",
            )

    def test_missing_service_id(self, db, escrow):
        """Empty service_id raises EscrowError."""
        with pytest.raises(EscrowError, match="provider_id, service_id, and buyer_id are required"):
            escrow.create_hold(
                provider_id="prov-1",
                service_id="",
                buyer_id="buyer-1",
                amount=10.0,
                usage_record_id="usage-009",
            )

    def test_missing_buyer_id(self, db, escrow):
        """Empty buyer_id raises EscrowError."""
        with pytest.raises(EscrowError, match="provider_id, service_id, and buyer_id are required"):
            escrow.create_hold(
                provider_id="prov-1",
                service_id="svc-1",
                buyer_id="",
                amount=10.0,
                usage_record_id="usage-010",
            )

    def test_multiple_holds_for_same_provider(self, db, escrow):
        """Multiple holds can be created for the same provider."""
        p = _setup_provider(db)
        hold_1 = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=10.0,
            usage_record_id="usage-011",
        )
        hold_2 = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-2",
            amount=20.0,
            usage_record_id="usage-012",
        )
        assert hold_1["id"] != hold_2["id"]
        all_holds = db.list_escrow_holds(provider_id=p["provider_id"])
        assert len(all_holds) == 2


# ── TestReleaseHold ──


class TestReleaseHold:
    def test_release_held(self, db, escrow):
        """Releasing a held escrow sets status to 'released' and records released_at."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-020",
        )

        released = escrow.release_hold(hold["id"])
        assert released["status"] == "released"
        assert released["released_at"] is not None

        # Verify persisted in DB
        fetched = db.get_escrow_hold(hold["id"])
        assert fetched["status"] == "released"
        assert fetched["released_at"] is not None

    def test_release_non_held_fails(self, db, escrow):
        """Cannot release a hold that is already released."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-021",
        )
        escrow.release_hold(hold["id"])

        with pytest.raises(EscrowError, match="Cannot release hold"):
            escrow.release_hold(hold["id"])

    def test_release_refunded_fails(self, db, escrow):
        """Cannot release a hold that is refunded."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-022",
        )
        escrow.refund_hold(hold["id"])

        with pytest.raises(EscrowError, match="Cannot release hold"):
            escrow.release_hold(hold["id"])

    def test_release_disputed_fails(self, db, escrow):
        """Cannot release a hold that is disputed."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-023",
        )
        escrow.dispute_hold(hold["id"])

        with pytest.raises(EscrowError, match="Cannot release hold"):
            escrow.release_hold(hold["id"])

    def test_release_not_found(self, escrow):
        """Releasing a nonexistent hold raises EscrowError."""
        with pytest.raises(EscrowError, match="Escrow hold not found"):
            escrow.release_hold("nonexistent-hold-id")


# ── TestRefundHold ──


class TestRefundHold:
    def test_refund_held(self, db, escrow):
        """Refunding a held escrow sets status to 'refunded'."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=30.0,
            usage_record_id="usage-030",
        )

        refunded = escrow.refund_hold(hold["id"], reason="buyer requested refund")
        assert refunded["status"] == "refunded"
        assert refunded["released_at"] is not None

        # Verify in DB
        fetched = db.get_escrow_hold(hold["id"])
        assert fetched["status"] == "refunded"

    def test_refund_disputed(self, db, escrow):
        """Refunding a disputed escrow is allowed."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=30.0,
            usage_record_id="usage-031",
        )
        escrow.dispute_hold(hold["id"])

        refunded = escrow.refund_hold(hold["id"], reason="dispute resolved in buyer favor")
        assert refunded["status"] == "refunded"
        assert refunded["released_at"] is not None

    def test_refund_released_fails(self, db, escrow):
        """Cannot refund a hold that is already released."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=30.0,
            usage_record_id="usage-032",
        )
        escrow.release_hold(hold["id"])

        with pytest.raises(EscrowError, match="Cannot refund hold"):
            escrow.refund_hold(hold["id"])

    def test_refund_already_refunded_fails(self, db, escrow):
        """Cannot refund a hold that is already refunded."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=30.0,
            usage_record_id="usage-033",
        )
        escrow.refund_hold(hold["id"])

        with pytest.raises(EscrowError, match="Cannot refund hold"):
            escrow.refund_hold(hold["id"])

    def test_refund_without_reason(self, db, escrow):
        """Refunding with empty reason succeeds (reason is optional)."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=30.0,
            usage_record_id="usage-034",
        )

        refunded = escrow.refund_hold(hold["id"])
        assert refunded["status"] == "refunded"

    def test_refund_not_found(self, escrow):
        """Refunding a nonexistent hold raises EscrowError."""
        with pytest.raises(EscrowError, match="Escrow hold not found"):
            escrow.refund_hold("nonexistent-hold-id")


# ── TestDisputeHold ──


class TestDisputeHold:
    def test_dispute_held(self, db, escrow):
        """Disputing a held escrow sets status to 'disputed'."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=40.0,
            usage_record_id="usage-040",
        )

        disputed = escrow.dispute_hold(hold["id"])
        assert disputed["status"] == "disputed"

        # Verify in DB
        fetched = db.get_escrow_hold(hold["id"])
        assert fetched["status"] == "disputed"

    def test_dispute_released_fails(self, db, escrow):
        """Cannot dispute a hold that is already released."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=40.0,
            usage_record_id="usage-041",
        )
        escrow.release_hold(hold["id"])

        with pytest.raises(EscrowError, match="Cannot dispute hold"):
            escrow.dispute_hold(hold["id"])

    def test_dispute_refunded_fails(self, db, escrow):
        """Cannot dispute a hold that is already refunded."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=40.0,
            usage_record_id="usage-042",
        )
        escrow.refund_hold(hold["id"])

        with pytest.raises(EscrowError, match="Cannot dispute hold"):
            escrow.dispute_hold(hold["id"])

    def test_dispute_already_disputed_fails(self, db, escrow):
        """Cannot dispute a hold that is already disputed."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=40.0,
            usage_record_id="usage-043",
        )
        escrow.dispute_hold(hold["id"])

        with pytest.raises(EscrowError, match="Cannot dispute hold"):
            escrow.dispute_hold(hold["id"])

    def test_dispute_not_found(self, escrow):
        """Disputing a nonexistent hold raises EscrowError."""
        with pytest.raises(EscrowError, match="Escrow hold not found"):
            escrow.dispute_hold("nonexistent-hold-id")


# ── TestProcessReleasable ──


class TestProcessReleasable:
    def test_batch_release_past_due(self, db, escrow):
        """Holds past their release_at are released in batch."""
        p = _setup_provider(db)

        # Create a hold, then manually backdate release_at to the past
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=10.0,
            usage_record_id="usage-050",
        )
        past_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.update_escrow_hold(hold["id"], {"release_at": past_time})

        released = escrow.process_releasable()
        assert len(released) == 1
        assert released[0]["id"] == hold["id"]
        assert released[0]["status"] == "released"

    def test_batch_release_multiple(self, db, escrow):
        """Multiple past-due holds are all released."""
        p = _setup_provider(db)
        past_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        hold_ids = []
        for i in range(3):
            hold = escrow.create_hold(
                provider_id=p["provider_id"],
                service_id=p["service_id"],
                buyer_id=f"buyer-{i}",
                amount=10.0 + i,
                usage_record_id=f"usage-05{i+1}",
            )
            db.update_escrow_hold(hold["id"], {"release_at": past_time})
            hold_ids.append(hold["id"])

        released = escrow.process_releasable()
        assert len(released) == 3
        released_ids = {r["id"] for r in released}
        assert released_ids == set(hold_ids)

    def test_skip_future_holds(self, db, escrow):
        """Holds with release_at in the future are not released."""
        p = _setup_provider(db)

        # Default hold has release_at = now + 7 days (in the future)
        escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=10.0,
            usage_record_id="usage-054",
        )

        released = escrow.process_releasable()
        assert len(released) == 0

    def test_skip_disputed_holds(self, db, escrow):
        """Disputed holds are not released even if past release_at."""
        p = _setup_provider(db)

        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=10.0,
            usage_record_id="usage-055",
        )
        past_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.update_escrow_hold(hold["id"], {"release_at": past_time})

        # Dispute the hold
        escrow.dispute_hold(hold["id"])

        released = escrow.process_releasable()
        assert len(released) == 0

        # Verify it is still disputed
        fetched = db.get_escrow_hold(hold["id"])
        assert fetched["status"] == "disputed"

    def test_skip_already_released(self, db, escrow):
        """Already-released holds are not re-released."""
        p = _setup_provider(db)

        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=10.0,
            usage_record_id="usage-056",
        )
        past_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.update_escrow_hold(hold["id"], {"release_at": past_time})
        escrow.release_hold(hold["id"])

        released = escrow.process_releasable()
        assert len(released) == 0

    def test_empty_when_no_holds(self, escrow):
        """process_releasable returns empty list when no holds exist."""
        released = escrow.process_releasable()
        assert released == []

    def test_mixed_statuses(self, db, escrow):
        """Only 'held' past-due records are released; disputed/refunded are skipped."""
        p = _setup_provider(db)
        past_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        # Hold 1: held, past-due -> should release
        hold_1 = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=10.0,
            usage_record_id="usage-057",
        )
        db.update_escrow_hold(hold_1["id"], {"release_at": past_time})

        # Hold 2: disputed, past-due -> should NOT release
        hold_2 = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-2",
            amount=20.0,
            usage_record_id="usage-058",
        )
        db.update_escrow_hold(hold_2["id"], {"release_at": past_time})
        escrow.dispute_hold(hold_2["id"])

        # Hold 3: refunded, past-due -> should NOT release
        hold_3 = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-3",
            amount=30.0,
            usage_record_id="usage-059",
        )
        db.update_escrow_hold(hold_3["id"], {"release_at": past_time})
        escrow.refund_hold(hold_3["id"])

        # Hold 4: held, future -> should NOT release
        escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-4",
            amount=40.0,
            usage_record_id="usage-060",
        )

        released = escrow.process_releasable()
        assert len(released) == 1
        assert released[0]["id"] == hold_1["id"]

    def test_handles_release_error_gracefully(self, db, escrow):
        """If releasing one hold fails, other holds still process."""
        p = _setup_provider(db)
        past_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        hold_1 = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=10.0,
            usage_record_id="usage-061",
        )
        db.update_escrow_hold(hold_1["id"], {"release_at": past_time})

        hold_2 = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-2",
            amount=20.0,
            usage_record_id="usage-062",
        )
        db.update_escrow_hold(hold_2["id"], {"release_at": past_time})

        # Manually break hold_1 by setting it to 'refunded' directly in DB
        # so release_hold will raise EscrowError for it
        db.update_escrow_hold(hold_1["id"], {"status": "refunded"})

        released = escrow.process_releasable()
        # hold_1 is refunded -> list_releasable won't return it (only status='held')
        # hold_2 is still held -> should release
        assert len(released) == 1
        assert released[0]["id"] == hold_2["id"]


# ── TestProviderSummary ──


class TestProviderSummary:
    def test_empty_summary(self, db, escrow):
        """Provider with no holds returns zero aggregates."""
        summary = escrow.get_provider_escrow_summary("no-such-provider")
        assert summary["provider_id"] == "no-such-provider"
        assert summary["total_held"] == 0.0
        assert summary["total_released"] == 0.0
        assert summary["total_refunded"] == 0.0
        assert summary["pending_count"] == 0

    def test_single_held(self, db, escrow):
        """Summary with one held entry."""
        p = _setup_provider(db)
        escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=100.0,
            usage_record_id="usage-070",
        )

        summary = escrow.get_provider_escrow_summary(p["provider_id"])
        assert summary["total_held"] == 100.0
        assert summary["total_released"] == 0.0
        assert summary["total_refunded"] == 0.0
        assert summary["pending_count"] == 1

    def test_mixed_statuses(self, db, escrow):
        """Summary correctly aggregates across different statuses."""
        p = _setup_provider(db)

        # Held
        escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=100.0,
            usage_record_id="usage-071",
        )

        # Released
        hold_2 = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-2",
            amount=200.0,
            usage_record_id="usage-072",
        )
        escrow.release_hold(hold_2["id"])

        # Refunded
        hold_3 = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-3",
            amount=50.0,
            usage_record_id="usage-073",
        )
        escrow.refund_hold(hold_3["id"])

        # Disputed (counts as pending/held)
        hold_4 = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-4",
            amount=75.0,
            usage_record_id="usage-074",
        )
        escrow.dispute_hold(hold_4["id"])

        summary = escrow.get_provider_escrow_summary(p["provider_id"])
        assert summary["provider_id"] == p["provider_id"]
        assert summary["total_held"] == 175.0   # 100 (held) + 75 (disputed)
        assert summary["total_released"] == 200.0
        assert summary["total_refunded"] == 50.0
        assert summary["pending_count"] == 2     # 1 held + 1 disputed

    def test_does_not_include_other_providers(self, db, escrow):
        """Summary only includes holds for the specified provider."""
        p1 = _setup_provider(db)

        # Create a second provider
        agent2 = IdentityManager(db).register("Bot2", "owner-2")
        provider2 = AgentProviderManager(db).register(
            agent2.agent_id,
            "bot2@example.com",
            "0x" + "b" * 40,
            "did:web:example2.com",
        )
        AgentProviderManager(db).activate(provider2["id"])
        now = datetime.now(timezone.utc).isoformat()
        svc2_id = f"svc_{uuid.uuid4().hex[:12]}"
        db.insert_service({
            "id": svc2_id,
            "provider_id": provider2["id"],
            "name": "Other Service",
            "description": "",
            "endpoint": "https://api.other.com/v1",
            "price_per_call": 0.05,
            "created_at": now,
            "updated_at": now,
        })

        escrow.create_hold(
            provider_id=p1["provider_id"],
            service_id=p1["service_id"],
            buyer_id="buyer-1",
            amount=100.0,
            usage_record_id="usage-075",
        )
        escrow.create_hold(
            provider_id=provider2["id"],
            service_id=svc2_id,
            buyer_id="buyer-2",
            amount=999.0,
            usage_record_id="usage-076",
        )

        summary_p1 = escrow.get_provider_escrow_summary(p1["provider_id"])
        assert summary_p1["total_held"] == 100.0
        assert summary_p1["pending_count"] == 1

        summary_p2 = escrow.get_provider_escrow_summary(provider2["id"])
        assert summary_p2["total_held"] == 999.0
        assert summary_p2["pending_count"] == 1


# ── TestIsAgentProvider ──


class TestIsAgentProvider:
    def test_registered_provider(self, db, escrow):
        """Registered and active provider returns True."""
        p = _setup_provider(db)
        assert escrow.is_agent_provider(p["provider_id"]) is True

    def test_unregistered_id(self, db, escrow):
        """Random ID that is not a provider returns False."""
        assert escrow.is_agent_provider("not-a-real-provider") is False

    def test_agent_without_provider_registration(self, db, escrow):
        """Agent identity without provider registration returns False."""
        agent = IdentityManager(db).register("SoloBot", "owner-3")
        assert escrow.is_agent_provider(agent.agent_id) is False

    def test_pending_provider(self, db, escrow):
        """Provider in pending_review status is still found (is_agent_provider checks existence, not status)."""
        agent = IdentityManager(db).register("PendingBot", "owner-4")
        provider = AgentProviderManager(db).register(
            agent.agent_id,
            "pending@example.com",
            "0x" + "c" * 40,
            "did:web:pending.com",
        )
        # Provider is in pending_review status (not activated)
        assert escrow.is_agent_provider(provider["id"]) is True


# ── TestHoldDaysConstant ──


class TestHoldTiers:
    def test_tiered_hold_periods(self):
        """Verify tiered escrow hold periods."""
        assert EscrowManager.HOLD_TIERS == [
            (1.0, 1),
            (100.0, 3),
            (float("inf"), 7),
        ]

    def test_dispute_timeout_fallback(self):
        """Dispute timeout fallback is 72 hours."""
        assert EscrowManager.DISPUTE_TIMEOUT_HOURS == 72

    def test_dispute_timeout_tiers(self):
        """Verify tiered dispute timeout periods."""
        assert EscrowManager.DISPUTE_TIMEOUT_TIERS == [
            (1.0, 24),
            (100.0, 72),
            (float("inf"), 168),
        ]


# ── TestStructuredDispute ──


class TestStructuredDispute:
    """Tests for structured dispute evidence submission."""

    def test_dispute_with_evidence(self, db, escrow):
        """Disputing with reason, category, and evidence stores structured data."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-sd01",
        )

        updated = escrow.dispute_hold(
            hold["id"],
            reason="Service returned error 500",
            category="timeout_or_error",
            evidence_urls=["https://example.com/screenshot1.png"],
            submitted_by="buyer-1",
        )

        assert updated["status"] == "disputed"
        assert updated["dispute_reason"] == "Service returned error 500"
        assert updated["dispute_category"] == "timeout_or_error"
        assert updated["dispute_timeout_at"] is not None

    def test_dispute_stores_evidence_record(self, db, escrow):
        """Dispute evidence is persisted in dispute_evidence table."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-sd02",
        )

        escrow.dispute_hold(
            hold["id"],
            reason="Wrong output format",
            category="wrong_output",
            evidence_urls=["https://example.com/log.txt"],
            submitted_by="buyer-1",
        )

        evidence = escrow.get_dispute_evidence(hold["id"])
        assert len(evidence) == 1
        assert evidence[0]["role"] == "buyer"
        assert evidence[0]["submitted_by"] == "buyer-1"
        assert evidence[0]["description"] == "Wrong output format"
        assert evidence[0]["evidence_urls"] == ["https://example.com/log.txt"]

    def test_dispute_invalid_category(self, db, escrow):
        """Invalid dispute category raises EscrowError."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-sd03",
        )

        with pytest.raises(EscrowError, match="Invalid dispute category"):
            escrow.dispute_hold(
                hold["id"],
                reason="test",
                category="invalid_category",
            )

    def test_dispute_backward_compatible(self, db, escrow):
        """Dispute without evidence (old API style) still works."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-sd04",
        )

        updated = escrow.dispute_hold(hold["id"])
        assert updated["status"] == "disputed"
        assert updated["dispute_category"] == "other"

    def test_dispute_categories_constant(self):
        """Verify all dispute categories exist."""
        assert "service_not_delivered" in DISPUTE_CATEGORIES
        assert "quality_issue" in DISPUTE_CATEGORIES
        assert "unauthorized_charge" in DISPUTE_CATEGORIES
        assert "wrong_output" in DISPUTE_CATEGORIES
        assert "timeout_or_error" in DISPUTE_CATEGORIES
        assert "other" in DISPUTE_CATEGORIES


# ── TestDisputeTimeoutTiers ──


class TestDisputeTimeoutTiers:
    """Tests for amount-based dispute timeout scaling."""

    def test_micropayment_24h_timeout(self, db, escrow):
        """<$1 disputes get 24h timeout."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=0.50,
            usage_record_id="usage-dt01",
        )

        updated = escrow.dispute_hold(hold["id"], reason="test")
        timeout_at = datetime.fromisoformat(updated["dispute_timeout_at"])
        # Should be ~24 hours from now
        expected_min = datetime.now(timezone.utc) + timedelta(hours=23)
        expected_max = datetime.now(timezone.utc) + timedelta(hours=25)
        assert expected_min < timeout_at < expected_max

    def test_standard_72h_timeout(self, db, escrow):
        """$1-$100 disputes get 72h timeout."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-dt02",
        )

        updated = escrow.dispute_hold(hold["id"], reason="test")
        timeout_at = datetime.fromisoformat(updated["dispute_timeout_at"])
        expected_min = datetime.now(timezone.utc) + timedelta(hours=71)
        expected_max = datetime.now(timezone.utc) + timedelta(hours=73)
        assert expected_min < timeout_at < expected_max

    def test_high_value_168h_timeout(self, db, escrow):
        """$100+ disputes get 168h (7 day) timeout."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=500.0,
            usage_record_id="usage-dt03",
        )

        updated = escrow.dispute_hold(hold["id"], reason="test")
        timeout_at = datetime.fromisoformat(updated["dispute_timeout_at"])
        expected_min = datetime.now(timezone.utc) + timedelta(hours=167)
        expected_max = datetime.now(timezone.utc) + timedelta(hours=169)
        assert expected_min < timeout_at < expected_max

    def test_timeout_helper_method(self, db, escrow):
        """Verify _dispute_timeout_hours_for_amount returns correct tiers."""
        assert escrow._dispute_timeout_hours_for_amount(0.50) == 24
        assert escrow._dispute_timeout_hours_for_amount(0.99) == 24
        assert escrow._dispute_timeout_hours_for_amount(1.0) == 72
        assert escrow._dispute_timeout_hours_for_amount(50.0) == 72
        assert escrow._dispute_timeout_hours_for_amount(99.99) == 72
        assert escrow._dispute_timeout_hours_for_amount(100.0) == 168
        assert escrow._dispute_timeout_hours_for_amount(1000.0) == 168


# ── TestProviderResponse ──


class TestProviderResponse:
    """Tests for provider counter-response to disputes."""

    def test_provider_can_respond(self, db, escrow):
        """Provider can submit counter-evidence to a dispute."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-pr01",
        )
        escrow.dispute_hold(hold["id"], reason="Service failed")

        escrow.respond_to_dispute(
            hold["id"],
            responder_id=p["provider_id"],
            description="Service was delivered successfully, see logs",
            evidence_urls=["https://example.com/server-log.txt"],
        )

        evidence = escrow.get_dispute_evidence(hold["id"])
        assert len(evidence) == 2  # buyer + provider
        provider_ev = [e for e in evidence if e["role"] == "provider"]
        assert len(provider_ev) == 1
        assert provider_ev[0]["description"] == "Service was delivered successfully, see logs"

    def test_respond_to_non_disputed_fails(self, db, escrow):
        """Cannot respond to a hold that is not disputed."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-pr02",
        )

        with pytest.raises(EscrowError, match="expected 'disputed'"):
            escrow.respond_to_dispute(
                hold["id"],
                responder_id=p["provider_id"],
                description="test",
            )

    def test_empty_response_fails(self, db, escrow):
        """Empty response description raises EscrowError."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-pr03",
        )
        escrow.dispute_hold(hold["id"])

        with pytest.raises(EscrowError, match="Response description is required"):
            escrow.respond_to_dispute(
                hold["id"],
                responder_id=p["provider_id"],
                description="",
            )


# ── TestAdminResolveDispute ──


class TestAdminResolveDispute:
    """Tests for admin dispute resolution."""

    def test_resolve_refund_buyer(self, db, escrow):
        """Admin resolves dispute in buyer's favor."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-ar01",
        )
        escrow.dispute_hold(hold["id"], reason="test")

        resolved = escrow.resolve_dispute(
            hold["id"],
            outcome="refund_buyer",
            note="Buyer claim verified, service did not deliver.",
        )

        assert resolved["status"] == "refunded"
        assert resolved["resolution_outcome"] == "refund_buyer"
        assert resolved["resolution_note"] == "Buyer claim verified, service did not deliver."
        assert resolved["resolved_at"] is not None

    def test_resolve_release_to_provider(self, db, escrow):
        """Admin resolves dispute in provider's favor."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-ar02",
        )
        escrow.dispute_hold(hold["id"], reason="test")

        resolved = escrow.resolve_dispute(
            hold["id"],
            outcome="release_to_provider",
            note="Service was delivered as described.",
        )

        assert resolved["status"] == "released"
        assert resolved["resolution_outcome"] == "release_to_provider"

    def test_resolve_partial_refund(self, db, escrow):
        """Admin resolves with partial refund including refund_amount."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-ar03",
        )
        escrow.dispute_hold(hold["id"], reason="test")

        resolved = escrow.resolve_dispute(
            hold["id"],
            outcome="partial_refund",
            note="Service partially delivered. 50% refund.",
            refund_amount=25.0,
        )

        assert resolved["status"] == "refunded"
        assert resolved["resolution_outcome"] == "partial_refund"
        assert resolved["refund_amount"] == 25.0
        assert resolved["provider_payout"] == 25.0

    def test_partial_refund_requires_amount(self, db, escrow):
        """partial_refund without refund_amount raises EscrowError."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-ar04",
        )
        escrow.dispute_hold(hold["id"], reason="test")

        with pytest.raises(EscrowError, match="refund_amount is required"):
            escrow.resolve_dispute(hold["id"], outcome="partial_refund")

    def test_partial_refund_validates_amount(self, db, escrow):
        """partial_refund with invalid amount raises EscrowError."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-ar05",
        )
        escrow.dispute_hold(hold["id"], reason="test")

        # Zero amount
        with pytest.raises(EscrowError, match="must be positive"):
            escrow.resolve_dispute(hold["id"], outcome="partial_refund", refund_amount=0)

        # Amount >= hold amount
        with pytest.raises(EscrowError, match="must be less than"):
            escrow.resolve_dispute(hold["id"], outcome="partial_refund", refund_amount=50.0)

    def test_resolve_invalid_outcome(self, db, escrow):
        """Invalid resolution outcome raises EscrowError."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-ar04",
        )
        escrow.dispute_hold(hold["id"])

        with pytest.raises(EscrowError, match="Invalid resolution outcome"):
            escrow.resolve_dispute(hold["id"], outcome="split_50_50")

    def test_resolve_non_disputed_fails(self, db, escrow):
        """Cannot resolve a hold that is not disputed."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-ar05",
        )

        with pytest.raises(EscrowError, match="expected 'disputed'"):
            escrow.resolve_dispute(hold["id"], outcome="refund_buyer")

    def test_resolution_outcomes_constant(self):
        """Verify all resolution outcomes exist."""
        assert "refund_buyer" in RESOLUTION_OUTCOMES
        assert "release_to_provider" in RESOLUTION_OUTCOMES
        assert "partial_refund" in RESOLUTION_OUTCOMES


# ── TestAutoResolveWithTieredTimeout ──


class TestAutoResolveWithTieredTimeout:
    """Tests for tiered dispute auto-resolution in process_releasable."""

    def test_auto_resolve_expired_dispute_with_timeout_at(self, db, escrow):
        """Dispute with expired dispute_timeout_at is auto-released."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-tr01",
        )
        escrow.dispute_hold(hold["id"], reason="test")

        # Backdate dispute_timeout_at to the past
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.update_escrow_hold(hold["id"], {"dispute_timeout_at": past})

        released = escrow.process_releasable()
        assert len(released) == 1
        assert released[0]["id"] == hold["id"]
        assert released[0]["status"] == "released"

    def test_no_auto_resolve_if_timeout_not_expired(self, db, escrow):
        """Dispute with future dispute_timeout_at is NOT auto-released."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            provider_id=p["provider_id"],
            service_id=p["service_id"],
            buyer_id="buyer-1",
            amount=50.0,
            usage_record_id="usage-tr02",
        )
        escrow.dispute_hold(hold["id"], reason="test")

        # dispute_timeout_at is already set to ~72h from now
        released = escrow.process_releasable()
        assert len(released) == 0


# ── Evidence URL Validation ──


class TestEvidenceUrlValidation:
    """Tests for _validate_evidence_urls helper."""

    def test_none_returns_empty(self):
        assert _validate_evidence_urls(None) == []

    def test_empty_list_returns_empty(self):
        assert _validate_evidence_urls([]) == []

    def test_valid_https_urls(self):
        urls = ["https://example.com/evidence1.png", "https://example.com/evidence2.pdf"]
        result = _validate_evidence_urls(urls)
        assert result == urls

    def test_rejects_http_scheme(self):
        with pytest.raises(EscrowError, match="https://"):
            _validate_evidence_urls(["http://example.com/file.png"])

    def test_rejects_ftp_scheme(self):
        with pytest.raises(EscrowError, match="https://"):
            _validate_evidence_urls(["ftp://example.com/file.png"])

    def test_rejects_javascript_scheme(self):
        with pytest.raises(EscrowError, match="https://"):
            _validate_evidence_urls(["javascript:alert(1)"])

    def test_rejects_too_many_urls(self):
        urls = [f"https://example.com/{i}.png" for i in range(11)]
        with pytest.raises(EscrowError, match="Too many evidence URLs"):
            _validate_evidence_urls(urls)

    def test_allows_max_urls(self):
        urls = [f"https://example.com/{i}.png" for i in range(10)]
        result = _validate_evidence_urls(urls)
        assert len(result) == 10

    def test_rejects_too_long_url(self):
        url = "https://example.com/" + "a" * 2048
        with pytest.raises(EscrowError, match="exceeds"):
            _validate_evidence_urls([url])

    def test_strips_whitespace(self):
        result = _validate_evidence_urls(["  https://example.com/file.png  "])
        assert result == ["https://example.com/file.png"]

    def test_integration_dispute_hold_validates(self, db, escrow):
        """dispute_hold should reject invalid evidence URLs."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            buyer_id="buyer-ev1", provider_id=p["provider_id"],
            service_id=p["service_id"], amount=10.0,
            usage_record_id="usage-ev1",
        )
        with pytest.raises(EscrowError, match="https://"):
            escrow.dispute_hold(
                hold["id"], reason="test",
                evidence_urls=["http://bad.com/evil.png"],
            )

    def test_integration_respond_validates(self, db, escrow):
        """respond_to_dispute should reject invalid evidence URLs."""
        p = _setup_provider(db)
        hold = escrow.create_hold(
            buyer_id="buyer-ev2", provider_id=p["provider_id"],
            service_id=p["service_id"], amount=10.0,
            usage_record_id="usage-ev2",
        )
        escrow.dispute_hold(hold["id"], reason="test")
        with pytest.raises(EscrowError, match="https://"):
            escrow.respond_to_dispute(
                hold["id"], responder_id=p["provider_id"],
                description="counter",
                evidence_urls=["ftp://bad.com/file"],
            )
