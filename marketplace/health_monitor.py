"""
Service Health Monitor — Platform-level automated health checks.

Periodically tests all active services and produces quality scores.
Designed to be called by cron (hourly) or on-demand via API.

Quality enforcement:
- Services below QUALITY_WARN_THRESHOLD receive a warning in logs/TG.
- Services below QUALITY_PAUSE_THRESHOLD for PAUSE_AFTER_CONSECUTIVE
  consecutive check rounds are automatically paused.
- Trend detection: services declining >15 points over 7 days trigger alerts.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from .db import Database

# --- Quality gate thresholds ---
QUALITY_WARN_THRESHOLD = 75.0  # below this: warn provider via log/TG
QUALITY_PAUSE_THRESHOLD = 60.0  # below this for N consecutive rounds: auto-pause
PAUSE_AFTER_CONSECUTIVE = 3  # number of consecutive low-quality rounds before pause
TREND_DECLINE_ALERT = 15.0  # quality drop in points over 7 days triggers alert

logger = logging.getLogger("health_monitor")


@dataclass(frozen=True)
class HealthCheckResult:
    """Result of a single service health check."""
    service_id: str
    provider_id: str
    reachable: bool
    latency_ms: int
    status_code: int
    error: str = ""
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ServiceHealthScore:
    """Aggregated health score for a service."""
    service_id: str
    provider_id: str
    uptime_pct: float  # 0-100
    avg_latency_ms: float
    error_rate_pct: float  # 0-100
    quality_score: float  # 0-100 composite
    check_count: int
    last_checked: str
    rank: Optional[int] = None  # position among all services


class HealthMonitor:
    """Run health checks on all active marketplace services."""

    TIMEOUT_SECONDS = 10.0
    MAX_CONCURRENT = 10  # limit concurrent checks to avoid overwhelming services

    def __init__(self, db: Database):
        self.db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create health_checks table if not exists."""
        with self.db.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS health_checks (
                    id TEXT PRIMARY KEY,
                    service_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    reachable INTEGER NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    status_code INTEGER DEFAULT 0,
                    error TEXT DEFAULT '',
                    checked_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_health_service
                    ON health_checks(service_id, checked_at);
                CREATE INDEX IF NOT EXISTS idx_health_provider
                    ON health_checks(provider_id, checked_at);
            """)

    async def check_service(self, service: dict) -> HealthCheckResult:
        """Check a single service's endpoint health."""
        endpoint = service["endpoint"]
        service_id = service["id"]
        provider_id = service["provider_id"]

        reachable = False
        latency_ms = 0
        status_code = 0
        error_msg = ""

        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                import time
                start = time.monotonic()
                resp = await client.get(endpoint)
                latency_ms = round((time.monotonic() - start) * 1000)
                status_code = resp.status_code
                reachable = status_code < 500
        except httpx.TimeoutException:
            latency_ms = int(self.TIMEOUT_SECONDS * 1000)
            error_msg = "timeout"
        except httpx.ConnectError:
            error_msg = "connection_refused"
        except Exception as exc:
            error_msg = f"error:{type(exc).__name__}"
            logger.warning("Health check failed for %s: %s", service_id, exc)

        result = HealthCheckResult(
            service_id=service_id,
            provider_id=provider_id,
            reachable=reachable,
            latency_ms=latency_ms,
            status_code=status_code,
            error=error_msg,
        )

        # Persist result
        self._save_result(result)
        return result

    def _save_result(self, result: HealthCheckResult) -> None:
        """Save health check result to database."""
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO health_checks
                   (id, service_id, provider_id, reachable, latency_ms,
                    status_code, error, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    result.service_id,
                    result.provider_id,
                    1 if result.reachable else 0,
                    result.latency_ms,
                    result.status_code,
                    result.error,
                    result.checked_at.isoformat(),
                ),
            )

    async def check_all_services(self) -> list[HealthCheckResult]:
        """Check all active services. Returns list of results."""
        services = self.db.list_services(status="active")
        if not services:
            logger.info("No active services to check")
            return []

        logger.info("Starting health check for %d services", len(services))
        semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)

        async def _check_with_limit(svc: dict) -> HealthCheckResult:
            async with semaphore:
                return await self.check_service(svc)

        results = await asyncio.gather(
            *[_check_with_limit(s) for s in services],
            return_exceptions=True,
        )

        # Filter out exceptions
        valid_results = []
        for r in results:
            if isinstance(r, HealthCheckResult):
                valid_results.append(r)
            else:
                logger.error("Health check exception: %s", r)

        logger.info(
            "Health check complete: %d/%d reachable",
            sum(1 for r in valid_results if r.reachable),
            len(valid_results),
        )
        return valid_results

    def get_service_health_score(
        self, service_id: str, lookback_days: int = 30,
    ) -> Optional[ServiceHealthScore]:
        """Calculate health score for a service based on recent checks."""
        cutoff = datetime.now(timezone.utc)
        from datetime import timedelta
        cutoff_str = (cutoff - timedelta(days=lookback_days)).isoformat()

        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT reachable, latency_ms, status_code, checked_at
                   FROM health_checks
                   WHERE service_id = ? AND checked_at >= ?
                   ORDER BY checked_at DESC""",
                (service_id, cutoff_str),
            ).fetchall()

        if not rows:
            return None

        total = len(rows)
        reachable_count = sum(1 for r in rows if r["reachable"])
        total_latency = sum(r["latency_ms"] for r in rows)
        error_count = sum(
            1 for r in rows
            if r["status_code"] >= 500 or not r["reachable"]
        )

        uptime_pct = round(reachable_count / total * 100, 1)
        avg_latency = round(total_latency / total, 1) if total > 0 else 0
        error_rate = round(error_count / total * 100, 1)

        # Composite quality score (0-100)
        # Weights: uptime 50%, latency 30%, error rate 20%
        latency_score = max(0, 100 - (avg_latency / 50))  # 0ms=100, 5000ms=0
        quality_score = round(
            uptime_pct * 0.5
            + latency_score * 0.3
            + (100 - error_rate) * 0.2,
            1,
        )

        # Get provider_id from first row
        provider_row = self.db.get_service(service_id)
        provider_id = provider_row["provider_id"] if provider_row else ""

        return ServiceHealthScore(
            service_id=service_id,
            provider_id=provider_id,
            uptime_pct=uptime_pct,
            avg_latency_ms=avg_latency,
            error_rate_pct=error_rate,
            quality_score=quality_score,
            check_count=total,
            last_checked=rows[0]["checked_at"],
        )

    def get_all_health_scores(
        self, lookback_days: int = 30,
    ) -> list[ServiceHealthScore]:
        """Get health scores for all active services, ranked by quality."""
        services = self.db.list_services(status="active")
        scores = []
        for svc in services:
            score = self.get_service_health_score(svc["id"], lookback_days)
            if score:
                scores.append(score)

        # Sort by quality score descending and assign ranks
        scores.sort(key=lambda s: s.quality_score, reverse=True)
        ranked = []
        for i, score in enumerate(scores, 1):
            ranked.append(ServiceHealthScore(
                service_id=score.service_id,
                provider_id=score.provider_id,
                uptime_pct=score.uptime_pct,
                avg_latency_ms=score.avg_latency_ms,
                error_rate_pct=score.error_rate_pct,
                quality_score=score.quality_score,
                check_count=score.check_count,
                last_checked=score.last_checked,
                rank=i,
            ))
        return ranked

    def get_provider_health_summary(
        self, provider_id: str, lookback_days: int = 30,
    ) -> dict:
        """Get aggregate health summary for a provider (all their services)."""
        services = self.db.list_services(status="active")
        provider_services = [s for s in services if s["provider_id"] == provider_id]

        scores = []
        for svc in provider_services:
            score = self.get_service_health_score(svc["id"], lookback_days)
            if score:
                scores.append(score)

        if not scores:
            return {
                "provider_id": provider_id,
                "service_count": len(provider_services),
                "avg_quality_score": 0,
                "avg_uptime_pct": 0,
                "avg_latency_ms": 0,
                "services": [],
            }

        return {
            "provider_id": provider_id,
            "service_count": len(provider_services),
            "avg_quality_score": round(
                sum(s.quality_score for s in scores) / len(scores), 1
            ),
            "avg_uptime_pct": round(
                sum(s.uptime_pct for s in scores) / len(scores), 1
            ),
            "avg_latency_ms": round(
                sum(s.avg_latency_ms for s in scores) / len(scores), 1
            ),
            "services": [
                {
                    "service_id": s.service_id,
                    "quality_score": s.quality_score,
                    "uptime_pct": s.uptime_pct,
                    "avg_latency_ms": s.avg_latency_ms,
                    "rank": s.rank,
                }
                for s in scores
            ],
        }

    # ------------------------------------------------------------------
    # Quality enforcement
    # ------------------------------------------------------------------

    def _ensure_quality_log_table(self) -> None:
        """Create table for tracking consecutive low-quality rounds."""
        with self.db.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS quality_enforcement_log (
                    service_id TEXT NOT NULL,
                    round_ts TEXT NOT NULL,
                    quality_score REAL NOT NULL,
                    action TEXT NOT NULL,
                    PRIMARY KEY (service_id, round_ts)
                );
                CREATE INDEX IF NOT EXISTS idx_qe_service
                    ON quality_enforcement_log(service_id, round_ts);
            """)

    def enforce_quality_gate(self, send_emails: bool = True) -> dict:
        """Evaluate all active services and enforce quality thresholds.

        When *send_emails* is True (default), sends localized notification
        emails to providers whose services are warned, paused, or declining.

        Returns a summary dict with warned, paused, and declining services.
        """
        self._ensure_quality_log_table()
        scores = self.get_all_health_scores(lookback_days=30)
        now_iso = datetime.now(timezone.utc).isoformat()

        warned: list[dict] = []
        paused: list[dict] = []
        declining: list[dict] = []

        for score in scores:
            svc = self.db.get_service(score.service_id)
            if not svc:
                continue
            svc_name = svc.get("name", score.service_id[:8])
            provider_id = svc.get("provider_id", "")

            # --- Auto-pause: consecutive low-quality ---
            if score.quality_score < QUALITY_PAUSE_THRESHOLD:
                consecutive = self._count_consecutive_low(score.service_id)
                if consecutive + 1 >= PAUSE_AFTER_CONSECUTIVE:
                    self._pause_service(score.service_id, score.quality_score, now_iso)
                    paused.append({
                        "service_id": score.service_id,
                        "name": svc_name,
                        "quality_score": score.quality_score,
                        "consecutive_low": consecutive + 1,
                    })
                    if send_emails:
                        self._send_quality_email(
                            "paused", score.service_id, svc_name,
                            provider_id, score.quality_score,
                        )
                    continue
                else:
                    self._log_quality_round(
                        score.service_id, now_iso, score.quality_score, "low"
                    )

            # --- Warn: below warn threshold ---
            if score.quality_score < QUALITY_WARN_THRESHOLD:
                warned.append({
                    "service_id": score.service_id,
                    "name": svc_name,
                    "quality_score": score.quality_score,
                })
                self._log_quality_round(
                    score.service_id, now_iso, score.quality_score, "warn"
                )
                if send_emails:
                    self._send_quality_email(
                        "warning", score.service_id, svc_name,
                        provider_id, score.quality_score,
                    )
            else:
                # Reset consecutive counter on healthy round
                self._log_quality_round(
                    score.service_id, now_iso, score.quality_score, "ok"
                )

            # --- Trend decline detection ---
            drop = self._detect_quality_decline(score.service_id)
            if drop is not None and drop >= TREND_DECLINE_ALERT:
                declining.append({
                    "service_id": score.service_id,
                    "name": svc_name,
                    "quality_score": score.quality_score,
                    "decline_7d": round(drop, 1),
                })
                if send_emails:
                    self._send_quality_email(
                        "declining", score.service_id, svc_name,
                        provider_id, score.quality_score, decline=drop,
                    )

        summary = {
            "checked": len(scores),
            "warned": warned,
            "paused": paused,
            "declining": declining,
            "timestamp": now_iso,
        }
        logger.info(
            "Quality gate: %d checked, %d warned, %d paused, %d declining",
            len(scores), len(warned), len(paused), len(declining),
        )
        return summary

    def _count_consecutive_low(self, service_id: str) -> int:
        """Count recent consecutive rounds where quality < QUALITY_PAUSE_THRESHOLD."""
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT action FROM quality_enforcement_log
                   WHERE service_id = ?
                   ORDER BY round_ts DESC
                   LIMIT ?""",
                (service_id, PAUSE_AFTER_CONSECUTIVE),
            ).fetchall()
        count = 0
        for r in rows:
            if r["action"] == "low":
                count += 1
            else:
                break
        return count

    def _log_quality_round(
        self, service_id: str, ts: str, quality: float, action: str,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO quality_enforcement_log
                   (service_id, round_ts, quality_score, action)
                   VALUES (?, ?, ?, ?)""",
                (service_id, ts, quality, action),
            )

    def _pause_service(
        self, service_id: str, quality: float, ts: str,
    ) -> None:
        """Pause a service due to sustained low quality."""
        self.db.update_service(service_id, {"status": "paused"})
        self._log_quality_round(service_id, ts, quality, "paused")
        logger.warning(
            "AUTO-PAUSED service %s — quality %.1f below %.1f for %d rounds",
            service_id, quality, QUALITY_PAUSE_THRESHOLD, PAUSE_AFTER_CONSECUTIVE,
        )

    def _detect_quality_decline(self, service_id: str) -> Optional[float]:
        """Detect quality score decline over the past 7 days.

        Returns the decline in points (positive = declining), or None if
        insufficient data.
        """
        now = datetime.now(timezone.utc)
        week_ago = (now - timedelta(days=7)).isoformat()

        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT quality_score, round_ts
                   FROM quality_enforcement_log
                   WHERE service_id = ? AND round_ts >= ?
                   ORDER BY round_ts ASC""",
                (service_id, week_ago),
            ).fetchall()

        if len(rows) < 2:
            return None

        earliest = rows[0]["quality_score"]
        latest = rows[-1]["quality_score"]
        return earliest - latest  # positive = declining

    def _send_quality_email(
        self,
        action: str,
        service_id: str,
        service_name: str,
        provider_id: str,
        quality_score: float,
        decline: float = 0.0,
    ) -> None:
        """Send quality notification email. Errors are logged, never raised."""
        try:
            from .quality_notifier import (
                notify_quality_declining,
                notify_quality_paused,
                notify_quality_warning,
            )

            if action == "warning":
                notify_quality_warning(
                    self.db, service_id, service_name, provider_id, quality_score,
                )
            elif action == "paused":
                notify_quality_paused(
                    self.db, service_id, service_name, provider_id, quality_score,
                )
            elif action == "declining":
                notify_quality_declining(
                    self.db, service_id, service_name, provider_id, quality_score,
                    decline=decline,
                )
        except Exception as exc:
            logger.warning("Quality email failed (%s/%s): %s", action, service_id[:12], exc)

    def get_platform_health_summary(self) -> dict:
        """Return a platform-wide health summary for the public status API."""
        scores = self.get_all_health_scores(lookback_days=30)
        if not scores:
            return {
                "status": "ok",
                "active_services": 0,
                "avg_quality_score": 0,
                "avg_uptime_pct": 0,
                "services_above_90": 0,
                "services_below_75": 0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        avg_quality = round(
            sum(s.quality_score for s in scores) / len(scores), 1
        )
        avg_uptime = round(
            sum(s.uptime_pct for s in scores) / len(scores), 1
        )
        above_90 = sum(1 for s in scores if s.quality_score >= 90)
        below_75 = sum(1 for s in scores if s.quality_score < 75)

        if avg_quality >= 85 and avg_uptime >= 95:
            status = "healthy"
        elif avg_quality >= 70:
            status = "degraded"
        else:
            status = "unhealthy"

        return {
            "status": status,
            "active_services": len(scores),
            "avg_quality_score": avg_quality,
            "avg_uptime_pct": avg_uptime,
            "services_above_90": above_90,
            "services_below_75": below_75,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
