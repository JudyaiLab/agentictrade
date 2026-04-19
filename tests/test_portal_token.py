"""Tests for Provider Portal API Token (PAT) generate/revoke."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient

from marketplace.db import Database
from marketplace.auth import APIKeyManager, generate_api_key, hash_secret
from marketplace.provider_auth import (
    ensure_provider_accounts_table,
    create_account,
    sign_session,
    _SESSION_COOKIE,
)
from api.main import app


@pytest.fixture
def db(tmp_path):
    _db = Database(tmp_path / "pat_test.db")
    ensure_provider_accounts_table(_db)
    return _db


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


@pytest.fixture
def provider_account(db):
    """Create a provider account and link an API key."""
    with patch("marketplace.provider_auth.check_password_breach", return_value=False):
        account = create_account(db, "provider@test.com", "Password123", "TestProvider")
    # Link an API key (mimics registration flow)
    key_id, raw_secret = generate_api_key(prefix="acf")
    hashed = hash_secret(raw_secret)
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO api_keys (key_id, hashed_secret, owner_id, role, created_at)
               VALUES (?, ?, ?, 'provider', ?)""",
            (key_id, hashed, account["id"], now),
        )
        conn.execute(
            "UPDATE provider_accounts SET api_key_id = ? WHERE id = ?",
            (key_id, account["id"]),
        )
    return {**account, "api_key_id": key_id, "acf_secret": raw_secret}


@pytest.fixture
def client(db, auth):
    with TestClient(app, raise_server_exceptions=False) as c:
        app.state.db = db
        app.state.auth = auth
        yield c


def _session_cookie(provider_id: str) -> dict:
    """Create session cookie dict for a provider."""
    token = sign_session(provider_id)
    return {_SESSION_COOKIE: token}


def _get_csrf(client, cookies: dict) -> str:
    """Extract CSRF token from settings page."""
    resp = client.get("/portal/settings", cookies=cookies)
    assert resp.status_code == 200
    html = resp.text
    # Find csrf_token value in hidden input
    idx = html.find('name="csrf_token" value="')
    assert idx != -1, "CSRF token not found in settings page"
    start = idx + len('name="csrf_token" value="')
    end = html.find('"', start)
    return html[start:end]


class TestGenerateToken:
    def test_generate_token_success(self, client, provider_account):
        cookies = _session_cookie(provider_account["id"])
        csrf = _get_csrf(client, cookies)
        resp = client.post(
            "/portal/api-token",
            data={"csrf_token": csrf},
            cookies=cookies,
            follow_redirects=False,
        )
        assert resp.status_code == 200
        html = resp.text
        assert "pat_" in html
        assert "won't be shown again" in html.lower() or "won&#x27;t be shown again" in html.lower()

    def test_generate_token_duplicate(self, client, provider_account, db):
        cookies = _session_cookie(provider_account["id"])
        csrf = _get_csrf(client, cookies)
        # First generation
        resp1 = client.post(
            "/portal/api-token",
            data={"csrf_token": csrf},
            cookies=cookies,
        )
        assert resp1.status_code == 200
        assert "pat_" in resp1.text

        # Second attempt — should fail
        csrf2 = _get_csrf(client, cookies)
        resp2 = client.post(
            "/portal/api-token",
            data={"csrf_token": csrf2},
            cookies=cookies,
        )
        assert resp2.status_code == 400
        assert "already have" in resp2.text.lower()

    def test_generate_token_requires_auth(self, client):
        resp = client.post("/portal/api-token", data={"csrf_token": "x"})
        # Should redirect to login
        assert resp.status_code == 303 or resp.status_code == 200

    def test_generate_token_csrf_required(self, client, provider_account):
        cookies = _session_cookie(provider_account["id"])
        resp = client.post(
            "/portal/api-token",
            data={"csrf_token": "invalid"},
            cookies=cookies,
        )
        assert resp.status_code == 403


