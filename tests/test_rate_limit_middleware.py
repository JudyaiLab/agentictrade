"""Tests for rate limiting middleware with rate limit headers."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from marketplace.rate_limit import RateLimiter, create_rate_limiter
from marketplace.auth import APIKeyManager
from marketplace.db import Database


@pytest.fixture
def app_with_rate_limit():
    """Create a test app with rate limiting middleware."""
    app = FastAPI()
    rate_limiter = RateLimiter(rate=5, per=60.0, burst=5)

    @app.middleware("http")
    async def rate_limit_middleware(request, call_next):
        """Rate limit middleware with standard headers."""
        client_ip = request.client.host if request.client else "unknown"

        allowed = rate_limiter.allow(client_ip)
        remaining, reset_seconds = rate_limiter.get_limit_info(client_ip)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": "5",
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_seconds),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = "5"
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_seconds)
        return response

    @app.get("/test")
    def test_endpoint():
        return {"ok": True}

    return app, rate_limiter


class TestRateLimitMiddlewareHeaders:
    """Test rate limit headers in middleware responses."""

    def test_headers_on_successful_request(self, app_with_rate_limit):
        app, rate_limiter = app_with_rate_limit
        client = TestClient(app)

        response = client.get("/test")

        assert response.status_code == 200
        assert "X-RateLimit-Limit" in response.headers
        assert response.headers["X-RateLimit-Limit"] == "5"
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers
        # After first request, should have 4 remaining
        assert response.headers["X-RateLimit-Remaining"] == "4"

    def test_headers_on_rate_limited_response(self, app_with_rate_limit):
        app, rate_limiter = app_with_rate_limit
        client = TestClient(app)

        # Use up all requests
        for _ in range(5):
            response = client.get("/test")
            assert response.status_code == 200

        # Next request should be rate limited
        response = client.get("/test")

        assert response.status_code == 429
        assert "X-RateLimit-Limit" in response.headers
        assert response.headers["X-RateLimit-Limit"] == "5"
        assert "X-RateLimit-Remaining" in response.headers
        assert response.headers["X-RateLimit-Remaining"] == "0"
        assert "X-RateLimit-Reset" in response.headers
        assert "Retry-After" in response.headers

    def test_remaining_decrements(self, app_with_rate_limit):
        app, rate_limiter = app_with_rate_limit
        client = TestClient(app)

        # First request
        response1 = client.get("/test")
        remaining1 = int(response1.headers["X-RateLimit-Remaining"])

        # Second request
        response2 = client.get("/test")
        remaining2 = int(response2.headers["X-RateLimit-Remaining"])

        assert response1.status_code == 200
        assert response2.status_code == 200
        assert remaining2 == remaining1 - 1

    def test_reset_time_is_reasonable(self, app_with_rate_limit):
        app, rate_limiter = app_with_rate_limit
        client = TestClient(app)

        response = client.get("/test")

        reset_seconds = int(response.headers["X-RateLimit-Reset"])
        assert reset_seconds > 0
        assert reset_seconds <= 60  # Window is 60 seconds

    def test_headers_with_multiple_clients(self, app_with_rate_limit):
        app, rate_limiter = app_with_rate_limit
        # Use different IPs to simulate different clients
        client1 = TestClient(app)
        client2 = TestClient(app)

        # Make requests with explicit headers to set different IPs
        # (TestClient simulates requests from 127.0.0.1 by default)
        response1 = client1.get("/test", headers={"x-forwarded-for": "192.168.1.1"})
        response2 = client2.get("/test", headers={"x-forwarded-for": "192.168.1.2"})

        # Both should be allowed
        assert response1.status_code == 200
        assert response2.status_code == 200

        # Note: TestClient doesn't interpret x-forwarded-for headers for client.host
        # Both requests will come from 127.0.0.1, so they share the same bucket
        # This test validates that the headers are present on both responses
        remaining1 = int(response1.headers["X-RateLimit-Remaining"])
        remaining2 = int(response2.headers["X-RateLimit-Remaining"])

        # Since both use same IP (127.0.0.1), the second should have one less
        assert response1.headers["X-RateLimit-Limit"] == "5"
        assert response2.headers["X-RateLimit-Limit"] == "5"
        assert remaining2 == remaining1 - 1


# ---------------------------------------------------------------------------
# Per-API-key rate limit middleware tests
# ---------------------------------------------------------------------------


def _build_app_with_per_key_rl(
    tmp_path,
    *,
    ip_rate: int = 100,
    key_rate_limit: int = 3,
) -> tuple[FastAPI, TestClient, str, str, APIKeyManager]:
    """Build a minimal FastAPI app that replicates the production middleware
    logic for per-key rate limiting (without importing the full ACF app)."""
    import os
    from marketplace.auth import APIKeyManager
    from marketplace.rate_limit import RateLimiter

    db = Database(tmp_path / "test_perkey.db")
    key_manager = APIKeyManager(db)
    rate_limiter = RateLimiter(rate=ip_rate, per=60.0, burst=ip_rate)

    # Create a key whose rate_limit we control
    key_id, raw_secret = key_manager.create_key(
        owner_id="test-owner",
        role="buyer",
        rate_limit=key_rate_limit,
    )
    bearer_token = f"{key_id}:{raw_secret}"

    # Helper: duplicate the _extract_api_key_id logic inline
    def extract_key_id(request):
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return None
        token = auth[7:].strip()
        if ":" not in token:
            return None
        return token.split(":", 1)[0]

    app = FastAPI()

    @app.middleware("http")
    async def middleware(request, call_next):
        client_ip = request.client.host if request.client else "unknown"

        # Layer 1: IP
        ip_ok = rate_limiter.allow(client_ip)
        ip_remaining, ip_reset = rate_limiter.get_limit_info(client_ip)
        if not ip_ok:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers={
                    "Retry-After": str(ip_reset),
                    "X-RateLimit-Limit": str(ip_rate),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(ip_reset),
                },
            )

        # Layer 2: per-key
        kid = extract_key_id(request)
        if kid:
            record = key_manager.db.get_api_key(kid)
            if record:
                klimit = record.get("rate_limit") or 60
                result = key_manager.check_rate_limit(kid, klimit)
                if result is not True:
                    retry_after = int(result)
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "API key rate limit exceeded. Try again later."},
                        headers={
                            "Retry-After": str(retry_after),
                            "X-RateLimit-Limit": str(klimit),
                            "X-RateLimit-Remaining": "0",
                            "X-RateLimit-Reset": str(retry_after),
                        },
                    )
                effective_limit = klimit
            else:
                effective_limit = ip_rate
        else:
            effective_limit = ip_rate

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(effective_limit)
        response.headers["X-RateLimit-Remaining"] = str(ip_remaining)
        response.headers["X-RateLimit-Reset"] = str(ip_reset)
        return response

    @app.get("/test")
    def test_endpoint():
        return {"ok": True}

    client = TestClient(app)
    return app, client, key_id, bearer_token, key_manager


class TestPerKeyRateLimitMiddleware:
    """Verify that the per-API-key rate limit layer is enforced by the middleware."""

    def test_requests_within_key_limit_succeed(self, tmp_path):
        _, client, _, token, _ = _build_app_with_per_key_rl(tmp_path, key_rate_limit=3)
        for _ in range(3):
            r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200

    def test_request_over_key_limit_returns_429(self, tmp_path):
        _, client, _, token, _ = _build_app_with_per_key_rl(tmp_path, key_rate_limit=2)
        # Exhaust the per-key allowance
        client.get("/test", headers={"Authorization": f"Bearer {token}"})
        client.get("/test", headers={"Authorization": f"Bearer {token}"})
        # Third request must be blocked by the key limit
        r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 429
        assert "API key rate limit exceeded" in r.json()["detail"]

    def test_429_response_includes_rate_limit_headers(self, tmp_path):
        _, client, _, token, _ = _build_app_with_per_key_rl(tmp_path, key_rate_limit=1)
        client.get("/test", headers={"Authorization": f"Bearer {token}"})
        r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 429
        assert r.headers["X-RateLimit-Limit"] == "1"
        assert r.headers["X-RateLimit-Remaining"] == "0"
        assert "Retry-After" in r.headers
        assert int(r.headers["Retry-After"]) >= 1

    def test_no_key_falls_back_to_ip_limit(self, tmp_path):
        """Unauthenticated requests use the IP limit only and must succeed."""
        _, client, _, _, _ = _build_app_with_per_key_rl(tmp_path, ip_rate=100, key_rate_limit=1)
        # Not passing an Authorization header — should hit IP bucket only
        r = client.get("/test")
        assert r.status_code == 200
        assert r.headers["X-RateLimit-Limit"] == "100"

    def test_different_keys_have_independent_counters(self, tmp_path):
        """Two distinct API keys must not share a rate limit window."""
        from marketplace.db import Database
        from marketplace.auth import APIKeyManager
        from marketplace.rate_limit import RateLimiter

        db = Database(tmp_path / "indep.db")
        km = APIKeyManager(db)
        rl = RateLimiter(rate=100, per=60.0, burst=100)

        kid1, sec1 = km.create_key("owner-1", rate_limit=1)
        kid2, sec2 = km.create_key("owner-2", rate_limit=5)
        token1 = f"{kid1}:{sec1}"
        token2 = f"{kid2}:{sec2}"

        app = FastAPI()

        def _kid(request):
            auth = request.headers.get("authorization", "")
            if not auth.lower().startswith("bearer "):
                return None
            t = auth[7:].strip()
            return t.split(":", 1)[0] if ":" in t else None

        @app.middleware("http")
        async def mw(request, call_next):
            rl.allow(request.client.host if request.client else "unknown")
            kid = _kid(request)
            if kid:
                record = km.db.get_api_key(kid)
                if record:
                    klimit = record.get("rate_limit") or 60
                    result = km.check_rate_limit(kid, klimit)
                    if result is not True:
                        return JSONResponse(
                            status_code=429,
                            content={"detail": "API key rate limit exceeded. Try again later."},
                        )
            return await call_next(request)

        @app.get("/test")
        def ep():
            return {"ok": True}

        c = TestClient(app)
        # Exhaust key1 (limit=1) — first succeeds, second blocked
        assert c.get("/test", headers={"Authorization": f"Bearer {token1}"}).status_code == 200
        assert c.get("/test", headers={"Authorization": f"Bearer {token1}"}).status_code == 429
        # key2 (limit=5) must still be fine
        assert c.get("/test", headers={"Authorization": f"Bearer {token2}"}).status_code == 200

    def test_ip_limit_still_enforced_without_key(self, tmp_path):
        """IP rate limit fires even when no API key is supplied."""
        _, client, _, _, _ = _build_app_with_per_key_rl(tmp_path, ip_rate=2, key_rate_limit=100)
        assert client.get("/test").status_code == 200
        assert client.get("/test").status_code == 200
        assert client.get("/test").status_code == 429
