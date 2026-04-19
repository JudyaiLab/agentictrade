"""
Reputation Engine — auto-computed agent/service reputation from usage data.
No user ratings. All scores derived from latency, uptime, error rate.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from .db import Database


class ReputationError(Exception):
    """Reputation operation errors."""


class ReputationEngine:
    """
    Computes agent/service reputation scores from usage_records.

    Scoring formula:
    - latency_score: 10.0 - (avg_latency_ms / 1000), clamped to [0, 10]
    - reliability_score: (success_rate / 10), clamped to [0, 10]
    - response_quality: (1 - error_rate/100) * 10, clamped to [0, 10]
    - overall_score: weighted average (latency 0.3, reliability 0.4, quality 0.3)
    """

    LATENCY_WEIGHT = 0.3
    RELIABILITY_WEIGHT = 0.4
    QUALITY_WEIGHT = 0.3

    def __init__(self, db: Database):
        self.db = db

    def compute_reputation(
        self,
        provider_id: str,
        service_id: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        period_label: str = "all-time",
    ) -> dict:
        """
        Compute reputation scores from usage data.

        Returns dict with individual scores + overall.
        """
        stats = self.db.get_usage_for_reputation(
            provider_id=provider_id,
            service_id=service_id,
            period_start=period_start,
            period_end=period_end,
        )

        if stats["total_calls"] == 0:
            return {
                "agent_id": provider_id,
                "service_id": service_id or "",
                "overall_score": 0.0,
                "latency_score": 0.0,
                "reliability_score": 0.0,
                "response_quality": 0.0,
                "call_count": 0,
                "period": period_label,
            }

        # Latency score: lower is better. 0ms = 10, 10000ms+ = 0
        latency_score = max(0.0, min(10.0, 10.0 - (stats["avg_latency"] / 1000)))

        # Reliability: success rate out of 10
        reliability_score = max(0.0, min(10.0, stats["success_rate"] / 10))

        # Response quality: inverse of error rate
        response_quality = max(0.0, min(10.0, (1 - stats["error_rate"] / 100) * 10))

        # Overall: weighted average
        overall_score = round(
            latency_score * self.LATENCY_WEIGHT
            + reliability_score * self.RELIABILITY_WEIGHT
            + response_quality * self.QUALITY_WEIGHT,
            2,
        )

        return {
            "agent_id": provider_id,
            "service_id": service_id or "",
            "overall_score": round(overall_score, 2),
            "latency_score": round(latency_score, 2),
            "reliability_score": round(reliability_score, 2),
            "response_quality": round(response_quality, 2),
            "call_count": stats["total_calls"],
            "period": period_label,
        }

    def save_reputation(
        self,
        provider_id: str,
        service_id: str = "",
        period_label: str = "all-time",
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> dict:
        """Compute and persist a reputation record."""
        scores = self.compute_reputation(
            provider_id=provider_id,
            service_id=service_id if service_id else None,
            period_start=period_start,
            period_end=period_end,
            period_label=period_label,
        )

        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": str(uuid.uuid4()),
            "agent_id": provider_id,
            "service_id": service_id,
            "overall_score": scores["overall_score"],
            "latency_score": scores["latency_score"],
            "reliability_score": scores["reliability_score"],
            "response_quality": scores["response_quality"],
            "call_count": scores["call_count"],
            "period": period_label,
            "created_at": now,
        }
        self.db.insert_reputation(record)

        # Update agent's reputation_score.
        # provider_id may be the agent UUID or the API key owner_id,
        # so try both lookup strategies.
        updated = self.db.update_agent(provider_id, {
            "reputation_score": scores["overall_score"],
            "updated_at": now,
        })
        if not updated:
            # provider_id is an owner_id — find agents with that owner_id
            agents = self.db.list_agents(owner_id=provider_id, limit=1)
            if agents:
                self.db.update_agent(agents[0]["agent_id"], {
                    "reputation_score": scores["overall_score"],
                    "updated_at": now,
                })

        return scores

    def get_agent_reputation(
        self, agent_id: str, period: str = "all-time"
    ) -> list[dict]:
        """Get persisted reputation records for an agent."""
        return self.db.get_reputation(agent_id, period)

    def get_service_reputation(
        self, service_id: str, period: str = "all-time"
    ) -> list[dict]:
        """Get persisted reputation records for a service."""
        return self.db.get_service_reputation(service_id, period)

    def get_leaderboard(self, limit: int = 20) -> list[dict]:
        """
        Get top agents ranked by reputation score.
        Uses agent_identities.reputation_score (updated on compute).
        """
        agents = self.db.list_agents(status="active", limit=min(limit, 100))
        # Sort by reputation_score descending
        sorted_agents = sorted(
            agents,
            key=lambda a: a.get("reputation_score", 0.0),
            reverse=True,
        )
        return [
            {
                "agent_id": a["agent_id"],
                "display_name": a["display_name"],
                "reputation_score": a.get("reputation_score", 0.0),
                "verified": a.get("verified", False),
            }
            for a in sorted_agents[:limit]
        ]
