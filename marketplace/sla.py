"""
Service Level Agreement (SLA) enforcement for Agent Provider services.

Defines SLA tiers with uptime, latency, and error rate guarantees.
Monitors compliance using existing health check data from HealthMonitor.
Tracks breaches and enables automatic actions on repeated violations.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .db import Database
from .health_monitor import HealthMonitor

logger = logging.getLogger("sla")


@dataclass(frozen=True)
class SLATier:
    """SLA tier definition with performance guarantees."""
    name: str
    uptime_pct_min: float       # Minimum uptime percentage (e.g., 99.0)
    latency_p95_max_ms: int     # Maximum p95 latency in ms (e.g., 2000)
    error_rate_max_pct: float   # Maximum error rate percentage (e.g., 5.0)


# Pre-defined SLA tiers
SLA_TIERS: dict[str, SLATier] = {
    "basic": SLATier(
        name="basic",
        uptime_pct_min=95.0,
        latency_p95_max_ms=5000,
        error_rate_max_pct=10.0,
    ),
    "standard": SLATier(
        name="standard",
        uptime_pct_min=99.0,
        latency_p95_max_ms=2000,
        error_rate_max_pct=5.0,
    ),
    "premium": SLATier(
        name="premium",
        uptime_pct_min=99.9,
        latency_p95_max_ms=500,
        error_rate_max_pct=1.0,
    ),
}

DEFAULT_SLA_TIER = "basic"


@dataclass(frozen=True)
class SLAStatus:
    """Current SLA compliance status for a service."""
    service_id: str
    sla_tier: str
    compliant: bool
    uptime_pct: float
    uptime_target: float
    uptime_met: bool
    avg_latency_ms: float
    latency_target: int
    latency_met: bool
    error_rate_pct: float
    error_rate_target: float
    error_rate_met: bool
    breach_count: int
    last_checked: str


class SLAManager:
    """Enforce and monitor SLA compliance for marketplace services."""

    def __init__(self, db: Database):
        self.db = db
        self.health_monitor = HealthMonitor(db)
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create SLA tables if not exists."""
        with self.db.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS service_sla ("
                "service_id TEXT PRIMARY KEY, "
                "sla_tier TEXT NOT NULL DEFAULT 'basic', "
                "custom_uptime_min REAL, "
                "custom_latency_max INTEGER, "
                "custom_error_rate_max REAL, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sla_breaches ("
                "id TEXT PRIMARY KEY, "
                "service_id TEXT NOT NULL, "
                "provider_id TEXT NOT NULL, "
                "sla_tier TEXT NOT NULL, "
                "breach_type TEXT NOT NULL, "
                "expected_value REAL NOT NULL, "
                "actual_value REAL NOT NULL, "
                "details TEXT DEFAULT '', "
                "created_at TEXT NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sla_breaches_service "
                "ON sla_breaches(service_id, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sla_breaches_provider "
                "ON sla_breaches(provider_id, created_at)"
            )

    def set_service_sla(
        self,
        service_id: str,
        sla_tier: str = DEFAULT_SLA_TIER,
    ) -> dict:
        """Set or update the SLA tier for a service.

        Args:
            service_id: The service to configure.
            sla_tier: One of 'basic', 'standard', 'premium'.

        Returns dict with the SLA configuration.
        """
        if sla_tier not in SLA_TIERS:
            raise ValueError(
                f"Invalid SLA tier '{sla_tier}'. "
                f"Must be one of: {', '.join(SLA_TIERS.keys())}"
            )

        now = datetime.now(timezone.utc).isoformat()

        with self.db.connect() as conn:
            # Upsert
            existing = conn.execute(
                "SELECT service_id FROM service_sla WHERE service_id = ?",
                (service_id,),
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE service_sla SET sla_tier = ?, updated_at = ? "
                    "WHERE service_id = ?",
                    (sla_tier, now, service_id),
                )
            else:
                conn.execute(
                    "INSERT INTO service_sla "
                    "(service_id, sla_tier, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (service_id, sla_tier, now, now),
                )

        return {
            "service_id": service_id,
            "sla_tier": sla_tier,
            "requirements": _tier_to_dict(SLA_TIERS[sla_tier]),
        }

    def get_service_sla(self, service_id: str) -> Optional[dict]:
        """Get the SLA configuration for a service."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM service_sla WHERE service_id = ?",
                (service_id,),
            ).fetchone()

        if not row:
            return None

        tier_name = row["sla_tier"]
        tier = SLA_TIERS.get(tier_name, SLA_TIERS[DEFAULT_SLA_TIER])

        return {
            "service_id": service_id,
            "sla_tier": tier_name,
            "requirements": _tier_to_dict(tier),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def check_compliance(
        self, service_id: str, lookback_days: int = 30,
    ) -> Optional[SLAStatus]:
        """Check if a service meets its SLA requirements.

        Uses health check data from the last `lookback_days` days.
        Records breaches if any thresholds are violated.
        """
        # Get SLA config (default to basic if not set)
        sla_config = self.get_service_sla(service_id)
        tier_name = sla_config["sla_tier"] if sla_config else DEFAULT_SLA_TIER
        tier = SLA_TIERS[tier_name]

        # Get health score
        health = self.health_monitor.get_service_health_score(
            service_id, lookback_days,
        )
        if not health:
            return None

        # Check each SLA dimension
        uptime_met = health.uptime_pct >= tier.uptime_pct_min
        latency_met = health.avg_latency_ms <= tier.latency_p95_max_ms
        error_rate_met = health.error_rate_pct <= tier.error_rate_max_pct
        compliant = uptime_met and latency_met and error_rate_met

        # Record breaches
        breaches_recorded = 0
        service = self.db.get_service(service_id)
        provider_id = service["provider_id"] if service else ""

        if not uptime_met:
            self._record_breach(
                service_id, provider_id, tier_name,
                "uptime", tier.uptime_pct_min, health.uptime_pct,
            )
            breaches_recorded += 1

        if not latency_met:
            self._record_breach(
                service_id, provider_id, tier_name,
                "latency", float(tier.latency_p95_max_ms), health.avg_latency_ms,
            )
            breaches_recorded += 1

        if not error_rate_met:
            self._record_breach(
                service_id, provider_id, tier_name,
                "error_rate", tier.error_rate_max_pct, health.error_rate_pct,
            )
            breaches_recorded += 1

        # Count total breaches
        breach_count = self._count_breaches(service_id, lookback_days)

        return SLAStatus(
            service_id=service_id,
            sla_tier=tier_name,
            compliant=compliant,
            uptime_pct=health.uptime_pct,
            uptime_target=tier.uptime_pct_min,
            uptime_met=uptime_met,
            avg_latency_ms=health.avg_latency_ms,
            latency_target=tier.latency_p95_max_ms,
            latency_met=latency_met,
            error_rate_pct=health.error_rate_pct,
            error_rate_target=tier.error_rate_max_pct,
            error_rate_met=error_rate_met,
            breach_count=breach_count,
            last_checked=health.last_checked,
        )

    def get_breaches(
        self, service_id: str, limit: int = 50,
    ) -> list[dict]:
        """Get recent SLA breaches for a service."""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sla_breaches WHERE service_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (service_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_provider_sla_summary(self, provider_id: str) -> dict:
        """Get SLA compliance summary across all of a provider's services."""
        services = self.db.list_services(status="active")
        provider_services = [s for s in services if s["provider_id"] == provider_id]

        results = []
        compliant_count = 0
        for svc in provider_services:
            status = self.check_compliance(svc["id"])
            if status:
                results.append(status)
                if status.compliant:
                    compliant_count += 1

        return {
            "provider_id": provider_id,
            "total_services": len(provider_services),
            "checked_services": len(results),
            "compliant_count": compliant_count,
            "compliance_rate": (
                round(compliant_count / len(results) * 100, 1)
                if results else 0
            ),
            "services": [
                {
                    "service_id": s.service_id,
                    "sla_tier": s.sla_tier,
                    "compliant": s.compliant,
                    "breach_count": s.breach_count,
                }
                for s in results
            ],
        }

    def _record_breach(
        self,
        service_id: str,
        provider_id: str,
        sla_tier: str,
        breach_type: str,
        expected: float,
        actual: float,
    ) -> None:
        """Record an SLA breach."""
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO sla_breaches "
                "(id, service_id, provider_id, sla_tier, breach_type, "
                "expected_value, actual_value, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    service_id,
                    provider_id,
                    sla_tier,
                    breach_type,
                    expected,
                    actual,
                    now,
                ),
            )
        logger.warning(
            "SLA breach: service=%s tier=%s type=%s expected=%.1f actual=%.1f",
            service_id, sla_tier, breach_type, expected, actual,
        )

    def _count_breaches(self, service_id: str, lookback_days: int = 30) -> int:
        """Count breaches in the lookback period."""
        from datetime import timedelta
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat()
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM sla_breaches "
                "WHERE service_id = ? AND created_at >= ?",
                (service_id, cutoff),
            ).fetchone()
        return row["cnt"] if row else 0


def _tier_to_dict(tier: SLATier) -> dict:
    """Convert SLATier to dict for API responses."""
    return {
        "uptime_pct_min": tier.uptime_pct_min,
        "latency_p95_max_ms": tier.latency_p95_max_ms,
        "error_rate_max_pct": tier.error_rate_max_pct,
    }
