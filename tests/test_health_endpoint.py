"""Tests for /health (public) and /health/details (admin-only) endpoints."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from marketplace.auth import APIKeyManager
from marketplace.db import Database


@pytest.fixture
def db(tmp_path):
    """Fresh isolated DB for each test."""
    return Database(tmp_path / "health_test.db")


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


@pytest.fixture
def admin_creds(auth):
    """Create admin API key; return 'key_id:secret' string."""
    key_id, secret = auth.create_key(owner_id="health-admin", role="admin")
    return f"{key_id}:{secret}"


@pytest.fixture
def client(db, auth):
    """TestClient with fresh DB injected after startup."""
    with TestClient(app, raise_server_exceptions=False) as c:
        app.state.db = db
        app.state.auth = auth
        app.state.payment_router = None  # no payment providers by default
        yield c


# ---------------------------------------------------------------------------
# Public /health — minimal response, no metrics
# ---------------------------------------------------------------------------

class TestPublicHealth:
    def test_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_status_is_ok(self, client):
        data = client.get("/health").json()
        assert data["status"] == "ok"

    def test_no_checks_exposed(self, client):
        """Public health must NOT expose internal metrics."""
        data = client.get("/health").json()
        assert "checks" not in data
        assert "timestamp" not in data

    def test_no_services_count_exposed(self, client):
        data = client.get("/health").json()
        assert "services_count" not in data

    def test_no_auth_required(self, client):
        """Public health should work without any auth header."""
        resp = client.get("/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Admin /health/details — full metrics, requires auth
# ---------------------------------------------------------------------------

class TestHealthDetailsAuth:
    def test_requires_auth(self, client):
        """health/details without auth returns 401."""
        resp = client.get("/health/details")
        assert resp.status_code == 401

    def test_rejects_non_admin(self, client, auth):
        """Non-admin key should get 403."""
        key_id, secret = auth.create_key(owner_id="buyer-1", role="buyer")
        resp = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {key_id}:{secret}"},
        )
        assert resp.status_code == 403


class TestHealthDetailsHealthyDB:
    def test_returns_200_when_db_ok(self, client, admin_creds):
        resp = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        )
        assert resp.status_code == 200

    def test_status_is_ok(self, client, admin_creds):
        data = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()
        assert data["status"] == "ok"

    def test_timestamp_present(self, client, admin_creds):
        data = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()
        assert "timestamp" in data
        assert data["timestamp"]  # non-empty

    def test_checks_block_present(self, client, admin_creds):
        data = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()
        assert "checks" in data

    def test_database_check_ok(self, client, admin_creds):
        checks = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()["checks"]
        assert checks["database"]["status"] == "ok"
        assert "latency_ms" in checks["database"]
        assert isinstance(checks["database"]["latency_ms"], (int, float))
        assert checks["database"]["latency_ms"] >= 0

    def test_services_count_present(self, client, admin_creds):
        checks = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()["checks"]
        assert "services_count" in checks
        assert isinstance(checks["services_count"], int)

    def test_payment_providers_empty_when_none_configured(self, client, admin_creds):
        checks = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()["checks"]
        assert checks["payment_providers"] == []

    def test_backward_compat_fields_preserved(self, client, admin_creds):
        """Existing consumers relying on 'status' and 'timestamp' must not break."""
        data = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()
        assert "status" in data
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# Unhealthy DB → 503 (admin details)
# ---------------------------------------------------------------------------

class TestHealthDetailsUnhealthyDB:
    def test_returns_503_when_db_unreachable(self, client, admin_creds):
        """Simulate DB failure — /health/details must return 503."""
        broken_db = MagicMock()
        broken_db.connect.side_effect = Exception("disk I/O error")
        app.state.db = broken_db

        resp = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        )
        assert resp.status_code == 503

    def test_status_degraded_when_db_fails(self, client, admin_creds):
        broken_db = MagicMock()
        broken_db.connect.side_effect = Exception("connection refused")
        app.state.db = broken_db

        data = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()
        assert data["status"] == "degraded"

    def test_database_check_shows_error(self, client, admin_creds):
        broken_db = MagicMock()
        broken_db.connect.side_effect = Exception("connection refused")
        app.state.db = broken_db

        checks = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()["checks"]
        assert checks["database"]["status"] == "error"

    def test_services_count_zero_when_db_fails(self, client, admin_creds):
        broken_db = MagicMock()
        broken_db.connect.side_effect = Exception("connection refused")
        app.state.db = broken_db

        checks = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()["checks"]
        assert checks["services_count"] == 0

    def test_timestamp_still_present_when_db_fails(self, client, admin_creds):
        """Timestamp must be returned even on DB failure."""
        broken_db = MagicMock()
        broken_db.connect.side_effect = Exception("disk I/O error")
        app.state.db = broken_db

        data = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()
        assert "timestamp" in data
        assert data["timestamp"]

    def test_db_error_detail_included(self, client, admin_creds):
        broken_db = MagicMock()
        broken_db.connect.side_effect = Exception("disk I/O error")
        app.state.db = broken_db

        checks = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()["checks"]
        assert "detail" in checks["database"]
        assert "disk I/O error" in checks["database"]["detail"]


# ---------------------------------------------------------------------------
# Payment providers (admin details)
# ---------------------------------------------------------------------------

class TestPaymentProviders:
    def test_configured_providers_listed(self, client, admin_creds):
        """When payment router has providers, list them in the response."""
        mock_router = MagicMock()
        mock_router._providers = {"x402": object(), "agentkit": object()}
        app.state.payment_router = mock_router

        checks = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()["checks"]
        assert set(checks["payment_providers"]) == {"x402", "agentkit"}

    def test_no_providers_returns_empty_list(self, client, admin_creds):
        app.state.payment_router = None
        checks = client.get(
            "/health/details",
            headers={"Authorization": f"Bearer {admin_creds}"},
        ).json()["checks"]
        assert checks["payment_providers"] == []
