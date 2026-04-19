"""Tests for webhook delivery log persistence and DB-backed retry queue."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marketplace.db import Database
from marketplace.webhooks import (
    MAX_RETRIES,
    WebhookManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test_persistence.db")


@pytest.fixture
def mgr(db):
    return WebhookManager(db)


def _subscribe(mgr, owner_id="owner-1", url="https://example.com/hook",
               events=None, secret="test-secret-value-1"):
    return mgr.subscribe(
        owner_id=owner_id,
        url=url,
        events=events or ["service.called"],
        secret=secret,
    )


def _mock_http_client(status_code=200):
    """Return a context-manager-compatible AsyncMock that responds with status_code."""
    mock_response = MagicMock()
    mock_response.status_code = status_code

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ---------------------------------------------------------------------------
# 1. Delivery record created on dispatch
# ---------------------------------------------------------------------------


class TestDeliveryRecordCreatedOnDispatch:
    @pytest.mark.asyncio
    async def test_delivery_record_inserted_before_http_call(self, mgr, db):
        """A delivery log record must exist in the DB before the HTTP call."""
        _subscribe(mgr, events=["service.called"])

        inserted_ids: list[str] = []

        original_insert = db.insert_delivery_log

        def capturing_insert(record):
            inserted_ids.append(record["id"])
            return original_insert(record)

        with patch.object(db, "insert_delivery_log", side_effect=capturing_insert):
            with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
                mock_cls.return_value = _mock_http_client(200)
                with patch("marketplace.webhooks.asyncio.sleep", new_callable=AsyncMock):
                    results = await mgr.dispatch("service.called", {"key": "value"})

        assert len(results) == 1
        assert len(inserted_ids) == 1

    @pytest.mark.asyncio
    async def test_delivery_record_has_correct_fields(self, mgr, db):
        """Inserted delivery log record must have the expected field values."""
        _subscribe(mgr, events=["payment.completed"])

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_http_client(200)
            with patch("marketplace.webhooks.asyncio.sleep", new_callable=AsyncMock):
                await mgr.dispatch("payment.completed", {"amount": "5.00"})

        # Fetch all delivery log records via DB method
        now_future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        records = db.list_pending_deliveries(now_future)
        # After successful delivery the record is 'delivered', not 'pending'
        # so we check directly via get_delivery_log
        # We need the id — find it by querying the DB directly
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM webhook_delivery_log"
            ).fetchall()
        assert len(rows) == 1
        rec = dict(rows[0])
        assert rec["event_type"] == "payment.completed"
        assert json.loads(rec["payload"]) == {"amount": "5.00"}

    @pytest.mark.asyncio
    async def test_one_record_per_subscriber(self, mgr, db):
        """One delivery log record is created per subscribed webhook."""
        _subscribe(mgr, owner_id="owner-1", url="https://example.com/hook1",
                   events=["service.called"])
        _subscribe(mgr, owner_id="owner-2", url="https://example.com/hook2",
                   events=["service.called"])

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_http_client(200)
            with patch("marketplace.webhooks.asyncio.sleep", new_callable=AsyncMock):
                results = await mgr.dispatch("service.called", {})

        assert len(results) == 2
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM webhook_delivery_log"
            ).fetchall()
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_no_record_for_unknown_event(self, mgr, db):
        """No delivery log record is created when the event is not in ALLOWED_EVENTS."""
        await mgr.dispatch("unknown.event", {})

        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM webhook_delivery_log"
            ).fetchall()
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# 2. Failed delivery updates attempts count and next_retry_at
# ---------------------------------------------------------------------------


class TestFailedDeliveryUpdatesLog:
    @pytest.mark.asyncio
    async def test_failed_attempt_increments_attempts(self, mgr, db):
        """After a failed first attempt, attempts count should be 1 and status pending.
        The method returns immediately (non-blocking); retry_pending handles retries."""
        _subscribe(mgr, events=["service.called"])

        mock_fail = MagicMock()
        mock_fail.status_code = 500

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_fail)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client
            results = await mgr.dispatch("service.called", {})

        # First attempt fails — returns immediately with pending status
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].attempts == 1

        with db.connect() as conn:
            row = conn.execute(
                "SELECT attempts, status FROM webhook_delivery_log"
            ).fetchone()
        assert dict(row)["attempts"] == 1
        assert dict(row)["status"] == "pending"

    @pytest.mark.asyncio
    async def test_failed_attempt_sets_next_retry_at(self, mgr, db):
        """After a failed attempt (not exhausted), next_retry_at must be set in the future."""
        _subscribe(mgr, events=["service.called"])

        mock_fail = MagicMock()
        mock_fail.status_code = 503
        mock_success = MagicMock()
        mock_success.status_code = 200

        call_count = 0
        captured_next_retry: list[str] = []

        original_update = db.update_delivery_log

        def capturing_update(delivery_id, updates):
            if "next_retry_at" in updates and updates.get("status") == "pending":
                captured_next_retry.append(updates["next_retry_at"])
            return original_update(delivery_id, updates)

        async def side_effect(url, content, headers):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_fail
            return mock_success

        with patch.object(db, "update_delivery_log", side_effect=capturing_update):
            with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
                client = AsyncMock()
                client.post = AsyncMock(side_effect=side_effect)
                client.__aenter__ = AsyncMock(return_value=client)
                client.__aexit__ = AsyncMock(return_value=False)
                mock_cls.return_value = client
                with patch("marketplace.webhooks.asyncio.sleep", new_callable=AsyncMock):
                    await mgr.dispatch("service.called", {})

        assert len(captured_next_retry) >= 1
        next_retry = datetime.fromisoformat(captured_next_retry[0])
        assert next_retry > datetime.now(timezone.utc)

    @pytest.mark.asyncio
    async def test_last_error_recorded_on_failure(self, mgr, db):
        """last_error field must be populated after a failed HTTP attempt."""
        _subscribe(mgr, events=["service.called"])

        mock_fail = MagicMock()
        mock_fail.status_code = 502

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_http_client(502)
            with patch("marketplace.webhooks.asyncio.sleep", new_callable=AsyncMock):
                await mgr.dispatch("service.called", {})

        with db.connect() as conn:
            row = conn.execute(
                "SELECT last_error FROM webhook_delivery_log"
            ).fetchone()
        last_error = dict(row)["last_error"]
        assert last_error is not None
        assert "502" in last_error


# ---------------------------------------------------------------------------
# 3. Successful delivery sets status='delivered'
# ---------------------------------------------------------------------------


class TestSuccessfulDeliverySetsStatus:
    @pytest.mark.asyncio
    async def test_status_delivered_on_success(self, mgr, db):
        """Delivery log status must be 'delivered' after a successful HTTP 200."""
        _subscribe(mgr, events=["service.called"])

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_http_client(200)
            with patch("marketplace.webhooks.asyncio.sleep", new_callable=AsyncMock):
                results = await mgr.dispatch("service.called", {})

        assert results[0].success is True

        with db.connect() as conn:
            row = conn.execute(
                "SELECT status, attempts FROM webhook_delivery_log"
            ).fetchone()
        rec = dict(row)
        assert rec["status"] == "delivered"
        assert rec["attempts"] == 1

    @pytest.mark.asyncio
    async def test_status_delivered_on_http_201(self, mgr, db):
        """2xx responses (e.g. 201) also count as success."""
        _subscribe(mgr, events=["payment.completed"])

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_http_client(201)
            with patch("marketplace.webhooks.asyncio.sleep", new_callable=AsyncMock):
                results = await mgr.dispatch("payment.completed", {})

        assert results[0].success is True
        with db.connect() as conn:
            row = conn.execute(
                "SELECT status FROM webhook_delivery_log"
            ).fetchone()
        assert dict(row)["status"] == "delivered"

    @pytest.mark.asyncio
    async def test_get_delivery_status_returns_delivered(self, mgr, db):
        """get_delivery_status() returns the record with status='delivered'."""
        _subscribe(mgr, events=["service.called"])

        delivery_ids: list[str] = []
        original_insert = db.insert_delivery_log

        def capturing_insert(record):
            delivery_ids.append(record["id"])
            return original_insert(record)

        with patch.object(db, "insert_delivery_log", side_effect=capturing_insert):
            with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
                mock_cls.return_value = _mock_http_client(200)
                with patch("marketplace.webhooks.asyncio.sleep", new_callable=AsyncMock):
                    await mgr.dispatch("service.called", {})

        assert len(delivery_ids) == 1
        status_record = mgr.get_delivery_status(delivery_ids[0])
        assert status_record is not None
        assert status_record["status"] == "delivered"

    @pytest.mark.asyncio
    async def test_get_delivery_status_returns_none_for_unknown(self, mgr):
        """get_delivery_status() returns None for a non-existent delivery_id."""
        result = mgr.get_delivery_status("nonexistent-id")
        assert result is None


# ---------------------------------------------------------------------------
# 4. Exhausted retries set status='exhausted'
# ---------------------------------------------------------------------------


class TestExhaustedRetries:
    @pytest.mark.asyncio
    async def test_all_retries_exhausted_sets_status(self, mgr, db):
        """When all retries fail across dispatch + retry_pending, status is 'exhausted'."""
        _subscribe(mgr, events=["service.called"])

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_http_client(500)

            # First dispatch — attempt 1 fails, returns pending
            results = await mgr.dispatch("service.called", {})
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

    @pytest.mark.asyncio
    async def test_exhausted_has_last_error(self, mgr, db):
        """Exhausted records must have a non-null last_error."""
        _subscribe(mgr, events=["service.called"])

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_http_client(503)

            await mgr.dispatch("service.called", {})

            # Exhaust remaining retries
            for _ in range(MAX_RETRIES - 1):
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE webhook_delivery_log SET next_retry_at = '2000-01-01T00:00:00+00:00' WHERE status = 'pending'"
                    )
                await mgr.retry_pending()

        with db.connect() as conn:
            row = conn.execute(
                "SELECT last_error, status FROM webhook_delivery_log"
            ).fetchone()
        rec = dict(row)
        assert rec["status"] == "exhausted"
        assert rec["last_error"] is not None

    @pytest.mark.asyncio
    async def test_get_delivery_status_returns_exhausted(self, mgr, db):
        """get_delivery_status() reflects exhausted status correctly."""
        _subscribe(mgr, events=["reputation.updated"])

        delivery_ids: list[str] = []
        original_insert = db.insert_delivery_log

        def capturing_insert(record):
            delivery_ids.append(record["id"])
            return original_insert(record)

        with patch.object(db, "insert_delivery_log", side_effect=capturing_insert):
            with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
                mock_cls.return_value = _mock_http_client(500)

                await mgr.dispatch("reputation.updated", {})

                # Exhaust remaining retries
                for _ in range(MAX_RETRIES - 1):
                    with db.connect() as conn:
                        conn.execute(
                            "UPDATE webhook_delivery_log SET next_retry_at = '2000-01-01T00:00:00+00:00' WHERE status = 'pending'"
                        )
                    await mgr.retry_pending()

        assert len(delivery_ids) == 1
        status_record = mgr.get_delivery_status(delivery_ids[0])
        assert status_record is not None
        assert status_record["status"] == "exhausted"


# ---------------------------------------------------------------------------
# 5. retry_pending picks up due records
# ---------------------------------------------------------------------------


class TestRetryPending:
    @pytest.mark.asyncio
    async def test_retry_pending_picks_up_due_records(self, mgr, db):
        """retry_pending() must process records with next_retry_at in the past."""
        sub = _subscribe(mgr, events=["service.called"])

        # Manually insert a 'pending' delivery log with next_retry_at in the past
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        delivery_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        db.insert_delivery_log({
            "id": delivery_id,
            "subscription_id": sub.id,
            "event_type": "service.called",
            "payload": json.dumps({"retried": True}),
            "status": "pending",
            "attempts": 1,
            "max_retries": MAX_RETRIES,
            "next_retry_at": past,
            "last_error": "HTTP 503",
            "created_at": now_iso,
            "updated_at": now_iso,
        })

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_http_client(200)
            with patch("marketplace.webhooks.asyncio.sleep", new_callable=AsyncMock):
                results = await mgr.retry_pending()

        assert len(results) == 1
        assert results[0].success is True

        status_record = mgr.get_delivery_status(delivery_id)
        assert status_record["status"] == "delivered"

    @pytest.mark.asyncio
    async def test_retry_pending_skips_future_records(self, mgr, db):
        """retry_pending() must NOT process records with next_retry_at in the future."""
        sub = _subscribe(mgr, events=["service.called"])

        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        delivery_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        db.insert_delivery_log({
            "id": delivery_id,
            "subscription_id": sub.id,
            "event_type": "service.called",
            "payload": json.dumps({}),
            "status": "pending",
            "attempts": 0,
            "max_retries": MAX_RETRIES,
            "next_retry_at": future,
            "last_error": None,
            "created_at": now_iso,
            "updated_at": now_iso,
        })

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_http_client(200)
            with patch("marketplace.webhooks.asyncio.sleep", new_callable=AsyncMock):
                results = await mgr.retry_pending()

        # No results — future record was not due
        assert results == []

        # Record should still be pending
        status_record = mgr.get_delivery_status(delivery_id)
        assert status_record["status"] == "pending"

    @pytest.mark.asyncio
    async def test_retry_pending_marks_exhausted_when_subscription_deleted(self, mgr, db):
        """retry_pending() marks records exhausted when the subscription no longer exists."""
        now_iso = datetime.now(timezone.utc).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        delivery_id = str(uuid.uuid4())
        db.insert_delivery_log({
            "id": delivery_id,
            "subscription_id": "deleted-subscription-id",
            "event_type": "service.called",
            "payload": json.dumps({}),
            "status": "pending",
            "attempts": 0,
            "max_retries": MAX_RETRIES,
            "next_retry_at": past,
            "last_error": None,
            "created_at": now_iso,
            "updated_at": now_iso,
        })

        await mgr.retry_pending()

        status_record = mgr.get_delivery_status(delivery_id)
        assert status_record["status"] == "exhausted"
        assert status_record["last_error"] == "subscription not found"

    @pytest.mark.asyncio
    async def test_retry_pending_returns_empty_when_no_due_records(self, mgr):
        """retry_pending() returns an empty list when there are no due records."""
        results = await mgr.retry_pending()
        assert results == []

    @pytest.mark.asyncio
    async def test_retry_pending_fails_updates_to_pending_with_backoff(self, mgr, db):
        """retry_pending() re-schedules a failing record with updated next_retry_at.
        Non-blocking: returns immediately after failure, sets next_retry_at for next round."""
        sub = _subscribe(mgr, events=["service.called"])

        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()
        delivery_id = str(uuid.uuid4())
        db.insert_delivery_log({
            "id": delivery_id,
            "subscription_id": sub.id,
            "event_type": "service.called",
            "payload": json.dumps({}),
            "status": "pending",
            "attempts": 1,   # one attempt already done; max_retries=3, so 2 left
            "max_retries": MAX_RETRIES,
            "next_retry_at": past,
            "last_error": "HTTP 500",
            "created_at": now_iso,
            "updated_at": now_iso,
        })

        mock_fail = MagicMock()
        mock_fail.status_code = 500

        with patch("marketplace.webhooks.httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_fail)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            # First retry_pending — fails, returns immediately with pending + backoff
            results = await mgr.retry_pending()

        assert len(results) == 1
        assert results[0].success is False
        status_record = mgr.get_delivery_status(delivery_id)
        assert status_record["status"] == "pending"
        assert status_record["attempts"] == 2
        # next_retry_at should be in the future (backoff applied)
        next_retry = datetime.fromisoformat(status_record["next_retry_at"])
        assert next_retry > datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 6. get_delivery_status
# ---------------------------------------------------------------------------


class TestGetDeliveryStatus:
    @pytest.mark.asyncio
    async def test_get_delivery_status_pending(self, mgr, db):
        """get_delivery_status returns a pending record correctly."""
        sub = _subscribe(mgr, events=["service.called"])
        now_iso = datetime.now(timezone.utc).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        delivery_id = str(uuid.uuid4())
        db.insert_delivery_log({
            "id": delivery_id,
            "subscription_id": sub.id,
            "event_type": "service.called",
            "payload": json.dumps({}),
            "status": "pending",
            "attempts": 0,
            "max_retries": MAX_RETRIES,
            "next_retry_at": future,
            "last_error": None,
            "created_at": now_iso,
            "updated_at": now_iso,
        })

        result = mgr.get_delivery_status(delivery_id)
        assert result is not None
        assert result["id"] == delivery_id
        assert result["status"] == "pending"
        assert result["event_type"] == "service.called"

    def test_get_delivery_status_none_for_missing(self, mgr):
        """Returns None when delivery_id does not exist."""
        assert mgr.get_delivery_status("does-not-exist") is None
