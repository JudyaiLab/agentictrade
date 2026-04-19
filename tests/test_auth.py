"""Tests for API Key authentication module."""
from __future__ import annotations

import pytest
import tempfile
from pathlib import Path

from marketplace.db import Database
from marketplace.auth import (
    APIKeyManager,
    AuthError,
    generate_api_key,
    hash_secret,
    verify_secret,
)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


# --- Key generation ---

class TestKeyGeneration:
    def test_generate_api_key_format(self):
        key_id, secret = generate_api_key()
        assert key_id.startswith("acf_")
        assert len(secret) > 20

    def test_generate_api_key_custom_prefix(self):
        key_id, _ = generate_api_key(prefix="test")
        assert key_id.startswith("test_")

    def test_generate_unique_keys(self):
        keys = [generate_api_key() for _ in range(10)]
        key_ids = [k[0] for k in keys]
        assert len(set(key_ids)) == 10  # All unique

    def test_hash_secret_scrypt_format(self):
        h = hash_secret("my_secret")
        assert h.startswith("scrypt$")
        parts = h.split("$")
        assert len(parts) == 3  # scrypt, salt_hex, hash_hex

    def test_hash_secret_unique_salts(self):
        h1 = hash_secret("my_secret")
        h2 = hash_secret("my_secret")
        # Different random salts produce different hashes
        assert h1 != h2

    def test_verify_secret_correct(self):
        stored = hash_secret("my_secret")
        assert verify_secret("my_secret", stored)

    def test_verify_secret_wrong(self):
        stored = hash_secret("my_secret")
        assert not verify_secret("wrong_secret", stored)

    def test_verify_secret_different_inputs(self):
        stored_a = hash_secret("secret_a")
        stored_b = hash_secret("secret_b")
        assert not verify_secret("secret_a", stored_b)
        assert not verify_secret("secret_b", stored_a)

    def test_verify_legacy_sha256(self):
        """Backward compat: old SHA-256 hashes still verify."""
        import hashlib as _hl
        legacy = _hl.sha256("old_secret".encode()).hexdigest()
        assert verify_secret("old_secret", legacy)
        assert not verify_secret("wrong", legacy)


# --- Key creation ---

class TestCreateKey:
    def test_create_buyer_key(self, auth):
        key_id, secret = auth.create_key(owner_id="buyer-1")
        assert key_id.startswith("acf_")
        assert len(secret) > 20

    def test_create_provider_key(self, auth):
        key_id, _ = auth.create_key(owner_id="prov-1", role="provider")
        record = auth.db.get_api_key(key_id)
        assert record["role"] == "provider"

    def test_create_admin_key(self, auth):
        key_id, _ = auth.create_key(owner_id="admin-1", role="admin")
        record = auth.db.get_api_key(key_id)
        assert record["role"] == "admin"

    def test_create_key_invalid_role(self, auth):
        with pytest.raises(AuthError, match="Invalid role"):
            auth.create_key(owner_id="user-1", role="superuser")

    def test_create_key_empty_owner(self, auth):
        with pytest.raises(AuthError, match="owner_id"):
            auth.create_key(owner_id="")

    def test_create_key_with_wallet(self, auth):
        key_id, _ = auth.create_key(
            owner_id="user-1",
            wallet_address="0xABC123",
        )
        record = auth.db.get_api_key(key_id)
        assert record["wallet_address"] == "0xABC123"

    def test_create_key_custom_rate_limit(self, auth):
        key_id, _ = auth.create_key(owner_id="user-1", rate_limit=100)
        record = auth.db.get_api_key(key_id)
        assert record["rate_limit"] == 100


# --- Key validation ---

class TestValidateKey:
    def test_validate_correct_key(self, auth):
        key_id, secret = auth.create_key(owner_id="user-1")
        record = auth.validate(key_id, secret)
        assert record["owner_id"] == "user-1"
        assert record["role"] == "buyer"

    def test_validate_wrong_secret(self, auth):
        key_id, _ = auth.create_key(owner_id="user-1")
        with pytest.raises(AuthError, match="Invalid API key"):
            auth.validate(key_id, "wrong_secret")

    def test_validate_nonexistent_key(self, auth):
        with pytest.raises(AuthError, match="Invalid API key"):
            auth.validate("acf_nonexistent", "any_secret")

    def test_validate_returns_all_fields(self, auth):
        key_id, secret = auth.create_key(
            owner_id="user-1",
            role="provider",
            rate_limit=120,
        )
        record = auth.validate(key_id, secret)
        assert record["key_id"] == key_id
        assert record["role"] == "provider"
        assert record["rate_limit"] == 120


# --- Rate limiting ---