class TestRevokeToken:
    def test_revoke_token(self, client, provider_account, db):
        cookies = _session_cookie(provider_account["id"])
        csrf = _get_csrf(client, cookies)
        # Generate first
        client.post("/portal/api-token", data={"csrf_token": csrf}, cookies=cookies)

        # Revoke
        csrf2 = _get_csrf(client, cookies)
        resp = client.post(
            "/portal/revoke-api-token",
            data={"csrf_token": csrf2},
            cookies=cookies,
        )
        assert resp.status_code == 200
        assert "revoked" in resp.text.lower()

        # Verify token is gone from DB
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE owner_id = ? AND key_id LIKE 'pat_%'",
                (provider_account["id"],),
            ).fetchone()
        assert row is None

    def test_revoke_token_csrf_required(self, client, provider_account):
        cookies = _session_cookie(provider_account["id"])
        resp = client.post(
            "/portal/revoke-api-token",
            data={"csrf_token": "bad"},
            cookies=cookies,
        )
        assert resp.status_code == 403


class TestTokenUsage:
    def _generate_pat(self, client, provider_account):
        """Helper: generate PAT and return the raw token string."""
        cookies = _session_cookie(provider_account["id"])
        csrf = _get_csrf(client, cookies)
        resp = client.post(
            "/portal/api-token",
            data={"csrf_token": csrf},
            cookies=cookies,
        )
        html = resp.text
        # Extract token from <code>pat_xxx:secret</code>
        import re
        match = re.search(r"(pat_[a-f0-9]+:[A-Za-z0-9_\-]+)", html)
        assert match, f"Could not find PAT token in response"
        return match.group(1)

    def test_use_token_with_provider_api(self, client, provider_account, db):
        """PAT token should work with /api/v1/provider/dashboard."""
        # Insert a service so dashboard has data
        now = datetime.now(timezone.utc).isoformat()
        db.insert_service({
            "id": "svc-pat-test",
            "provider_id": provider_account["id"],
            "name": "PAT Test Service",
            "endpoint": "https://example.com/api",
            "price_per_call": 1.0,
            "currency": "USDC",
            "payment_method": "x402",
            "free_tier_calls": 0,
            "status": "active",
            "category": "crypto",
            "tags": ["test"],
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        })

        token = self._generate_pat(client, provider_account)
        resp = client.get(
            "/api/v1/provider/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "provider_id" in data or "total_services" in data

    def test_revoked_token_rejected(self, client, provider_account, db):
        """After revoking, the token should be rejected."""
        token = self._generate_pat(client, provider_account)

        # Revoke
        cookies = _session_cookie(provider_account["id"])
        csrf = _get_csrf(client, cookies)
        client.post("/portal/revoke-api-token", data={"csrf_token": csrf}, cookies=cookies)

        # Try to use revoked token
        resp = client.get(
            "/api/v1/provider/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401 or resp.status_code == 403


class TestSettingsPagePATDisplay:
    def test_settings_shows_generate_button(self, client, provider_account):
        cookies = _session_cookie(provider_account["id"])
        resp = client.get("/portal/settings", cookies=cookies)
        assert resp.status_code == 200
        assert "Generate Token" in resp.text

    def test_settings_shows_revoke_after_generate(self, client, provider_account):
        cookies = _session_cookie(provider_account["id"])
        csrf = _get_csrf(client, cookies)
        client.post("/portal/api-token", data={"csrf_token": csrf}, cookies=cookies)

        resp = client.get("/portal/settings", cookies=cookies)
        assert resp.status_code == 200
        assert "Revoke Token" in resp.text
        assert "pat_" in resp.text


class TestPortalBruteForce:
    """Test portal login brute-force protection."""

    def test_blocks_after_5_failures(self, client, db, auth):
        """Portal login should block after 5 failed attempts (DB-backed)."""
        import api.routes.portal as portal_mod
        # Reset the DB-backed limiter so previous tests don't interfere
        portal_mod._portal_limiter = None

        ensure_provider_accounts_table(db)

        for i in range(5):
            resp = client.post("/portal/login", data={
                "email": "wrong@test.com",
                "password": "badpass",
                "csrf_token": "",
            })
            # Each attempt consumes one slot in the rate limiter

        # 6th attempt should be blocked
        resp = client.post("/portal/login", data={
            "email": "wrong@test.com",
            "password": "badpass",
            "csrf_token": "",
        })
        assert resp.status_code == 429
        assert "Too many failed attempts" in resp.text

        # Clean up: reset limiter for test isolation
        portal_mod._portal_limiter = None
