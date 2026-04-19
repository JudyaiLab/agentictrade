"""
Agent Identity Management — CRUD + verification for marketplace agents.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from .db import Database
from .models import AgentIdentity


class IdentityError(Exception):
    """Identity operation errors."""


class IdentityManager:
    """Manages agent identities on the marketplace."""

    ALLOWED_IDENTITY_TYPES = {"api_key_only", "kya_jwt", "did_vc"}
    ALLOWED_STATUSES = {"active", "suspended", "deactivated"}

    def __init__(self, db: Database):
        self.db = db

    def register(
        self,
        display_name: str,
        owner_id: str,
        identity_type: str = "api_key_only",
        capabilities: list[str] | None = None,
        wallet_address: Optional[str] = None,
        metadata: dict | None = None,
    ) -> AgentIdentity:
        """Register a new agent identity."""
        if not display_name or not display_name.strip():
            raise IdentityError("Display name is required")
        if not owner_id or not owner_id.strip():
            raise IdentityError("Owner ID is required")
        if identity_type not in self.ALLOWED_IDENTITY_TYPES:
            raise IdentityError(
                f"Identity type must be one of: {self.ALLOWED_IDENTITY_TYPES}"
            )

        now = datetime.now(timezone.utc)
        agent = AgentIdentity(
            agent_id=str(uuid.uuid4()),
            display_name=display_name.strip(),
            owner_id=owner_id.strip(),
            identity_type=identity_type,
            capabilities=tuple(c.strip() for c in (capabilities or [])),
            wallet_address=wallet_address,
            verified=False,
            reputation_score=0.0,
            status="active",
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )

        self.db.insert_agent({
            "agent_id": agent.agent_id,
            "display_name": agent.display_name,
            "owner_id": agent.owner_id,
            "identity_type": agent.identity_type,
            "capabilities": list(agent.capabilities),
            "wallet_address": agent.wallet_address,
            "verified": agent.verified,
            "reputation_score": agent.reputation_score,
            "status": agent.status,
            "created_at": agent.created_at.isoformat(),
            "updated_at": agent.updated_at.isoformat(),
            "metadata": agent.metadata,
        })

        return agent

    def get(self, agent_id: str) -> Optional[AgentIdentity]:
        """Get an agent by ID."""
        row = self.db.get_agent(agent_id)
        if not row:
            return None
        return self._from_db(row)

    def list_agents(
        self,
        owner_id: str | None = None,
        status: str = "active",
        limit: int = 50,
        offset: int = 0,
    ) -> list[AgentIdentity]:
        """List agents with optional filtering."""
        rows = self.db.list_agents(
            owner_id=owner_id,
            status=status,
            limit=min(limit, 100),
            offset=max(offset, 0),
        )
        return [self._from_db(r) for r in rows]

    def update(
        self, agent_id: str, owner_id: str, **updates
    ) -> Optional[AgentIdentity]:
        """Update an agent (only owner can update)."""
        existing = self.db.get_agent(agent_id)
        if not existing:
            return None
        if existing["owner_id"] != owner_id:
            raise IdentityError("Only the agent owner can update")

        db_updates = {}
        now = datetime.now(timezone.utc).isoformat()

        if "display_name" in updates:
            name = updates["display_name"]
            if not name or not name.strip():
                raise IdentityError("Display name cannot be empty")
            db_updates["display_name"] = name.strip()

        if "capabilities" in updates:
            db_updates["capabilities"] = [
                c.strip() for c in updates["capabilities"]
            ]

        if "wallet_address" in updates:
            db_updates["wallet_address"] = updates["wallet_address"]

        if "metadata" in updates:
            db_updates["metadata"] = updates["metadata"]

        if "status" in updates:
            if updates["status"] not in self.ALLOWED_STATUSES:
                raise IdentityError(
                    f"Status must be one of: {self.ALLOWED_STATUSES}"
                )
            db_updates["status"] = updates["status"]

        db_updates["updated_at"] = now
        self.db.update_agent(agent_id, db_updates)
        return self.get(agent_id)

    def deactivate(self, agent_id: str, owner_id: str) -> bool:
        """Deactivate an agent (soft delete)."""
        existing = self.db.get_agent(agent_id)
        if not existing:
            return False
        if existing["owner_id"] != owner_id:
            raise IdentityError("Only the agent owner can deactivate")
        return self.db.delete_agent(agent_id)

    def search(self, query: str, limit: int = 20) -> list[AgentIdentity]:
        """Search agents by name or ID."""
        if not query or not query.strip():
            return []
        rows = self.db.search_agents(query.strip(), limit=min(limit, 100))
        return [self._from_db(r) for r in rows]

    def verify(self, agent_id: str) -> Optional[AgentIdentity]:
        """Mark an agent as verified (admin action)."""
        now = datetime.now(timezone.utc).isoformat()
        updated = self.db.update_agent(
            agent_id, {"verified": True, "updated_at": now}
        )
        if not updated:
            return None
        return self.get(agent_id)

    @staticmethod
    def _from_db(row: dict) -> AgentIdentity:
        return AgentIdentity(
            agent_id=row["agent_id"],
            display_name=row["display_name"],
            owner_id=row["owner_id"],
            identity_type=row.get("identity_type", "api_key_only"),
            capabilities=tuple(row.get("capabilities", [])),
            wallet_address=row.get("wallet_address"),
            verified=row.get("verified", False),
            reputation_score=row.get("reputation_score", 0.0),
            status=row.get("status", "active"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            metadata=row.get("metadata", {}),
        )
