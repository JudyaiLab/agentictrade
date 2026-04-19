"""
API Key authentication for Agent Commerce Framework.
Handles key generation, hashing, validation, and rate limiting.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from .db import Database

# scrypt parameters (OWASP recommended minimums)
_SCRYPT_N = 2**14  # CPU/memory cost
_SCRYPT_R = 8      # block size
_SCRYPT_P = 1      # parallelism
_SCRYPT_DKLEN = 32 # derived key length
_SCRYPT_SALT_LEN = 16  # salt bytes

# Default key lifetime
DEFAULT_KEY_TTL_DAYS = 365


class AuthError(Exception):
    """Authentication/authorization errors."""


def generate_api_key(prefix: str = "acf") -> tuple[str, str]:
    """
    Generate an API key pair.

    Returns (key_id, raw_secret).
    The raw_secret is shown once; we store only the hash.
    """
    key_id = f"{prefix}_{uuid.uuid4().hex[:16]}"
    raw_secret = secrets.token_urlsafe(32)
    return key_id, raw_secret


def hash_secret(raw_secret: str, *, salt: bytes | None = None) -> str:
    """
    Hash an API secret using scrypt (RFC 7914).

    Returns 'scrypt$<salt_hex>$<hash_hex>'.
    A random salt is generated when *salt* is None (key creation).
    Pass the original salt when verifying.
    """
    if salt is None:
        salt = os.urandom(_SCRYPT_SALT_LEN)
    derived = hashlib.scrypt(
        raw_secret.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${salt.hex()}${derived.hex()}"


def _is_legacy_hash(stored: str) -> bool:
    """Return True if the stored hash is a legacy SHA-256 hex digest."""
    return not stored.startswith("scrypt$")


def verify_secret(raw_secret: str, stored_hash: str) -> bool:
    """
    Verify *raw_secret* against *stored_hash*.

    Supports both legacy SHA-256 hashes and scrypt hashes for
    backward compatibility during migration.
    """
    if _is_legacy_hash(stored_hash):
        candidate = hashlib.sha256(raw_secret.encode()).hexdigest()
        return secrets.compare_digest(candidate, stored_hash)

    # scrypt$<salt_hex>$<hash_hex>
    parts = stored_hash.split("$")
    if len(parts) != 3:
        return False
    salt = bytes.fromhex(parts[1])
    candidate = hash_secret(raw_secret, salt=salt)
    return secrets.compare_digest(candidate, stored_hash)


class APIKeyManager:
    """Manages API key lifecycle: create, validate, revoke."""

    def __init__(self, db: Database):
        self.db = db
        # DB-backed per-key rate limiter — works across multiple workers.
        # Each key gets its own window via a prefixed key in the shared
        # rate_limit_windows table.
        from .rate_limit import DatabaseRateLimiter
        self._per_key_rl = DatabaseRateLimiter(db, rate=60, window_seconds=60.0)

    def create_key(
        self,
        owner_id: str,
        role: str = "buyer",
        rate_limit: int = 60,
        wallet_address: Optional[str] = None,
        ttl_days: int | None = DEFAULT_KEY_TTL_DAYS,
    ) -> tuple[str, str]:
        """
        Create a new API key.

        *ttl_days* controls lifetime (default 365). Pass ``None`` for
        a non-expiring key.

        Returns (key_id, raw_secret). raw_secret is shown once only.
        """
        if role not in ("admin", "provider", "buyer"):
            raise AuthError(f"Invalid role: {role}")
        if not owner_id:
            raise AuthError("owner_id is required")

        key_id, raw_secret = generate_api_key()
        hashed = hash_secret(raw_secret)
        now = datetime.now(timezone.utc)

        expires_at = None
        if ttl_days is not None:
            expires_at = (now + timedelta(days=ttl_days)).isoformat()

        self.db.insert_api_key({
            "key_id": key_id,
            "hashed_secret": hashed,
            "owner_id": owner_id,
            "role": role,
            "rate_limit": rate_limit,
            "wallet_address": wallet_address,
            "created_at": now.isoformat(),
            "expires_at": expires_at,
        })

        return key_id, raw_secret

    def validate(self, key_id: str, raw_secret: str) -> dict:
        """
        Validate an API key. Returns key record if valid.

        Raises AuthError on invalid key, wrong secret, or expired key.
        """
        key_record = self.db.get_api_key(key_id)
        if not key_record:
            raise AuthError("Invalid API key")

        if not verify_secret(raw_secret, key_record["hashed_secret"]):
            raise AuthError("Invalid API key")

        # Check expiry
        if key_record.get("expires_at"):
            expires = datetime.fromisoformat(key_record["expires_at"])
            if datetime.now(timezone.utc) > expires:
                raise AuthError("API key expired")

        return key_record

    def validate_key_id(self, key_id: str) -> dict | None:
        """Look up a key by ID only (no secret check). For session cookie validation."""
        record = self.db.get_api_key(key_id)
        if not record:
            return None
        if record.get("expires_at"):
            expires = datetime.fromisoformat(record["expires_at"])
            if datetime.now(timezone.utc) > expires:
                return None
        return record

    def check_rate_limit(self, key_id: str, limit: int) -> bool | int:
        """
        Check if key is within rate limit (calls per minute).

        Uses a DB-backed sliding window so limits are consistent across
        multiple workers (horizontal scaling).

        Returns True if allowed, or the number of seconds until the
        window resets (>= 1) when rate limited.
        """
        # Use rate_override to avoid mutating shared state (thread-safe).
        rl_key = f"apikey:{key_id}"
        allowed = self._per_key_rl.allow(rl_key, rate_override=limit)

        if allowed:
            return True
        # Rate limited — return seconds until window resets
        return max(1, int(self._per_key_rl.window_seconds))
