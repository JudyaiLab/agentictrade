"""Tests for ServiceReviewEngine — automated security review pipeline."""
from __future__ import annotations

import uuid
import pytest
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from marketplace.db import Database
from marketplace.service_review import ServiceReviewEngine


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(Path(tmp) / "test.db")


@pytest.fixture
def engine(db):
    return ServiceReviewEngine(db)


def _insert_service(db, service_id=None, endpoint="https://api.example.com/v1"):
    """Helper: insert a test service."""
    sid = service_id or str(uuid.uuid4())
    provider_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.insert_service({
        "id": sid,
        "provider_id": provider_id,
        "name": "Test Service",
        "description": "Test",
        "endpoint": endpoint,
        "price_per_call": 0.01,
        "currency": "USDC",
        "payment_method": "x402",
        "free_tier_calls": 0,
        "bulk_discount": "{}",
        "status": "reviewing",
        "category": "test",
        "tags": "test",
        "created_at": now,
        "updated_at": now,
        "metadata": "{}",
    })
    return sid, provider_id


# --- Create Review ---

class TestCreateReview:
    @pytest.mark.asyncio
    async def test_create_review_basic(self, engine, db):
        sid, pid = _insert_service(db)
        review = await engine.create_review(sid, pid)
        assert review["service_id"] == sid
        assert review["provider_id"] == pid
        assert review["status"] == "pending"
        assert review["review_type"] == "automated"

    @pytest.mark.asyncio
    async def test_create_review_stored_in_db(self, engine, db):
        sid, pid = _insert_service(db)
        review = await engine.create_review(sid, pid)
        stored = db.get_service_review(review["id"])
        assert stored is not None
        assert stored["status"] == "pending"


# --- Execute Review ---

