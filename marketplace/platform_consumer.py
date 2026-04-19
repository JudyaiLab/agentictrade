"""
Platform Agent Consumer — Generates real usage data for all marketplace services.

The platform periodically calls registered services to:
1. Generate usage data so providers see real API traffic
2. Feed health monitoring with fresh data points
3. Maintain service quality scores even without external buyers

Designed for cron execution (2-3x daily).
Target: 100+ calls/month per service.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

from .db import Database

logger = logging.getLogger("platform_consumer")

PLATFORM_BUYER_ID = "platform-agent"
PLATFORM_USER_AGENT = "AgenticTrade-Platform/1.0"
REQUEST_TIMEOUT = 15.0
MAX_CONCURRENT = 5


class PlatformConsumer:
    """Call marketplace services to generate usage data."""

    def __init__(self, db: Database):
        self.db = db

    def _get_active_services(self) -> list[dict]:
        """Get all active services eligible for platform consumption."""
        services = self.db.list_services(status="active")
        # Skip example/placeholder services
        return [
            s for s in services
            if s.get("endpoint", "").startswith("http")
            and "example.com" not in s.get("endpoint", "")
        ]

    async def call_service(self, service: dict) -> dict:
        """
        Make a test call to a service endpoint.

        Uses GET to the base endpoint. Records the result as a usage record.
        """
        endpoint = service["endpoint"]
        service_id = service["id"]
        provider_id = service["provider_id"]
        price = float(service.get("price_per_call", 0))

        status_code = 0
        latency_ms = 0
        error_msg = ""

        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT,
                follow_redirects=False,
            ) as client:
                start = time.monotonic()
                resp = await client.get(
                    endpoint,
                    headers={"User-Agent": PLATFORM_USER_AGENT},
                )
                latency_ms = round((time.monotonic() - start) * 1000)
                status_code = resp.status_code

        except httpx.TimeoutException:
            latency_ms = int(REQUEST_TIMEOUT * 1000)
            status_code = 504
            error_msg = "timeout"
        except httpx.ConnectError:
            status_code = 502
            error_msg = "connection_refused"
        except Exception as exc:
            status_code = 500
            error_msg = str(exc)[:200]

        # Record usage (free — platform consumption doesn't charge)
        record_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO usage_records
                   (id, service_id, buyer_id, provider_id, amount_usd,
                    status_code, latency_ms, timestamp, payment_tx, payment_method)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    service_id,
                    PLATFORM_BUYER_ID,
                    provider_id,
                    0.0,  # Platform calls are free
                    status_code,
                    latency_ms,
                    now,
                    f"platform:{record_id}",
                    "platform",
                ),
            )

        return {
            "service_id": service_id,
            "provider_id": provider_id,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "error": error_msg,
            "recorded": True,
        }

    async def consume_all(self) -> list[dict]:
        """
        Call all eligible services and record results.

        Returns list of call results.
        """
        services = self._get_active_services()
        if not services:
            logger.info("No eligible services for platform consumption")
            return []

        logger.info("Platform consuming %d services", len(services))
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def _call_with_limit(svc: dict) -> dict:
            async with semaphore:
                return await self.call_service(svc)

        results = await asyncio.gather(
            *[_call_with_limit(s) for s in services],
            return_exceptions=True,
        )

        valid = []
        for r in results:
            if isinstance(r, dict):
                valid.append(r)
            else:
                logger.error("Platform consumer exception: %s", r)

        successful = sum(1 for r in valid if r.get("status_code", 0) < 500)
        logger.info(
            "Platform consumption complete: %d/%d successful",
            successful, len(valid),
        )
        return valid

    def get_consumption_stats(self, days: int = 30) -> dict:
        """Get platform consumption statistics."""
        from datetime import timedelta
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()

        with self.db.connect() as conn:
            total = conn.execute(
                """SELECT COUNT(*) FROM usage_records
                   WHERE buyer_id = ? AND timestamp >= ?""",
                (PLATFORM_BUYER_ID, cutoff),
            ).fetchone()[0]

            per_service = conn.execute(
                """SELECT service_id, COUNT(*) as calls,
                          AVG(latency_ms) as avg_latency
                   FROM usage_records
                   WHERE buyer_id = ? AND timestamp >= ?
                   GROUP BY service_id""",
                (PLATFORM_BUYER_ID, cutoff),
            ).fetchall()

        return {
            "period_days": days,
            "total_calls": total,
            "services_consumed": len(per_service),
            "per_service": [
                {
                    "service_id": row["service_id"],
                    "calls": row["calls"],
                    "avg_latency_ms": round(row["avg_latency"], 1),
                }
                for row in per_service
            ],
        }
