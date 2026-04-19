"""
Tests for email routes — download gate, subscriber management, unsubscribe.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from marketplace.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "email_test.db")


@pytest.fixture
def client(db):
    with TestClient(app, raise_server_exceptions=False) as c:
        app.state.db = db
        yield c


# ---------------------------------------------------------------------------
# POST /api/v1/download-gate
# ---------------------------------------------------------------------------

class TestDownloadGate:
    def test_new_subscriber_gets_download_link(self, client):
        resp = client.post("/api/v1/download-gate", json={
            "email": "test@example.com",
            "consent": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "/portal/getting-started" in data["download_url"]
        assert data["already_subscribed"] is False
        assert "email" in data["message"].lower() or "download" in data["message"].lower()

    def test_duplicate_subscriber_returns_already_subscribed(self, client):
        client.post("/api/v1/download-gate", json={"email": "dup@example.com", "consent": True})
        resp = client.post("/api/v1/download-gate", json={"email": "dup@example.com", "consent": True})
        assert resp.status_code == 200
        assert resp.json()["already_subscribed"] is True

    def test_email_case_insensitive(self, client):
        client.post("/api/v1/download-gate", json={"email": "Test@Example.COM", "consent": True})
        resp = client.post("/api/v1/download-gate", json={"email": "test@example.com", "consent": True})
        assert resp.status_code == 200
        assert resp.json()["already_subscribed"] is True

    def test_invalid_email_rejected(self, client):
        resp = client.post("/api/v1/download-gate", json={"email": "not-an-email", "consent": True})
        assert resp.status_code == 422

    def test_empty_email_rejected(self, client):
        resp = client.post("/api/v1/download-gate", json={"email": "", "consent": True})
        assert resp.status_code == 422

    def test_consent_required(self, client):
        """Download gate requires explicit consent."""
        resp = client.post("/api/v1/download-gate", json={"email": "no-consent@example.com"})
        assert resp.status_code == 422

    def test_consent_false_rejected(self, client):
        """Download gate rejects consent=False."""
        resp = client.post("/api/v1/download-gate", json={
            "email": "false-consent@example.com",
            "consent": False,
        })
        assert resp.status_code == 422
        assert "consent" in resp.json()["error"].lower()

    def test_consent_metadata_stored(self, client, db):
        """Consent metadata (IP, timestamp) is stored with subscriber."""
        import json
        client.post("/api/v1/download-gate", json={
            "email": "consent_meta@example.com",
            "consent": True,
        })
        sub = db.get_subscriber("consent_meta@example.com")
        assert sub is not None
        metadata = json.loads(sub.get("metadata", "{}"))
        assert "consent_given_at" in metadata
        assert "consent_ip" in metadata

    def test_subscriber_stored_in_db(self, client, db):
        client.post("/api/v1/download-gate", json={"email": "stored@example.com", "consent": True})
        sub = db.get_subscriber("stored@example.com")
        assert sub is not None
        assert sub["email"] == "stored@example.com"
        assert sub["source"] == "website"
        assert sub["drip_stage"] == 0

    def test_custom_source(self, client, db):
        client.post("/api/v1/download-gate", json={
            "email": "custom@example.com",
            "source": "blog-cta",
            "consent": True,
        })
        sub = db.get_subscriber("custom@example.com")
        assert sub["source"] == "blog-cta"

    def test_subscriber_has_drip_scheduled(self, client, db):
        client.post("/api/v1/download-gate", json={"email": "drip@example.com", "consent": True})
        sub = db.get_subscriber("drip@example.com")
        assert sub["drip_next_at"] is not None


# ---------------------------------------------------------------------------
# GET /api/v1/unsubscribe
# ---------------------------------------------------------------------------

class TestUnsubscribe:
    def _unsub_token(self, email: str) -> str:
        """Generate an unsubscribe token using the production helper."""
        from api.routes.email import _unsub_token
        return _unsub_token(email)

    def test_unsubscribe_existing(self, client, db):
        client.post("/api/v1/download-gate", json={"email": "unsub@example.com", "consent": True})
        token = self._unsub_token("unsub@example.com")
        resp = client.get("/api/v1/unsubscribe", params={"email": "unsub@example.com", "token": token})
        assert resp.status_code == 200
        assert "unsubscribed" in resp.json()["message"].lower()

    def test_unsubscribe_unknown_email(self, client):
        token = self._unsub_token("unknown@example.com")
        resp = client.get("/api/v1/unsubscribe", params={"email": "unknown@example.com", "token": token})
        assert resp.status_code == 200
        assert "not found" in resp.json()["message"].lower()

    def test_unsubscribe_without_token_rejected(self, client, db):
        client.post("/api/v1/download-gate", json={"email": "notoken@example.com", "consent": True})
        resp = client.get("/api/v1/unsubscribe", params={"email": "notoken@example.com"})
        assert resp.status_code == 403

    def test_unsubscribe_invalid_token_rejected(self, client, db):
        client.post("/api/v1/download-gate", json={"email": "badtoken@example.com", "consent": True})
        resp = client.get("/api/v1/unsubscribe", params={"email": "badtoken@example.com", "token": "invalid"})
        assert resp.status_code == 403

    def test_unsubscribed_user_can_resubscribe(self, client, db):
        client.post("/api/v1/download-gate", json={"email": "resub@example.com", "consent": True})
        token = self._unsub_token("resub@example.com")
        client.get("/api/v1/unsubscribe", params={"email": "resub@example.com", "token": token})
        # After unsubscribe, download gate should still work (returns link)
        resp = client.post("/api/v1/download-gate", json={"email": "resub@example.com", "consent": True})
        assert resp.status_code == 200
        assert "/portal/getting-started" in resp.json()["download_url"]

    def test_token_is_non_deterministic(self):
        """Two calls to _unsub_token for the same email should produce different tokens."""
        from api.routes.email import _unsub_token
        t1 = _unsub_token("same@example.com")
        t2 = _unsub_token("same@example.com")
        assert t1 != t2  # Non-deterministic due to random nonce

    def test_token_contains_nonce(self):
        """Token format should be nonce:hmac."""
        from api.routes.email import _unsub_token
        token = _unsub_token("test@example.com")
        assert ":" in token
        nonce, sig = token.split(":", 1)
        assert len(nonce) == 32  # 16 bytes hex
        assert len(sig) == 32

    def test_both_tokens_verify(self):
        """Both non-deterministic tokens should verify correctly."""
        from api.routes.email import _unsub_token, _verify_unsub_token
        email = "verify@example.com"
        t1 = _unsub_token(email)
        t2 = _unsub_token(email)
        assert _verify_unsub_token(email, t1) is True
        assert _verify_unsub_token(email, t2) is True

    def test_cross_email_token_rejected(self):
        """Token for one email should not verify for another."""
        from api.routes.email import _unsub_token, _verify_unsub_token
        token = _unsub_token("alice@example.com")
        assert _verify_unsub_token("bob@example.com", token) is False


# ---------------------------------------------------------------------------
# GET /api/v1/admin/subscribers
# ---------------------------------------------------------------------------

class TestSubscriberStats:
    def test_stats_with_admin_key(self, client, db):
        client.post("/api/v1/download-gate", json={"email": "a@example.com", "consent": True})
        client.post("/api/v1/download-gate", json={"email": "b@example.com", "consent": True})
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "admin-key"}, clear=False):
            resp = client.get(
                "/api/v1/admin/subscribers",
                headers={"x-admin-key": "admin-key"},
            )
        assert resp.status_code == 200
        assert resp.json()["total_subscribers"] == 2

    def test_stats_wrong_key_rejected(self, client):
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "real"}, clear=False):
            resp = client.get(
                "/api/v1/admin/subscribers",
                headers={"x-admin-key": "wrong"},
            )
        assert resp.status_code == 401

    def test_stats_no_secret_configured(self, client):
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": ""}, clear=False):
            resp = client.get(
                "/api/v1/admin/subscribers",
                headers={"x-admin-key": "anything"},
            )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# DB subscriber methods
# ---------------------------------------------------------------------------

class TestSubscriberDB:
    def test_insert_and_get(self, db):
        sub = {
            "id": "sub-001",
            "email": "db@example.com",
            "source": "test",
            "subscribed_at": "2026-03-21T00:00:00Z",
            "confirmed": 0,
            "drip_stage": 0,
            "drip_next_at": "2026-03-23T00:00:00Z",
            "metadata": "{}",
        }
        assert db.insert_subscriber(sub) is True
        got = db.get_subscriber("db@example.com")
        assert got["id"] == "sub-001"

    def test_duplicate_email_returns_false(self, db):
        sub = {
            "id": "sub-002",
            "email": "dup@example.com",
            "source": "test",
            "subscribed_at": "2026-03-21T00:00:00Z",
            "confirmed": 0,
            "drip_stage": 0,
            "drip_next_at": None,
            "metadata": "{}",
        }
        assert db.insert_subscriber(sub) is True
        sub["id"] = "sub-003"
        assert db.insert_subscriber(sub) is False

    def test_advance_drip(self, db):
        sub = {
            "id": "sub-drip",
            "email": "drip@example.com",
            "source": "test",
            "subscribed_at": "2026-03-21T00:00:00Z",
            "confirmed": 0,
            "drip_stage": 0,
            "drip_next_at": "2026-03-23T00:00:00Z",
            "metadata": "{}",
        }
        db.insert_subscriber(sub)
        db.advance_drip("sub-drip", 1, "2026-03-26T00:00:00Z")
        got = db.get_subscriber("drip@example.com")
        assert got["drip_stage"] == 1
        assert got["drip_next_at"] == "2026-03-26T00:00:00Z"

    def test_unsubscribe(self, db):
        sub = {
            "id": "sub-unsub",
            "email": "bye@example.com",
            "source": "test",
            "subscribed_at": "2026-03-21T00:00:00Z",
            "confirmed": 0,
            "drip_stage": 0,
            "drip_next_at": None,
            "metadata": "{}",
        }
        db.insert_subscriber(sub)
        assert db.unsubscribe("bye@example.com") is True
        assert db.unsubscribe("nonexistent@example.com") is False

    def test_count_excludes_unsubscribed(self, db):
        for i in range(3):
            db.insert_subscriber({
                "id": f"sub-cnt-{i}",
                "email": f"cnt{i}@example.com",
                "source": "test",
                "subscribed_at": "2026-03-21T00:00:00Z",
                "confirmed": 0,
                "drip_stage": 0,
                "drip_next_at": None,
                "metadata": "{}",
            })
        db.unsubscribe("cnt1@example.com")
        assert db.count_subscribers() == 2

    def test_list_subscribers_for_drip(self, db):
        db.insert_subscriber({
            "id": "sub-drip-1",
            "email": "due@example.com",
            "source": "test",
            "subscribed_at": "2026-03-21T00:00:00Z",
            "confirmed": 0,
            "drip_stage": 0,
            "drip_next_at": "2026-03-20T00:00:00Z",
            "metadata": "{}",
        })
        db.insert_subscriber({
            "id": "sub-drip-2",
            "email": "notdue@example.com",
            "source": "test",
            "subscribed_at": "2026-03-21T00:00:00Z",
            "confirmed": 0,
            "drip_stage": 0,
            "drip_next_at": "2026-04-01T00:00:00Z",
            "metadata": "{}",
        })
        due = db.list_subscribers_for_drip("2026-03-21T00:00:00Z")
        assert len(due) == 1
        assert due[0]["email"] == "due@example.com"
