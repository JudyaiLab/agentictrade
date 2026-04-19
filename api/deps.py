"""
Shared API dependencies — authentication, rate limiting, common extractors.
Single source of truth for auth logic used across all route modules.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from marketplace.auth import AuthError


def extract_owner(request: Request) -> tuple[str, dict]:
    """
    Extract authenticated owner_id and key record from Authorization header.

    Expected format: Bearer key_id:secret

    Returns (owner_id, key_record).
    Raises HTTPException on auth failure.
    PAT tokens (pat_ prefix) are also checked for expiration.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="API key required")

    token = auth_header[7:]
    parts = token.split(":", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=401,
            detail="Invalid key format. Use key_id:secret",
        )

    key_id, secret = parts
    key_mgr = request.app.state.auth
    try:
        record = key_mgr.validate(key_id, secret)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))

    # Check PAT token expiration
    if key_id.startswith("pat_"):
        try:
            from marketplace.provider_auth import validate_pat_expiry
            db = request.app.state.db
            if not validate_pat_expiry(db, key_id):
                raise HTTPException(
                    status_code=401,
                    detail="API token has expired. Generate a new token from the portal.",
                )
        except ImportError:
            pass  # provider_auth not available — skip expiry check

    return record["owner_id"], record


def require_admin(request: Request) -> tuple[str, dict]:
    """Extract owner and verify admin role."""
    owner_id, record = extract_owner(request)
    if record["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return owner_id, record


def require_provider(request: Request) -> tuple[str, dict]:
    """Extract owner and verify provider or admin role."""
    owner_id, record = extract_owner(request)
    if record["role"] not in ("provider", "admin"):
        raise HTTPException(status_code=403, detail="Provider access required")
    return owner_id, record