class TestExecuteReview:
    @pytest.mark.asyncio
    async def test_review_not_found(self, engine):
        with pytest.raises(ValueError, match="not found"):
            await engine.execute_review("nonexistent-id")

    @pytest.mark.asyncio
    async def test_review_service_not_found(self, engine, db):
        # Insert a review for a service that doesn't exist
        review_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.insert_service_review({
            "id": review_id,
            "service_id": "nonexistent-service",
            "provider_id": "some-provider",
            "review_type": "automated",
            "status": "pending",
            "endpoint_reachable": 0,
            "response_format_valid": 0,
            "response_time_ms": 0,
            "malicious_check_passed": 0,
            "error_details": "",
            "reviewer_notes": "",
            "reviewed_at": None,
            "created_at": now,
        })
        result = await engine.execute_review(review_id)
        assert result["status"] == "failed"
        assert "not found" in result["error_details"].lower()

    @pytest.mark.asyncio
    async def test_review_empty_endpoint(self, engine, db):
        sid, pid = _insert_service(db, endpoint="")
        review = await engine.create_review(sid, pid)
        result = await engine.execute_review(review["id"])
        assert result["status"] == "failed"
        assert "empty" in result["error_details"].lower()

    @pytest.mark.asyncio
    async def test_review_invalid_scheme(self, engine, db):
        sid, pid = _insert_service(db, endpoint="ftp://evil.com/data")
        review = await engine.create_review(sid, pid)
        result = await engine.execute_review(review["id"])
        assert result["status"] == "failed"
        assert "scheme" in result["error_details"].lower()

    @pytest.mark.asyncio
    async def test_review_all_pass(self, engine, db):
        sid, pid = _insert_service(db)
        review = await engine.create_review(sid, pid)

        # Mock httpx to simulate a good JSON response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_redirect = False
        mock_response.headers = {"content-type": "application/json"}
        mock_response.url = "https://api.example.com/v1"
        mock_response.content = b'{"status": "ok"}'

        async def mock_get(*args, **kwargs):
            return mock_response

        with patch("marketplace.service_review.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.get = mock_get
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = instance

            result = await engine.execute_review(review["id"])

        assert result["status"] == "passed"
        assert result["endpoint_reachable"] == 1
        assert result["response_format_valid"] == 1
        assert result["malicious_check_passed"] == 1

        # Verify service promoted to active
        svc = db.get_service(sid)
        assert svc["status"] == "active"

    @pytest.mark.asyncio
    async def test_review_fail_non_json(self, engine, db):
        sid, pid = _insert_service(db)
        review = await engine.create_review(sid, pid)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_redirect = False
        mock_response.headers = {"content-type": "text/html"}
        mock_response.url = "https://api.example.com/v1"
        mock_response.content = b'<html></html>'

        async def mock_get(*args, **kwargs):
            return mock_response

        with patch("marketplace.service_review.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.get = mock_get
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = instance

            result = await engine.execute_review(review["id"])

        assert result["status"] == "failed"
        assert "response_not_json" in result["error_details"]


# --- Should Skip Review ---

class TestShouldSkipReview:
    def test_no_provider(self, engine):
        assert engine.should_skip_review("nonexistent") is False

    def test_fast_track_eligible(self, engine, db):
        # Insert a provider with fast_track=True
        pid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.insert_agent_provider({
            "id": pid,
            "agent_id": str(uuid.uuid4()),
            "owner_email": "test@example.com",
            "wallet_address": "0x" + "a" * 40,
            "did": "did:web:example.com",
            "declaration": "",
            "status": "active",
            "reputation_score": 90.0,
            "fast_track_eligible": 1,
            "daily_tx_cap": 500.0,
            "daily_tx_used": 0.0,
            "daily_tx_reset_at": now,
            "probation_ends_at": now,
            "total_reports": 0,
            "created_at": now,
            "updated_at": now,
            "metadata": "{}",
        })
        assert engine.should_skip_review(pid) is True

    def test_not_fast_track(self, engine, db):
        pid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.insert_agent_provider({
            "id": pid,
            "agent_id": str(uuid.uuid4()),
            "owner_email": "test@example.com",
            "wallet_address": "0x" + "a" * 40,
            "did": "did:web:example.com",
            "declaration": "",
            "status": "active",
            "reputation_score": 50.0,
            "fast_track_eligible": 0,
            "daily_tx_cap": 500.0,
            "daily_tx_used": 0.0,
            "daily_tx_reset_at": now,
            "probation_ends_at": now,
            "total_reports": 0,
            "created_at": now,
            "updated_at": now,
            "metadata": "{}",
        })
        assert engine.should_skip_review(pid) is False


# --- Review Pending Services ---

class TestReviewPendingServices:
    @pytest.mark.asyncio
    async def test_no_pending(self, engine):
        results = await engine.review_pending_services()
        assert results == []

    @pytest.mark.asyncio
    async def test_batch_review(self, engine, db):
        sid, pid = _insert_service(db)
        await engine.create_review(sid, pid)

        # Mock to fail fast (connection error)
        with patch("marketplace.service_review.httpx.AsyncClient") as mock_client:
            import httpx
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = instance

            results = await engine.review_pending_services()

        assert len(results) == 1
        assert results[0]["status"] == "failed"


# --- Response Format Check ---

class TestResponseFormatCheck:
    def test_json_content_type(self):
        resp = MagicMock()
        resp.headers = {"content-type": "application/json; charset=utf-8"}
        assert ServiceReviewEngine._check_response_format(resp) is True

    def test_non_json_content_type(self):
        resp = MagicMock()
        resp.headers = {"content-type": "text/html"}
        assert ServiceReviewEngine._check_response_format(resp) is False

    def test_missing_content_type(self):
        resp = MagicMock()
        resp.headers = {}
        assert ServiceReviewEngine._check_response_format(resp) is False


# --- Malicious Check ---

class TestMaliciousCheck:
    def test_clean_response(self, engine):
        resp = MagicMock()
        resp.url = "https://api.example.com/v1"
        resp.headers = {"content-type": "application/json"}
        resp.content = b'{"ok": true}'
        passed, notes = engine._check_malicious("https://api.example.com/v1", resp)
        assert passed is True
        assert notes == ""

    def test_suspicious_domain(self, engine):
        resp = MagicMock()
        resp.url = "https://bit.ly/something"
        resp.headers = {"content-type": "application/json"}
        resp.content = b'{"ok": true}'
        passed, notes = engine._check_malicious("https://api.example.com", resp)
        assert passed is False
        assert "suspicious_final_domain" in notes

    def test_attachment_disposition(self, engine):
        resp = MagicMock()
        resp.url = "https://api.example.com/v1"
        resp.headers = {
            "content-type": "application/json",
            "content-disposition": "attachment; filename=malware.exe",
        }
        resp.content = b'{"ok": true}'
        passed, notes = engine._check_malicious("https://api.example.com/v1", resp)
        assert passed is False
        assert "dangerous_disposition" in notes

    def test_oversized_body(self, engine):
        resp = MagicMock()
        resp.url = "https://api.example.com/v1"
        resp.headers = {
            "content-type": "application/json",
            "content-length": str(20 * 1024 * 1024),
        }
        resp.content = b'x'  # actual content small, but header says big
        passed, notes = engine._check_malicious("https://api.example.com/v1", resp)
        assert passed is False
        assert "body_too_large" in notes

    def test_suspicious_header(self, engine):
        resp = MagicMock()
        resp.url = "https://api.example.com/v1"
        resp.headers = {
            "content-type": "application/json",
            "x-coinhive": "active",
        }
        resp.content = b'{"ok": true}'
        passed, notes = engine._check_malicious("https://api.example.com/v1", resp)
        assert passed is False
        assert "suspicious_header" in notes
