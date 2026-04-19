"""Tests for Webhook notification system."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from marketplace.db import Database
from marketplace.webhooks import (
    WebhookManager,
    WebhookError,
    WebhookSubscription,
    WebhookDeliveryResult,
    ALLOWED_EVENTS,
    MAX_WEBHOOKS_PER_OWNER,
)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def mgr(db):
    return WebhookManager(db)


def _subscribe(mgr, owner_id="owner-1", url="https://example.com/hook",
               events=None, secret="test-secret"):
    """Helper to create a subscription with defaults."""
    return mgr.subscribe(
        owner_id=owner_id,
        url=url,
        events=events or ["service.called"],
        secret=secret,
    )


# --- Subscribe ---

class TestSubscribe:
    def test_subscribe_basic(self, mgr):
        wh = _subscribe(mgr)
        assert wh.owner_id == "owner-1"
        assert wh.url == "https://example.com/hook"
        assert wh.events == ("service.called",)
        assert wh.active is True
        assert wh.secret == "test-secret"

    def test_subscribe_multiple_events(self, mgr):
        wh = _subscribe(mgr, events=["service.called", "payment.completed"])
        assert "service.called" in wh.events
        assert "payment.completed" in wh.events

    def test_subscribe_deduplicates_events(self, mgr):
        wh = _subscribe(mgr, events=["service.called", "service.called"])
        assert wh.events == ("service.called",)

    def test_subscribe_sorts_events(self, mgr):
        wh = _subscribe(mgr, events=["settlement.completed", "payment.completed"])
        assert wh.events == ("payment.completed", "settlement.completed")

    def test_subscribe_all_allowed_events(self, mgr):
        wh = _subscribe(mgr, events=list(ALLOWED_EVENTS))
        assert len(wh.events) == len(ALLOWED_EVENTS)

    def test_subscribe_returns_id(self, mgr):
        wh = _subscribe(mgr)
        assert wh.id  # non-empty UUID

    def test_subscribe_strips_whitespace(self, mgr):
        wh = mgr.subscribe(
            owner_id="  owner-1  ",
            url="  https://example.com/hook  ",
            events=["service.called"],
            secret="  secret  ",
        )
        assert wh.owner_id == "owner-1"
        assert wh.url == "https://example.com/hook"
        assert wh.secret == "secret"

    def test_subscribe_http_url_rejected(self, mgr):
        with pytest.raises(WebhookError, match="HTTPS"):
            _subscribe(mgr, url="http://example.com/hook")

    def test_subscribe_empty_url_rejected(self, mgr):
        with pytest.raises(WebhookError, match="URL is required"):
            _subscribe(mgr, url="")

    def test_subscribe_invalid_event_rejected(self, mgr):
        with pytest.raises(WebhookError, match="Invalid events"):
            _subscribe(mgr, events=["invalid.event"])

    def test_subscribe_mixed_valid_invalid_events_rejected(self, mgr):
        with pytest.raises(WebhookError, match="Invalid events"):
            _subscribe(mgr, events=["service.called", "bad.event"])

    def test_subscribe_empty_events_rejected(self, mgr):
        with pytest.raises(WebhookError, match="At least one event"):
            mgr.subscribe(
                owner_id="owner-1",
                url="https://example.com/hook",
                events=[],
                secret="test-secret",
            )

    def test_subscribe_empty_owner_rejected(self, mgr):
        with pytest.raises(WebhookError, match="owner_id is required"):
            _subscribe(mgr, owner_id="")

    def test_subscribe_empty_secret_rejected(self, mgr):
        with pytest.raises(WebhookError, match="Secret is required"):
            _subscribe(mgr, secret="")

    def test_subscribe_whitespace_only_secret_rejected(self, mgr):
        with pytest.raises(WebhookError, match="Secret is required"):
            _subscribe(mgr, secret="   ")

    def test_subscribe_max_webhooks_enforced(self, mgr):
        for i in range(MAX_WEBHOOKS_PER_OWNER):
            _subscribe(mgr, url=f"https://example.com/hook{i}")
        with pytest.raises(WebhookError, match="Maximum"):
            _subscribe(mgr, url="https://example.com/one-too-many")

    def test_subscribe_max_webhooks_different_owners(self, mgr):
        """Different owners have independent limits."""
        for i in range(MAX_WEBHOOKS_PER_OWNER):
            _subscribe(mgr, owner_id="owner-1", url=f"https://example.com/hook{i}")
        # owner-2 should still be able to subscribe
        wh = _subscribe(mgr, owner_id="owner-2")
        assert wh.owner_id == "owner-2"


# --- Unsubscribe ---

class TestUnsubscribe:
    def test_unsubscribe_own_webhook(self, mgr):
        wh = _subscribe(mgr)
        result = mgr.unsubscribe(wh.id, "owner-1")
        assert result is True

    def test_unsubscribe_removes_from_list(self, mgr):
        wh = _subscribe(mgr)
        mgr.unsubscribe(wh.id, "owner-1")
        subs = mgr.list_subscriptions("owner-1")
        assert len(subs) == 0

    def test_unsubscribe_wrong_owner_fails(self, mgr):
        wh = _subscribe(mgr, owner_id="owner-1")
        result = mgr.unsubscribe(wh.id, "owner-2")
        assert result is False
        # Webhook should still exist
        subs = mgr.list_subscriptions("owner-1")
        assert len(subs) == 1

    def test_unsubscribe_nonexistent_fails(self, mgr):
        result = mgr.unsubscribe("nonexistent-id", "owner-1")
        assert result is False


# --- List ---

class TestListSubscriptions:
    def test_list_empty(self, mgr):
        subs = mgr.list_subscriptions("owner-1")
        assert subs == []

    def test_list_own_webhooks(self, mgr):
        _subscribe(mgr, owner_id="owner-1", url="https://example.com/hook1")
        _subscribe(mgr, owner_id="owner-1", url="https://example.com/hook2")
        _subscribe(mgr, owner_id="owner-2", url="https://example.com/hook3")
        subs = mgr.list_subscriptions("owner-1")
        assert len(subs) == 2
        for s in subs:
            assert s.owner_id == "owner-1"

    def test_list_returns_webhook_subscriptions(self, mgr):
        _subscribe(mgr)
        subs = mgr.list_subscriptions("owner-1")
        assert isinstance(subs[0], WebhookSubscription)


# --- Dispatch ---

class TestDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_sends_to_subscribers(self, mgr):
        _subscribe(mgr, events=["service.called"])
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await mgr.dispatch("service.called", {"service_id": "svc-1"})
            assert len(results) == 1
            assert results[0].success is True
            assert results[0].status_code == 200
            assert results[0].attempts == 1

    @pytest.mark.asyncio
    async def test_dispatch_hmac_signature(self, mgr):
        secret = "my-secret-key"
        _subscribe(mgr, secret=secret, events=["payment.completed"])

        captured_headers = {}
        captured_body = b""

        mock_response = MagicMock()
        mock_response.status_code = 200

        async def capture_post(url, content, headers):
            nonlocal captured_headers, captured_body
            captured_headers = dict(headers)
            captured_body = content.encode("utf-8") if isinstance(content, str) else content
            return mock_response

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=capture_post)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await mgr.dispatch("payment.completed", {"amount": "1.00"})

        # Verify HMAC signature
        assert "X-ACF-Signature" in captured_headers
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            captured_body,
            hashlib.sha256,
        ).hexdigest()
        assert captured_headers["X-ACF-Signature"] == expected_sig

    @pytest.mark.asyncio
    async def test_dispatch_includes_event_header(self, mgr):
        _subscribe(mgr, events=["service.called"])
        captured_headers = {}

        mock_response = MagicMock()
        mock_response.status_code = 200

        async def capture_post(url, content, headers):
            nonlocal captured_headers
            captured_headers = dict(headers)
            return mock_response

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=capture_post)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await mgr.dispatch("service.called", {})

        assert captured_headers.get("X-ACF-Event") == "service.called"

    @pytest.mark.asyncio
    async def test_dispatch_no_subscribers(self, mgr):
        results = await mgr.dispatch("service.called", {"test": True})
        assert results == []

    @pytest.mark.asyncio
    async def test_dispatch_unknown_event(self, mgr):
        results = await mgr.dispatch("unknown.event", {})
        assert results == []

    @pytest.mark.asyncio
    async def test_dispatch_skips_unsubscribed_events(self, mgr):
        _subscribe(mgr, events=["service.called"])

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await mgr.dispatch("payment.completed", {"amount": "1.00"})
            assert results == []

    @pytest.mark.asyncio
    async def test_dispatch_payload_structure(self, mgr):
        _subscribe(mgr, events=["service.called"])
        captured_body = None

        mock_response = MagicMock()
        mock_response.status_code = 200

        async def capture_post(url, content, headers):
            nonlocal captured_body
            captured_body = json.loads(content)
            return mock_response

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=capture_post)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await mgr.dispatch("service.called", {"service_id": "svc-1"})

        assert captured_body is not None
        assert captured_body["event"] == "service.called"
        assert captured_body["payload"] == {"service_id": "svc-1"}
        assert "timestamp" in captured_body
        assert "webhook_id" in captured_body


# --- Retry ---

class TestRetry:
    @pytest.mark.asyncio
    async def test_retry_on_server_error(self, mgr, db):
        _subscribe(mgr, events=["service.called"])

        mock_fail = MagicMock()
        mock_fail.status_code = 500
        mock_success = MagicMock()
        mock_success.status_code = 200

        call_count = 0

        async def side_effect(url, content, headers):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return mock_fail
            return mock_success

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=side_effect)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # First dispatch — attempt 1 fails, persisted as pending
            results = await mgr.dispatch("service.called", {})
            assert len(results) == 1
            assert results[0].success is False
            assert results[0].attempts == 1

            # Retry via retry_pending (attempt 2 fails, attempt 3 succeeds)
            for _ in range(2):
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE webhook_delivery_log SET next_retry_at = '2000-01-01T00:00:00+00:00' WHERE status = 'pending'"
                    )
                retry_results = await mgr.retry_pending()
                if retry_results and retry_results[0].success:
                    break

        assert retry_results[0].success is True
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self, mgr, db):
        from marketplace.webhooks import MAX_RETRIES
        _subscribe(mgr, events=["service.called"])

        mock_fail = MagicMock()
        mock_fail.status_code = 500

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_fail)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # First dispatch — attempt 1 fails
            results = await mgr.dispatch("service.called", {})
            assert len(results) == 1
            assert results[0].success is False
            assert results[0].attempts == 1

            # Exhaust remaining retries via retry_pending
            for _ in range(MAX_RETRIES - 1):
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE webhook_delivery_log SET next_retry_at = '2000-01-01T00:00:00+00:00' WHERE status = 'pending'"
                    )
                await mgr.retry_pending()

        with db.connect() as conn:
            row = conn.execute(
                "SELECT status, attempts FROM webhook_delivery_log"
            ).fetchone()
        rec = dict(row)
        assert rec["status"] == "exhausted"
        assert rec["attempts"] == MAX_RETRIES
        assert results[0].error is not None

    @pytest.mark.asyncio
    async def test_retry_on_network_error(self, mgr, db):
        import httpx as httpx_mod
        _subscribe(mgr, events=["service.called"])

        mock_success = MagicMock()
        mock_success.status_code = 200
        call_count = 0

        async def side_effect(url, content, headers):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx_mod.ConnectError("Connection refused")
            return mock_success

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=side_effect)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # First dispatch — attempt 1 fails with network error
            results = await mgr.dispatch("service.called", {})
            assert len(results) == 1
            assert results[0].success is False
            assert results[0].attempts == 1

            # Retry via retry_pending — attempt 2 succeeds
            with db.connect() as conn:
                conn.execute(
                    "UPDATE webhook_delivery_log SET next_retry_at = '2000-01-01T00:00:00+00:00' WHERE status = 'pending'"
                )
            retry_results = await mgr.retry_pending()

        assert len(retry_results) == 1
        assert retry_results[0].success is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_exponential_backoff(self, mgr, db):
        from marketplace.webhooks import MAX_RETRIES
        _subscribe(mgr, events=["service.called"])

        mock_fail = MagicMock()
        mock_fail.status_code = 500

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_fail)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # First dispatch — attempt 1 fails
            await mgr.dispatch("service.called", {})

            # Check that the delivery log has next_retry_at set with backoff
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT next_retry_at, attempts FROM webhook_delivery_log WHERE status = 'pending'"
                ).fetchone()
            rec = dict(row)
            assert rec["attempts"] == 1
            # next_retry_at should be in the future (backoff applied)
            from datetime import datetime, timezone
            next_retry = datetime.fromisoformat(rec["next_retry_at"])
            assert next_retry > datetime.now(timezone.utc)


# --- Dispatch to multiple subscribers ---

class TestMultipleSubscribers:
    @pytest.mark.asyncio
    async def test_dispatch_to_multiple(self, mgr):
        _subscribe(mgr, owner_id="owner-1", url="https://example.com/hook1",
                   events=["service.called"])
        _subscribe(mgr, owner_id="owner-2", url="https://example.com/hook2",
                   events=["service.called"])

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await mgr.dispatch("service.called", {"test": True})

        assert len(results) == 2
        assert all(r.success for r in results)


# --- DB-level webhook operations ---

class TestDBWebhookOps:
    def test_insert_and_get(self, db):
        wh_id = str(uuid.uuid4())
        db.insert_webhook({
            "id": wh_id,
            "owner_id": "owner-1",
            "url": "https://example.com/hook",
            "events": ["service.called"],
            "secret": "s3cret",
            "active": True,
            "created_at": "2026-01-01T00:00:00+00:00",
        })
        fetched = db.get_webhook(wh_id)
        assert fetched is not None
        assert fetched["id"] == wh_id
        assert fetched["owner_id"] == "owner-1"
        assert fetched["events"] == ["service.called"]
        assert fetched["active"] is True

    def test_get_nonexistent(self, db):
        assert db.get_webhook("nonexistent") is None

    def test_list_webhooks_by_owner(self, db):
        for i in range(3):
            db.insert_webhook({
                "id": str(uuid.uuid4()),
                "owner_id": "owner-1",
                "url": f"https://example.com/hook{i}",
                "events": ["service.called"],
                "secret": "s3cret",
                "active": True,
                "created_at": "2026-01-01T00:00:00+00:00",
            })
        db.insert_webhook({
            "id": str(uuid.uuid4()),
            "owner_id": "owner-2",
            "url": "https://example.com/other",
            "events": ["service.called"],
            "secret": "s3cret",
            "active": True,
            "created_at": "2026-01-01T00:00:00+00:00",
        })
        owner1 = db.list_webhooks("owner-1")
        assert len(owner1) == 3
        owner2 = db.list_webhooks("owner-2")
        assert len(owner2) == 1

    def test_delete_webhook_owner_scoped(self, db):
        wh_id = str(uuid.uuid4())
        db.insert_webhook({
            "id": wh_id,
            "owner_id": "owner-1",
            "url": "https://example.com/hook",
            "events": ["service.called"],
            "secret": "s3cret",
            "active": True,
            "created_at": "2026-01-01T00:00:00+00:00",
        })
        # Wrong owner can't delete
        assert db.delete_webhook(wh_id, "owner-2") is False
        # Right owner can delete
        assert db.delete_webhook(wh_id, "owner-1") is True
        assert db.get_webhook(wh_id) is None

    def test_list_webhooks_for_event(self, db):
        db.insert_webhook({
            "id": str(uuid.uuid4()),
            "owner_id": "owner-1",
            "url": "https://example.com/hook1",
            "events": ["service.called", "payment.completed"],
            "secret": "s3cret",
            "active": True,
            "created_at": "2026-01-01T00:00:00+00:00",
        })
        db.insert_webhook({
            "id": str(uuid.uuid4()),
            "owner_id": "owner-2",
            "url": "https://example.com/hook2",
            "events": ["payment.completed"],
            "secret": "s3cret",
            "active": True,
            "created_at": "2026-01-01T00:00:00+00:00",
        })
        db.insert_webhook({
            "id": str(uuid.uuid4()),
            "owner_id": "owner-3",
            "url": "https://example.com/hook3",
            "events": ["service.called"],
            "secret": "s3cret",
            "active": False,
            "created_at": "2026-01-01T00:00:00+00:00",
        })
        # service.called: hook1 (active) matches, hook3 has it but is inactive
        svc_hooks = db.list_webhooks_for_event("service.called")
        assert len(svc_hooks) == 1
        # payment.completed: hook1 and hook2
        pay_hooks = db.list_webhooks_for_event("payment.completed")
        assert len(pay_hooks) == 2


# --- WebhookSubscription dataclass ---

class TestWebhookSubscription:
    def test_frozen(self, mgr):
        wh = _subscribe(mgr)
        with pytest.raises(AttributeError):
            wh.url = "https://other.com"

    def test_created_at_set(self, mgr):
        wh = _subscribe(mgr)
        assert wh.created_at is not None


# --- WebhookDeliveryResult dataclass ---

class TestWebhookDeliveryResult:
    def test_success_result(self):
        r = WebhookDeliveryResult(
            webhook_id="wh-1",
            url="https://example.com",
            event="service.called",
            success=True,
            status_code=200,
            attempts=1,
        )
        assert r.success is True
        assert r.error is None

    def test_failure_result(self):
        r = WebhookDeliveryResult(
            webhook_id="wh-1",
            url="https://example.com",
            event="service.called",
            success=False,
            status_code=0,
            attempts=3,
            error="Connection refused",
        )
        assert r.success is False
        assert r.error == "Connection refused"

    def test_frozen(self):
        r = WebhookDeliveryResult()
        with pytest.raises(AttributeError):
            r.success = True


# --- New event types (provider + escrow lifecycle) ---

class TestNewEventTypes:
    """Verify the new webhook event types added for TA evaluation Round 2."""

    def test_provider_activated_is_allowed(self):
        assert "provider.activated" in ALLOWED_EVENTS

    def test_provider_suspended_is_allowed(self):
        assert "provider.suspended" in ALLOWED_EVENTS

    def test_escrow_dispute_opened_is_allowed(self):
        assert "escrow.dispute_opened" in ALLOWED_EVENTS

    def test_escrow_released_is_allowed(self):
        assert "escrow.released" in ALLOWED_EVENTS

    def test_subscribe_to_new_events(self, mgr):
        wh = _subscribe(mgr, events=["provider.activated", "escrow.released"])
        assert "provider.activated" in wh.events
        assert "escrow.released" in wh.events

    @pytest.mark.asyncio
    async def test_dispatch_provider_suspended(self, mgr):
        _subscribe(mgr, events=["provider.suspended"])
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await mgr.dispatch("provider.suspended", {
                "provider_id": "prov-1", "reason": "policy violation",
            })
            assert len(results) == 1
            assert results[0].success is True

    @pytest.mark.asyncio
    async def test_dispatch_escrow_dispute_opened(self, mgr):
        _subscribe(mgr, events=["escrow.dispute_opened"])
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await mgr.dispatch("escrow.dispute_opened", {
                "hold_id": "hold-1", "buyer_id": "buyer-1",
            })
            assert len(results) == 1
            assert results[0].success is True

    def test_total_allowed_events_count(self):
        assert len(ALLOWED_EVENTS) == 8