class TestRateLimit:
    def test_within_rate_limit(self, auth):
        assert auth.check_rate_limit("key-1", limit=5)
        assert auth.check_rate_limit("key-1", limit=5)
        assert auth.check_rate_limit("key-1", limit=5)

    def test_exceeds_rate_limit(self, auth):
        for _ in range(3):
            auth.check_rate_limit("key-2", limit=3)
        assert auth.check_rate_limit("key-2", limit=3) is not True

    def test_separate_keys_separate_limits(self, auth):
        for _ in range(3):
            auth.check_rate_limit("key-a", limit=3)
        # key-a is exhausted
        assert auth.check_rate_limit("key-a", limit=3) is not True
        # key-b is fresh
        assert auth.check_rate_limit("key-b", limit=3)


# ---------------------------------------------------------------------------
# API-level key creation (privilege escalation prevention)
# ---------------------------------------------------------------------------

class TestKeyCreationAPI:
    """Test that buyer keys cannot escalate to provider/admin roles via API."""

    @pytest.fixture
    def client(self, db):
        from fastapi.testclient import TestClient
        from api.main import app
        from marketplace.audit import AuditLogger
        with TestClient(app, raise_server_exceptions=False) as c:
            app.state.db = db
            app.state.auth = APIKeyManager(db)
            app.state.audit = AuditLogger(str(db.db_path))
            yield c

    def test_buyer_cannot_create_provider_key(self, client, db):
        auth = APIKeyManager(db)
        buyer_id, buyer_secret = auth.create_key(owner_id="buyer-1", role="buyer")
        resp = client.post("/api/v1/keys", json={
            "owner_id": "escalated",
            "role": "provider",
        }, headers={"Authorization": f"Bearer {buyer_id}:{buyer_secret}"})
        assert resp.status_code == 403

    def test_buyer_cannot_create_admin_key(self, client, db):
        auth = APIKeyManager(db)
        buyer_id, buyer_secret = auth.create_key(owner_id="buyer-2", role="buyer")
        resp = client.post("/api/v1/keys", json={
            "owner_id": "escalated",
            "role": "admin",
        }, headers={"Authorization": f"Bearer {buyer_id}:{buyer_secret}"})
        assert resp.status_code == 403

    def test_provider_can_create_provider_key(self, client, db):
        auth = APIKeyManager(db)
        prov_id, prov_secret = auth.create_key(owner_id="prov-1", role="provider")
        resp = client.post("/api/v1/keys", json={
            "owner_id": "new-prov",
            "role": "provider",
        }, headers={"Authorization": f"Bearer {prov_id}:{prov_secret}"})
        assert resp.status_code == 201

    def test_admin_can_create_any_role(self, client, db):
        auth = APIKeyManager(db)
        admin_id, admin_secret = auth.create_key(owner_id="admin-1", role="admin")
        for role in ("buyer", "provider", "admin"):
            resp = client.post("/api/v1/keys", json={
                "owner_id": f"created-{role}",
                "role": role,
            }, headers={"Authorization": f"Bearer {admin_id}:{admin_secret}"})
            assert resp.status_code == 201, f"Admin should create {role} key"


class TestBruteForceProtection:
    """Brute-force protection: 5 failed attempts per IP within 60s → 429."""

    @pytest.fixture(autouse=True)
    def reset_failures(self):
        """Reset the global failure tracker between tests."""
        from api.routes.auth import _auth_failures
        _auth_failures.clear()
        yield
        _auth_failures.clear()

    @pytest.fixture
    def client(self, db):
        from fastapi.testclient import TestClient
        from api.main import app
        from marketplace.audit import AuditLogger
        with TestClient(app, raise_server_exceptions=False) as c:
            app.state.db = db
            app.state.auth = APIKeyManager(db)
            app.state.audit = AuditLogger(str(db.db_path))
            yield c

    def test_first_failure_returns_401(self, client):
        resp = client.post("/api/v1/keys/validate", json={
            "key_id": "bad", "secret": "wrong",
        })
        assert resp.status_code == 401

    def test_five_failures_triggers_lockout(self, client):
        for _ in range(5):
            client.post("/api/v1/keys/validate", json={
                "key_id": "bad", "secret": "wrong",
            })
        # 6th attempt should be 429
        resp = client.post("/api/v1/keys/validate", json={
            "key_id": "bad", "secret": "wrong",
        })
        assert resp.status_code == 429
        assert "Too many" in resp.json()["error"]

    def test_valid_key_still_works_before_lockout(self, client, db):
        auth = APIKeyManager(db)
        key_id, secret = auth.create_key(owner_id="good-user", role="buyer")
        # Make 4 bad attempts (under threshold)
        for _ in range(4):
            client.post("/api/v1/keys/validate", json={
                "key_id": "bad", "secret": "wrong",
            })
        # Valid attempt should still work
        resp = client.post("/api/v1/keys/validate", json={
            "key_id": key_id, "secret": secret,
        })
        assert resp.status_code == 200
        assert resp.json()["valid"] is True
