"""
Provider authentication — email + password registration and login.

Providers (humans who list APIs) get a web-based account with:
- Email + password registration (scrypt hashed)
- Email verification token
- Password reset token
- Session management via signed cookies
- Password strength validation + HIBP breach checking
- Personal Access Token (PAT) with configurable expiration

Buyers (AI agents) continue using API keys — no password needed.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from .db import Database

logger = logging.getLogger("acf.provider_auth")

# ---------------------------------------------------------------------------
# Password hashing (scrypt, same params as API key auth)
# ---------------------------------------------------------------------------

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_SALT_LEN = 16


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Hash password with scrypt. Returns 'scrypt$<salt_hex>$<hash_hex>'."""
    if salt is None:
        salt = os.urandom(_SCRYPT_SALT_LEN)
    derived = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${salt.hex()}${derived.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against stored hash."""
    try:
        parts = hashed.split("$")
        if len(parts) != 3 or parts[0] != "scrypt":
            return False
        salt = bytes.fromhex(parts[1])
        expected = hash_password(password, salt=salt)
        return hmac.compare_digest(expected, hashed)
    except (ValueError, IndexError):
        return False


# Pre-computed dummy hash for timing-oracle prevention.
# When an email is not found, we verify against this dummy so the
# response time is indistinguishable from a "wrong password" path.
_DUMMY_HASH = hash_password("timing_oracle_prevention_dummy")


# ---------------------------------------------------------------------------
# Password strength validation
# ---------------------------------------------------------------------------

_MIN_PASSWORD_LENGTH = 8
_PASSWORD_PATTERN = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$"
)


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Validate password meets strength requirements.

    Requirements:
    - At least 8 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit

    Returns (is_valid, error_message). error_message is empty when valid.
    """
    if len(password) < _MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {_MIN_PASSWORD_LENGTH} characters"
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r"\d", password):
        return False, "Password must contain at least one digit"
    return True, ""


def check_password_breach(password: str) -> bool:
    """Check if password appears in known breaches via HIBP k-Anonymity API.

    Uses the Have I Been Pwned Passwords API with k-anonymity:
    - SHA-1 hash the password
    - Send only the first 5 hex chars (prefix) to the API
    - Check if the suffix appears in the response

    Returns True if password is breached (unsafe), False if clean.
    Returns False on any network error (fail open — don't block registration).
    """
    try:
        import httpx
    except ImportError:
        logger.debug("httpx not available — skipping HIBP breach check")
        return False

    sha1 = hashlib.sha1(password.encode()).hexdigest().upper()
    prefix = sha1[:5]
    suffix = sha1[5:]

    try:
        resp = httpx.get(
            f"https://api.pwnedpasswords.com/range/{prefix}",
            timeout=5.0,
            headers={"Add-Padding": "true"},
        )
        if resp.status_code != 200:
            logger.warning("HIBP API returned %d — skipping breach check", resp.status_code)
            return False

        for line in resp.text.splitlines():
            parts = line.strip().split(":")
            if len(parts) == 2 and parts[0] == suffix:
                count = int(parts[1])
                if count > 0:
                    logger.info("Password found in %d breaches (HIBP)", count)
                    return True
        return False
    except Exception as exc:
        logger.warning("HIBP breach check failed: %s — skipping", exc)
        return False


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------

# Derive session key from the portal secret using HMAC prefix derivation
# so that session signing uses a purpose-specific key rather than the raw
# master secret.
_RAW_PORTAL_SECRET = os.environ.get("ACF_PORTAL_SECRET", "") or os.urandom(32).hex()
_SESSION_SECRET = hmac.new(
    _RAW_PORTAL_SECRET.encode(), b"portal_session", hashlib.sha256,
).hexdigest()
_SESSION_COOKIE = "acf_portal_session"
_SESSION_MAX_AGE = 3600 * 24  # 24 hours


def sign_session(provider_id: str) -> str:
    """Create signed session token: provider_id|timestamp|signature."""
    ts = str(int(time.time()))
    payload = f"{provider_id}|{ts}"
    sig = hmac.new(
        _SESSION_SECRET.encode(), payload.encode(), hashlib.sha256,
    ).hexdigest()[:32]
    return f"{payload}|{sig}"


def verify_session(token: str) -> Optional[str]:
    """Verify session token. Returns provider_id or None."""
    parts = token.split("|")
    if len(parts) != 3:
        return None
    provider_id, ts_str, sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return None
    if time.time() - ts > _SESSION_MAX_AGE:
        return None
    expected = hmac.new(
        _SESSION_SECRET.encode(), f"{provider_id}|{ts_str}".encode(), hashlib.sha256,
    ).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    return provider_id


# ---------------------------------------------------------------------------
# Verification & reset tokens
# ---------------------------------------------------------------------------

