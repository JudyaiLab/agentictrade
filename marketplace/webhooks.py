"""
Webhook notification system for Agent Commerce Framework.
Dispatches event notifications to subscriber endpoints with HMAC-SHA256 signatures.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx

from .db import Database

logger = logging.getLogger("webhooks")

# Internal hosts allowed to bypass SSRF protection (platform-owned services)
_INTERNAL_ALLOWED = set(
    h.strip() for h in os.environ.get("ACF_INTERNAL_HOSTS", "").split(",")
) - {""}


ALLOWED_EVENTS = frozenset({
    "service.called",
    "payment.completed",
    "reputation.updated",
    "settlement.completed",
    "provider.activated",
    "provider.suspended",
    "escrow.dispute_opened",
    "escrow.released",
})

MAX_WEBHOOKS_PER_OWNER = 20
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0


class WebhookError(Exception):
    """Webhook operation errors."""


@dataclass(frozen=True)
class WebhookSubscription:
    """Immutable webhook subscription."""
    id: str = ""
    owner_id: str = ""
    url: str = ""
    events: tuple[str, ...] = ()
    secret: str = ""
    active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class WebhookDeliveryResult:
    """Result of a single webhook delivery attempt."""
    webhook_id: str = ""
    url: str = ""
    event: str = ""
    success: bool = False
    status_code: int = 0
    attempts: int = 0
    error: Optional[str] = None


class WebhookManager:
    """
    Manages webhook subscriptions and event dispatching.

    Supports:
    - CRUD for webhook subscriptions
    - HMAC-SHA256 signed payloads
    - Async dispatch with DB-backed delivery log and retry queue
    """

    def __init__(self, db: Database):
        self.db = db

    def subscribe(
        self,
        owner_id: str,
        url: str,
        events: list[str],
        secret: str,
    ) -> WebhookSubscription:
        """
        Create a new webhook subscription.

        Validates URL (must be https://), events (must be from allowed set),
        and enforces per-owner webhook limit.
        """
        if not owner_id or not owner_id.strip():
            raise WebhookError("owner_id is required")
        owner_id = owner_id.strip()

        if not url or not url.strip():
            raise WebhookError("URL is required")
        url = url.strip()
        if not url.startswith("https://"):
            raise WebhookError("Webhook URL must use HTTPS (https://)")

        # Fail-fast SSRF check: reject URLs that resolve to private/loopback IPs
        import ipaddress
        import socket
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            if parsed.hostname:
                addrs = socket.getaddrinfo(parsed.hostname, None)
                for addr_info in addrs:
                    try:
                        resolved_ip = ipaddress.ip_address(addr_info[4][0])
                        if (resolved_ip.is_private or resolved_ip.is_loopback
                                or resolved_ip.is_link_local or resolved_ip.is_reserved):
                            raise WebhookError(
                                "Webhook URL resolves to a private/reserved IP address"
                            )
                    except ValueError:
                        pass
        except WebhookError:
            raise
        except Exception:
            pass  # DNS resolution failure — will fail at delivery time

        if not events:
            raise WebhookError("At least one event is required")
        invalid = set(events) - ALLOWED_EVENTS
        if invalid:
            raise WebhookError(
                f"Invalid events: {invalid}. Allowed: {sorted(ALLOWED_EVENTS)}"
            )

        if not secret or not secret.strip():
            raise WebhookError("Secret is required for HMAC signing")
        secret = secret.strip()

        # Check per-owner limit
        existing = self.db.list_webhooks(owner_id)
        if len(existing) >= MAX_WEBHOOKS_PER_OWNER:
            raise WebhookError(
                f"Maximum {MAX_WEBHOOKS_PER_OWNER} webhooks per owner"
            )

        now = datetime.now(timezone.utc)
        webhook = WebhookSubscription(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            url=url,
            events=tuple(sorted(set(events))),
            secret=secret,
            active=True,
            created_at=now,
        )

        self.db.insert_webhook({
            "id": webhook.id,
            "owner_id": webhook.owner_id,
            "url": webhook.url,
            "events": list(webhook.events),
            "secret": webhook.secret,
            "active": webhook.active,
            "created_at": webhook.created_at.isoformat(),
        })

        return webhook

    def unsubscribe(self, webhook_id: str, owner_id: str) -> bool:
        """
        Delete a webhook subscription.
        Owner-scoped: only the owner can delete their webhooks.
        """
        return self.db.delete_webhook(webhook_id, owner_id)

    def list_subscriptions(self, owner_id: str) -> list[WebhookSubscription]:
        """List all webhooks owned by the given owner."""
        rows = self.db.list_webhooks(owner_id)
        return [self._from_db(r) for r in rows]

    def get_delivery_status(self, delivery_id: str) -> dict | None:
        """Return the delivery log record for a given delivery_id, or None."""
        return self.db.get_delivery_status(delivery_id)

    def get_delivery_history(
        self,
        subscription_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """
        Query webhook delivery history from webhook_delivery_log.

        Args:
            subscription_id: Filter by webhook subscription ID (optional).
            status: Filter by delivery status — 'pending', 'delivered',
                    'exhausted' (optional).
            limit: Maximum number of records to return (default 50).

        Returns:
            List of delivery log dicts ordered by created_at DESC.
        """
        return self.db.get_delivery_history(
            subscription_id=subscription_id,
            status=status,
            limit=limit,
        )

    async def dispatch(
        self,
        event: str,
        payload: dict,
    ) -> list[WebhookDeliveryResult]:
        """
        Dispatch an event to all subscribed webhooks.

        Creates a delivery log record per subscriber, then attempts a single
        HTTP POST. On success the record is marked 'delivered'. On failure
        the record is left as 'pending' with a scheduled next_retry_at for
        the retry_pending() loop to pick up later.
        """
        if event not in ALLOWED_EVENTS:
            logger.warning("Attempted to dispatch unknown event: %s", event)
            return []

        subscribers = self.db.list_webhooks_for_event(event)
        if not subscribers:
            return []

        tasks = [
            self._deliver(sub, event, payload)
            for sub in subscribers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        delivery_results = []
        for r in results:
            if isinstance(r, Exception):
                logger.error("Webhook delivery exception: %s", r)
            else:
                delivery_results.append(r)

        return delivery_results

    async def retry_pending(self) -> list[WebhookDeliveryResult]:
        """Retry all pending delivery records whose next_retry_at is in the past."""
        now_iso = datetime.now(timezone.utc).isoformat()
        pending = self.db.list_pending_deliveries(now_iso)
        if not pending:
            return []

        results: list[WebhookDeliveryResult] = []
        for record in pending:
            subscription_id = record["subscription_id"]
            webhook = self.db.get_webhook(subscription_id)
            if not webhook:
                # Subscription deleted — mark exhausted
                self.db.update_delivery_log(record["id"], {
                    "status": "exhausted",
                    "last_error": "subscription not found",
                })
                continue

            result = await self._attempt_delivery(
                record=record,
                webhook=webhook,
            )
            results.append(result)

        return results

    @staticmethod
    def _check_ssrf(url: str) -> None:
        """Block webhook delivery to private/internal IPs (SSRF protection)."""
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            raise WebhookError("Invalid webhook URL")
        if hostname in _INTERNAL_ALLOWED:
            return
        try:
            addrs = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            raise WebhookError(f"DNS resolution failed for {hostname}")
        for addr_info in addrs:
            try:
                resolved_ip = ipaddress.ip_address(addr_info[4][0])
                if (
                    resolved_ip.is_private
                    or resolved_ip.is_loopback
                    or resolved_ip.is_link_local
                    or resolved_ip.is_reserved
                ):
                    raise WebhookError("Webhook URL resolves to a private address")
            except ValueError:
                pass  # Non-IP address format, skip

    async def _deliver(
        self,
        webhook: dict,
        event: str,
        payload: dict,
    ) -> WebhookDeliveryResult:
        """Deliver a single webhook: create delivery log record, attempt once.

        On success the record is marked 'delivered'. On failure the record
        is left as 'pending' with a scheduled next_retry_at for the
        retry_pending() loop to pick up later.
        """
        # SSRF check: block delivery to private/internal IPs
        try:
            self._check_ssrf(webhook["url"])
        except WebhookError as e:
            logger.warning("SSRF blocked webhook %s: %s", webhook["id"], e)
            return WebhookDeliveryResult(
                webhook_id=webhook["id"],
                url=webhook["url"],
                event=event,
                success=False,
                status_code=0,
                attempts=0,
                error=f"SSRF blocked: {e}",
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        delivery_id = str(uuid.uuid4())

        # Create the delivery log record before the HTTP call
        self.db.insert_delivery_log({
            "id": delivery_id,
            "subscription_id": webhook["id"],
            "event_type": event,
            "payload": json.dumps(payload, default=str),
            "status": "pending",
            "attempts": 0,
            "max_retries": MAX_RETRIES,
            "next_retry_at": now_iso,
            "last_error": None,
            "created_at": now_iso,
            "updated_at": now_iso,
        })

        record = self.db.get_delivery_status(delivery_id)
        return await self._attempt_delivery(record=record, webhook=webhook)

    async def _attempt_delivery(
        self,
        record: dict,
        webhook: dict,
    ) -> WebhookDeliveryResult:
        """Make a single HTTP attempt for a delivery record and update the DB."""
        delivery_id = record["id"]
        event = record["event_type"]
        payload = json.loads(record["payload"]) if isinstance(record["payload"], str) else record["payload"]
        current_attempts = record["attempts"]
        max_retries = record.get("max_retries", MAX_RETRIES)

        body = json.dumps({
            "event": event,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "webhook_id": webhook["id"],
        }, default=str)

        signature = hmac.new(
            webhook["secret"].encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-ACF-Signature": signature,
            "X-ACF-Event": event,
        }

        new_attempts = current_attempts + 1
        last_error: Optional[str] = None
        success = False
        status_code = 0

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    webhook["url"],
                    content=body,
                    headers=headers,
                )
                status_code = response.status_code
                if response.status_code < 300:
                    success = True
                    logger.info(
                        "Webhook delivered: %s -> %s (attempt %d)",
                        event, webhook["url"], new_attempts,
                    )
                else:
                    last_error = f"HTTP {response.status_code}"
                    logger.warning(
                        "Webhook delivery failed: %s -> %s (attempt %d, %s)",
                        event, webhook["url"], new_attempts, last_error,
                    )
        except httpx.HTTPError as e:
            last_error = str(e)
            logger.warning(
                "Webhook delivery error: %s -> %s (attempt %d, %s)",
                event, webhook["url"], new_attempts, last_error,
            )

        # Update the delivery log
        if success:
            self.db.update_delivery_log(delivery_id, {
                "status": "delivered",
                "attempts": new_attempts,
                "last_error": last_error,
            })
        elif new_attempts >= max_retries:
            self.db.update_delivery_log(delivery_id, {
                "status": "exhausted",
                "attempts": new_attempts,
                "last_error": last_error,
            })
        else:
            # Schedule retry with exponential backoff
            backoff = INITIAL_BACKOFF_SECONDS * (2 ** (new_attempts - 1))
            next_retry = (
                datetime.now(timezone.utc) + timedelta(seconds=backoff)
            ).isoformat()
            self.db.update_delivery_log(delivery_id, {
                "status": "pending",
                "attempts": new_attempts,
                "next_retry_at": next_retry,
                "last_error": last_error,
            })

        return WebhookDeliveryResult(
            webhook_id=webhook["id"],
            url=webhook["url"],
            event=event,
            success=success,
            status_code=status_code,
            attempts=new_attempts,
            error=last_error,
        )

    @staticmethod
    def _from_db(row: dict) -> WebhookSubscription:
        return WebhookSubscription(
            id=row["id"],
            owner_id=row["owner_id"],
            url=row["url"],
            events=tuple(row.get("events", [])),
            secret=row["secret"],
            active=row.get("active", True),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
