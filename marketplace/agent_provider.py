"""
Agent Provider registration and lifecycle management.

Handles registration, activation, suspension, daily transaction limits,
fast-track eligibility, and lookup for AI agents acting as service providers.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from .db import Database

# Pre-compiled validation patterns
_WALLET_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_DID_RE = re.compile(r"^did:[a-z]+:[a-zA-Z0-9._:-]+$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AgentProviderError(Exception):
    """Raised when an agent provider operation fails."""


class AgentProviderManager:
    """Manages agent provider registration, lifecycle, and daily limits."""

    PROBATION_DAYS = 30
    PROBATION_DAYS_MIN = 7
    FAST_TRACK_PROBATION_DAYS = 14
    DEFAULT_DAILY_TX_CAP = 500.0
    FAST_TRACK_REPUTATION_THRESHOLD = 80.0
    REPORT_DELIST_THRESHOLD = 3

    ALLOWED_STATUSES = {"pending_review", "active", "suspended"}

    def __init__(self, db: Database, probation_days: int | None = None):
        self.db = db
        if probation_days is not None:
            self.PROBATION_DAYS = max(probation_days, self.PROBATION_DAYS_MIN)

    def register(
        self,
        agent_id: str,
        owner_email: str,
        wallet_address: str,
        did: str,
        declaration: str = "",
        metadata: dict | None = None,
    ) -> dict:
        """Register an agent as a service provider.

        Validates that the agent_id exists and is active, the owner_email is
        a valid format, wallet_address matches Ethereum format, did matches
        the DID spec, and the agent is not already registered as a provider.

        Returns the newly created agent_provider record as a dict.
        """
        # --- Input validation ---
        if not agent_id or not agent_id.strip():
            raise AgentProviderError("agent_id is required")
        agent_id = agent_id.strip()

        if not owner_email or not owner_email.strip():
            raise AgentProviderError("owner_email is required")
        owner_email = owner_email.strip().lower()
        if not _EMAIL_RE.match(owner_email):
            raise AgentProviderError("owner_email format is invalid")

        if not self.validate_wallet_address(wallet_address):
            raise AgentProviderError(
                "wallet_address must match ^0x[0-9a-fA-F]{40}$"
            )

        if not self.validate_did(did):
            raise AgentProviderError(
                "did must match ^did:[a-z]+:[a-zA-Z0-9._:-]+$"
            )

        # --- Agent identity must exist and be active ---
        agent = self.db.get_agent(agent_id)
        if not agent:
            raise AgentProviderError(
                f"Agent identity '{agent_id}' does not exist"
            )
        if agent.get("status") != "active":
            raise AgentProviderError(
                f"Agent identity '{agent_id}' is not active "
                f"(current status: {agent.get('status')})"
            )

        # --- Must not already be registered ---
        existing = self.db.get_agent_provider_by_agent_id(agent_id)
        if existing:
            raise AgentProviderError(
                f"Agent '{agent_id}' is already registered as provider "
                f"(id: {existing['id']})"
            )

        # --- Build the record ---
        now = datetime.now(timezone.utc)
        provider_id = str(uuid.uuid4())

        # Fast-track: agents with high reputation get shorter probation
        probation_days = self.PROBATION_DAYS
        reputation = float(agent.get("reputation_score", 0))
        if reputation >= self.FAST_TRACK_REPUTATION_THRESHOLD:
            probation_days = self.FAST_TRACK_PROBATION_DAYS
        probation_ends_at = now + timedelta(days=probation_days)

        record = {
            "id": provider_id,
            "agent_id": agent_id,
            "owner_email": owner_email,
            "wallet_address": wallet_address,
            "did": did,
            "declaration": declaration,
            "status": "pending_review",
            "reputation_score": 0.0,
            "fast_track_eligible": 0,
            "daily_tx_cap": self.DEFAULT_DAILY_TX_CAP,
            "daily_tx_used": 0.0,
            "daily_tx_reset_at": now.isoformat(),
            "probation_ends_at": probation_ends_at.isoformat(),
            "total_reports": 0,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "metadata": "{}",
        }

        self.db.insert_agent_provider(record)

        # Return the freshly inserted record from DB (immutable read-back)
        return self.db.get_agent_provider(provider_id)  # type: ignore[return-value]

    def activate(self, agent_provider_id: str) -> dict:
        """Set provider status to active.

        Returns the updated record.
        Raises AgentProviderError if provider not found.
        """
        provider = self._get_or_raise(agent_provider_id)
        if provider["status"] == "active":
            return provider  # already active, no-op

        now = datetime.now(timezone.utc).isoformat()
        self.db.update_agent_provider(
            agent_provider_id, {"status": "active", "updated_at": now}
        )
        return self.db.get_agent_provider(agent_provider_id)  # type: ignore[return-value]

    def suspend(self, agent_provider_id: str, reason: str) -> dict:
        """Suspend a provider with a reason.

        Stores the reason in metadata.suspension_reason.
        Returns the updated record.
        Raises AgentProviderError if provider not found or reason is empty.
        """
        if not reason or not reason.strip():
            raise AgentProviderError("Suspension reason is required")

        provider = self._get_or_raise(agent_provider_id)
        now = datetime.now(timezone.utc).isoformat()

        # Merge suspension reason into existing metadata without mutation
        existing_meta = dict(provider.get("metadata", {}))
        new_meta = {
            **existing_meta,
            "suspension_reason": reason.strip(),
            "suspended_at": now,
        }

        import json
        self.db.update_agent_provider(
            agent_provider_id,
            {
                "status": "suspended",
                "updated_at": now,
                "metadata": json.dumps(new_meta),
            },
        )
        return self.db.get_agent_provider(agent_provider_id)  # type: ignore[return-value]

    def check_daily_tx_limit(
        self, agent_provider_id: str, amount: float
    ) -> bool:
        """Check whether the provider can transact the given amount today.

        Auto-resets daily_tx_used if the current date has rolled over since
        the last reset. Returns True if the transaction would stay within cap.
        Read-only — does not modify the DB.
        """
        provider = self._get_or_raise(agent_provider_id)
        now = datetime.now(timezone.utc)

        daily_used = float(provider.get("daily_tx_used", 0.0))
        daily_cap = float(provider.get("daily_tx_cap", self.DEFAULT_DAILY_TX_CAP))

        # Auto-reset check: if a new UTC day has started, used is effectively 0
        reset_at_str = provider.get("daily_tx_reset_at")
        if reset_at_str:
            try:
                reset_at = datetime.fromisoformat(reset_at_str)
                if reset_at.date() < now.date():
                    daily_used = 0.0
            except (ValueError, TypeError):
                daily_used = 0.0

        return (daily_used + amount) <= daily_cap

    def record_transaction(
        self, agent_provider_id: str, amount: float
    ) -> bool:
        """Atomically check the daily cap and record the transaction.

        Uses a single SQL UPDATE with a WHERE clause to prevent TOCTOU
        race conditions. Returns True if the transaction was accepted,
        False if it would exceed the daily cap.

        Raises AgentProviderError if provider not found.
        """
        self._get_or_raise(agent_provider_id)  # existence check
        now = datetime.now(timezone.utc)
        today_str = now.date().isoformat()

        with self.db.connect() as conn:
            # Atomic UPDATE: reset if new day, then add amount only if within cap
            # Step 1: Reset if needed (new day)
            conn.execute(
                "UPDATE agent_providers "
                "SET daily_tx_used = 0.0, daily_tx_reset_at = ? "
                "WHERE id = ? AND DATE(daily_tx_reset_at) < ?",
                (now.isoformat(), agent_provider_id, today_str),
            )

            # Step 2: Atomic increment with cap check
            cur = conn.execute(
                "UPDATE agent_providers "
                "SET daily_tx_used = daily_tx_used + ?, updated_at = ? "
                "WHERE id = ? AND (daily_tx_used + ?) <= daily_tx_cap",
                (amount, now.isoformat(), agent_provider_id, amount),
            )
            accepted = cur.rowcount > 0

        return accepted

    def is_fast_track_eligible(self, agent_provider_id: str) -> bool:
        """Check fast-track eligibility.

        Eligible when:
        - reputation_score >= FAST_TRACK_REPUTATION_THRESHOLD (80.0)
        - probation period has ended
        - status is 'active'
        """
        provider = self._get_or_raise(agent_provider_id)

        if provider["status"] != "active":
            return False

        reputation = float(provider.get("reputation_score", 0.0))
        if reputation < self.FAST_TRACK_REPUTATION_THRESHOLD:
            return False

        probation_str = provider.get("probation_ends_at")
        if not probation_str:
            return False

        probation_end = datetime.fromisoformat(probation_str)
        now = datetime.now(timezone.utc)
        if now < probation_end:
            return False

        return True

    def appeal_suspension(
        self, agent_provider_id: str, reason: str,
    ) -> dict:
        """Submit an appeal for a suspended provider.

        Changes status from 'suspended' to 'pending_review' so an admin
        can re-evaluate. Stores appeal reason in metadata.

        Raises AgentProviderError if not suspended or reason is empty.
        """
        if not reason or not reason.strip():
            raise AgentProviderError("Appeal reason is required")

        provider = self._get_or_raise(agent_provider_id)
        if provider["status"] != "suspended":
            raise AgentProviderError(
                f"Only suspended providers can appeal "
                f"(current status: {provider['status']})"
            )

        import json
        now = datetime.now(timezone.utc).isoformat()
        existing_meta = dict(provider.get("metadata", {}))
        new_meta = {
            **existing_meta,
            "appeal_reason": reason.strip(),
            "appeal_submitted_at": now,
        }
        self.db.update_agent_provider(
            agent_provider_id,
            {
                "status": "pending_review",
                "updated_at": now,
                "metadata": json.dumps(new_meta),
            },
        )
        return self.db.get_agent_provider(agent_provider_id)  # type: ignore[return-value]

    def get_by_agent_id(self, agent_id: str) -> Optional[dict]:
        """Look up an agent provider by the underlying agent_id.

        Returns the provider record dict or None if not found.
        """
        if not agent_id or not agent_id.strip():
            return None
        return self.db.get_agent_provider_by_agent_id(agent_id.strip())

    # --- Static validators ---

    @staticmethod
    def validate_wallet_address(address: str) -> bool:
        """Validate Ethereum-style wallet address: ^0x[0-9a-fA-F]{40}$"""
        if not address:
            return False
        return bool(_WALLET_RE.match(address))

    @staticmethod
    def validate_did(did: str) -> bool:
        """Validate DID format: ^did:[a-z]+:[a-zA-Z0-9._:-]+$"""
        if not did:
            return False
        return bool(_DID_RE.match(did))

    # --- Internal helpers ---

    def _get_or_raise(self, agent_provider_id: str) -> dict:
        """Fetch provider by ID or raise AgentProviderError."""
        if not agent_provider_id or not agent_provider_id.strip():
            raise AgentProviderError("agent_provider_id is required")
        provider = self.db.get_agent_provider(agent_provider_id.strip())
        if not provider:
            raise AgentProviderError(
                f"Agent provider '{agent_provider_id}' not found"
            )
        return provider