def generate_token() -> str:
    """Generate a URL-safe token for email verification or password reset."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash a verification/reset token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

PROVIDER_ACCOUNTS_SQL = """
CREATE TABLE IF NOT EXISTS provider_accounts (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    company_name TEXT DEFAULT '',
    verified INTEGER DEFAULT 0,
    verify_token_hash TEXT,
    reset_token_hash TEXT,
    reset_token_expires TEXT,
    api_key_id TEXT,
    status TEXT DEFAULT 'active',
    locale TEXT DEFAULT 'en',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_provider_email
    ON provider_accounts(email);
CREATE INDEX IF NOT EXISTS idx_provider_api_key
    ON provider_accounts(api_key_id);
"""


def ensure_provider_accounts_table(db: Database) -> None:
    """Create provider_accounts table if not exists."""
    with db.connect() as conn:
        conn.executescript(PROVIDER_ACCOUNTS_SQL)


# ---------------------------------------------------------------------------
# Account CRUD
# ---------------------------------------------------------------------------

class ProviderAccountError(Exception):
    """Provider account operation errors."""


def create_account(
    db: Database,
    email: str,
    password: str,
    display_name: str = "",
    locale: str = "en",
) -> dict:
    """
    Create a new provider account.

    Returns the account record (without password).
    Raises ProviderAccountError if email already exists.
    """
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ProviderAccountError("Invalid email address")

    # Password strength validation
    is_strong, strength_error = validate_password_strength(password)
    if not is_strong:
        raise ProviderAccountError(strength_error)

    # HIBP breach check (non-blocking — logs warning on failure)
    if check_password_breach(password):
        raise ProviderAccountError(
            "This password has appeared in a known data breach. "
            "Please choose a different password."
        )

    now = datetime.now(timezone.utc).isoformat()
    account_id = str(uuid.uuid4())
    hashed = hash_password(password)
    verify_token = generate_token()
    verify_hash = hash_token(verify_token)

    record = {
        "id": account_id,
        "email": email,
        "hashed_password": hashed,
        "display_name": display_name or email.split("@")[0],
        "verified": 0,
        "verify_token_hash": verify_hash,
        "status": "active",
        "locale": locale,
        "created_at": now,
        "updated_at": now,
    }

    try:
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO provider_accounts
                   (id, email, hashed_password, display_name, verified,
                    verify_token_hash, status, locale, created_at, updated_at)
                   VALUES (:id, :email, :hashed_password, :display_name, :verified,
                           :verify_token_hash, :status, :locale, :created_at, :updated_at)""",
                record,
            )
    except Exception as e:
        if "UNIQUE" in str(e):
            raise ProviderAccountError("Email already registered")
        raise

    return {
        "id": account_id,
        "email": email,
        "display_name": record["display_name"],
        "verified": False,
        "verify_token": verify_token,
        "locale": locale,
    }


def authenticate(db: Database, email: str, password: str) -> Optional[dict]:
    """
    Authenticate a provider by email + password.

    Returns account dict on success, None on failure.
    Updates last_login_at on success.
    """
    email = email.strip().lower()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM provider_accounts WHERE email = ? AND status = 'active'",
            (email,),
        ).fetchone()

    if row is None:
        # Prevent timing oracle: perform dummy scrypt so response time
        # is indistinguishable from "wrong password" path.
        verify_password(password, _DUMMY_HASH)
        return None

    if not verify_password(password, row["hashed_password"]):
        return None

    # Update last login
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        conn.execute(
            "UPDATE provider_accounts SET last_login_at = ? WHERE id = ?",
            (now, row["id"]),
        )

    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "company_name": row["company_name"] or "",
        "verified": bool(row["verified"]),
        "locale": row["locale"] or "en",
    }


