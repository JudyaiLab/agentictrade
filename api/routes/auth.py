"""
API Key management routes.

Includes brute-force protection: 5 failed auth attempts per IP
within 60 seconds triggers a temporary lockout.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from marketplace.auth import AuthError

router = APIRouter(tags=["auth"])


MAX_RATE_LIMIT = 300  # Server-enforced cap on per-key rate limit

# --- Brute-force protection ---
_AUTH_FAIL_WINDOW = 60  # seconds
_AUTH_FAIL_MAX = 5      # max failures per window

# {ip: [(timestamp, ...),]} — tracks recent auth failures
_auth_failures: dict[str, list[float]] = defaultdict(list)


def _check_brute_force(client_ip: str) -> None:
    """Raise 429 if IP has exceeded auth failure limit."""
    now = time.monotonic()
    window = _auth_failures[client_ip]
    # Prune old entries
    _auth_failures[client_ip] = [t for t in window if now - t < _AUTH_FAIL_WINDOW]
    if len(_auth_failures[client_ip]) >= _AUTH_FAIL_MAX:
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again later.",
        )


def _record_auth_failure(client_ip: str) -> None:
    """Record a failed auth attempt for brute-force tracking."""
    _auth_failures[client_ip].append(time.monotonic())


class CreateKeyRequest(BaseModel):
    owner_id: str
    role: str = "buyer"
    rate_limit: int = 60
    wallet_address: Optional[str] = None


class ValidateKeyRequest(BaseModel):
    key_id: str
    secret: str


ALLOWED_ROLES = {"buyer", "provider", "admin"}


@router.post("/keys", status_code=201)
async def create_api_key(req: CreateKeyRequest, request: Request):
    """Create a new API key. Returns the secret once only.

    Buyer keys can be created without authentication.
    Provider and admin keys require an existing authenticated Bearer token.
    """
    auth_mgr = request.app.state.auth

    if req.role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"role must be one of {ALLOWED_ROLES}",
        )

    # Enforce rate_limit cap to prevent self-assigned unlimited rates
    if req.rate_limit < 1 or req.rate_limit > MAX_RATE_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"rate_limit must be between 1 and {MAX_RATE_LIMIT}",
        )

    # Provider and admin key creation requires authentication
    if req.role in ("admin", "provider"):
        client_ip = request.client.host if request.client else "unknown"
        _check_brute_force(client_ip)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail=f"{req.role.title()} key creation requires authentication",
            )
        token = auth_header[7:]
        parts = token.split(":", 1)
        if len(parts) != 2:
            _record_auth_failure(client_ip)
            raise HTTPException(status_code=401, detail="Invalid key format")
        try:
            caller = auth_mgr.validate(parts[0], parts[1])
        except AuthError:
            _record_auth_failure(client_ip)
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if req.role == "admin" and caller["role"] not in ("admin", "provider", "buyer"):
            raise HTTPException(
                status_code=403, detail="Authentication required to create admin keys"
            )
        # Any authenticated key can create provider keys (enables bootstrapping)
        if req.role == "provider" and caller["role"] not in ("provider", "admin", "buyer"):
            raise HTTPException(
                status_code=403,
                detail="Authentication required to create provider keys",
            )

    try:
        key_id, raw_secret = auth_mgr.create_key(
            owner_id=req.owner_id,
            role=req.role,
            rate_limit=req.rate_limit,
            wallet_address=req.wallet_address,
        )

        # Audit log: key_created
        audit = getattr(request.app.state, "audit", None)
        if audit is not None:
            client_ip = request.client.host if request.client else ""
            audit.log_event(
                event_type="key_created",
                actor=req.owner_id,
                target=key_id,
                details=f"role={req.role}",
                ip_address=client_ip,
            )

        return {
            "key_id": key_id,
            "secret": raw_secret,
            "role": req.role,
            "rate_limit": req.rate_limit,
            "message": "Save the secret — it cannot be retrieved again.",
        }
    except AuthError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/keys/validate")
async def validate_api_key(req: ValidateKeyRequest, request: Request):
    """Validate an API key pair.

    Brute-force protected: 5 failures per IP within 60s triggers 429.
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_brute_force(client_ip)

    auth = request.app.state.auth
    try:
        record = auth.validate(req.key_id, req.secret)
        return {
            "valid": True,
            "owner_id": record["owner_id"],
            "role": record["role"],
            "rate_limit": record["rate_limit"],
        }
    except AuthError as e:
        _record_auth_failure(client_ip)

        # Audit log: auth_failure
        audit = getattr(request.app.state, "audit", None)
        if audit is not None:
            audit.log_event(
                event_type="auth_failure",
                actor=req.key_id,
                target="keys/validate",
                details=str(e),
                ip_address=client_ip,
            )
        raise HTTPException(status_code=401, detail=str(e))