def get_account_by_id(db: Database, account_id: str) -> Optional[dict]:
    """Get provider account by ID."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM provider_accounts WHERE id = ? AND status = 'active'",
            (account_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "company_name": row["company_name"] or "",
        "verified": bool(row["verified"]),
        "api_key_id": row["api_key_id"],
        "locale": row["locale"] or "en",
        "created_at": row["created_at"],
    }


def verify_email(db: Database, token: str) -> bool:
    """Verify email with token. Returns True if successful."""
    token_hash = hash_token(token)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id FROM provider_accounts WHERE verify_token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            """UPDATE provider_accounts
               SET verified = 1, verify_token_hash = NULL, updated_at = ?
               WHERE id = ?""",
            (datetime.now(timezone.utc).isoformat(), row["id"]),
        )
    return True


def request_password_reset(db: Database, email: str) -> Optional[str]:
    """
    Generate a password reset token for the given email.

    Returns the raw token (to send via email) or None if email not found.
    """
    email = email.strip().lower()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id FROM provider_accounts WHERE email = ? AND status = 'active'",
            (email,),
        ).fetchone()
    if row is None:
        return None

    token = generate_token()
    token_hash = hash_token(token)
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    with db.connect() as conn:
        conn.execute(
            """UPDATE provider_accounts
               SET reset_token_hash = ?, reset_token_expires = ?, updated_at = ?
               WHERE id = ?""",
            (token_hash, expires, datetime.now(timezone.utc).isoformat(), row["id"]),
        )
    return token


def reset_password(db: Database, token: str, new_password: str) -> bool:
    """Reset password using a valid, non-expired token. Returns True on success."""
    is_strong, _ = validate_password_strength(new_password)
    if not is_strong:
        return False
    token_hash = hash_token(token)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, reset_token_expires FROM provider_accounts "
            "WHERE reset_token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None:
            return False

        # Enforce token expiry
        expires_str = row["reset_token_expires"]
        if expires_str:
            try:
                expires_at = datetime.fromisoformat(expires_str)
                if datetime.now(timezone.utc) > expires_at:
                    return False  # Token expired
            except (ValueError, TypeError):
                return False  # Malformed expiry — reject

        hashed = hash_password(new_password)
        conn.execute(
            """UPDATE provider_accounts
               SET hashed_password = ?, reset_token_hash = NULL,
                   reset_token_expires = NULL, updated_at = ?
               WHERE id = ?""",
            (hashed, datetime.now(timezone.utc).isoformat(), row["id"]),
        )
    return True


def update_profile(
    db: Database,
    account_id: str,
    display_name: str | None = None,
    company_name: str | None = None,
) -> bool:
    """Update provider profile fields. Returns True on success."""
    updates = []
    params: list = []
    if display_name is not None:
        updates.append("display_name = ?")
        params.append(display_name)
    if company_name is not None:
        updates.append("company_name = ?")
        params.append(company_name)
    if not updates:
        return True
    updates.append("updated_at = ?")
    params.append(datetime.now(timezone.utc).isoformat())
    params.append(account_id)
    with db.connect() as conn:
        conn.execute(
            f"UPDATE provider_accounts SET {', '.join(updates)} WHERE id = ?",
            params,
        )
    return True


def link_api_key(db: Database, account_id: str, key_id: str) -> None:
    """Link an API key to a provider account."""
    with db.connect() as conn:
        conn.execute(
            "UPDATE provider_accounts SET api_key_id = ?, updated_at = ? WHERE id = ?",
            (key_id, datetime.now(timezone.utc).isoformat(), account_id),
        )


# ---------------------------------------------------------------------------
# PAT (Personal Access Token) expiration management
# ---------------------------------------------------------------------------

_DEFAULT_PAT_EXPIRY_DAYS = int(os.environ.get("ACF_PAT_EXPIRY_DAYS", "90"))

PAT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pat_tokens (
    key_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pat_owner ON pat_tokens(owner_id);
"""


def ensure_pat_table(db: Database) -> None:
    """Create pat_tokens table if not exists."""
    with db.connect() as conn:
        conn.executescript(PAT_TABLE_SQL)


def create_pat_record(
    db: Database,
    key_id: str,
    owner_id: str,
    expiry_days: int | None = None,
) -> dict:
    """Create a PAT expiry record.

    Returns dict with key_id, owner_id, created_at, expires_at.
    """
    if expiry_days is None:
        expiry_days = _DEFAULT_PAT_EXPIRY_DAYS
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=expiry_days)
    record = {
        "key_id": key_id,
        "owner_id": owner_id,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    ensure_pat_table(db)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO pat_tokens (key_id, owner_id, created_at, expires_at)
               VALUES (:key_id, :owner_id, :created_at, :expires_at)
               ON CONFLICT(key_id) DO UPDATE SET
                   owner_id = excluded.owner_id,
                   created_at = excluded.created_at,
                   expires_at = excluded.expires_at""",
            record,
        )
    return record


def validate_pat_expiry(db: Database, key_id: str) -> bool:
    """Check if a PAT token has expired.

    Returns True if valid (not expired), False if expired or not found.
    """
    ensure_pat_table(db)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT expires_at FROM pat_tokens WHERE key_id = ?",
            (key_id,),
        ).fetchone()

    if row is None:
        # No expiry record — treat as valid (backwards compatibility)
        return True

    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) <= expires_at
    except (ValueError, TypeError):
        return False


def delete_pat_record(db: Database, key_id: str) -> None:
    """Delete PAT expiry record (called on revoke)."""
    ensure_pat_table(db)
    with db.connect() as conn:
        conn.execute("DELETE FROM pat_tokens WHERE key_id = ?", (key_id,))
