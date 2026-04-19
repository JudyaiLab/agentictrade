"""Tests for Platform Agent Consumer."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.main import app
from marketplace.auth import APIKeyManager
from marketplace.db import Database
from marketplace.platform_consumer import PlatformConsumer, PLATFORM_BUYER_ID


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "consumer_test.db")


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


@pytest.fixture
def consumer(db):
    return PlatformConsumer(db)


@pytest.fixture
def admin_creds(auth):
    key_id, secret = auth.create_key(owner_id="admin-pc", role="admin")
    return key_id, secret


@pytest.fixture
def client(db, auth):
    with TestClient(app, raise_server_exceptions=False) as c:
        app.state.db = db
        app.state.auth = auth
        yield c


def _headers(creds):
    key_id, secret = creds
    return {"Authorization": f"Bearer {key_id}:{secret}"}


def _insert_service(db, service_id=None, endpoint="https://httpbin.org/get"):
    sid = service_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO services
               (id, name, provider_id, endpoint, price_per_call,
                free_tier_calls, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (id) DO NOTHING""",
            (sid, "Test Service", "prov-1", endpoint, 0.01, 10, "active", now, now),
        )
    return sid


class TestPlatformConsumer:
    """Unit tests for PlatformConsumer."""

    def test_no_services(self, consumer):
        import asyncio
        results = asyncio.run(consumer.consume_all())
        assert results == []

    def test_filters_example_services(self, consumer, db):
        _insert_service(db, endpoint="https://api.example.com/test")
        services = consumer._get_active_services()
        assert len(services) == 0

    def test_includes_real_services(self, consumer, db):
        _insert_service(db, endpoint="https://httpbin.org/get")
        services = consumer._get_active_services()
        assert len(services) == 1

    def test_consumption_stats_empty(self, consumer):
        stats = consumer.get_consumption_stats()
        assert stats["total_calls"] == 0
        assert stats["services_consumed"] == 0

    def test_consumption_stats_after_calls(self, consumer, db):
        # Manually insert platform usage records
        now = datetime.now(timezone.utc).isoformat()
        with db.connect() as conn:
            for i in range(5):
                conn.execute(
                    """INSERT INTO usage_records
                       (id, service_id, buyer_id, provider_id, amount_usd,
                        status_code, latency_ms, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        "svc-1",
                        PLATFORM_BUYER_ID,
                        "prov-1",
                        0.0,
                        200,
                        50,
                        now,
                    ),
                )

        stats = consumer.get_consumption_stats()
        assert stats["total_calls"] == 5
        assert stats["services_consumed"] == 1
        assert stats["per_service"][0]["calls"] == 5

    def test_call_unreachable_service(self, consumer, db):
        """Calling an unreachable service should still record usage."""
        import asyncio
        _insert_service(db, service_id="svc-unreach", endpoint="http://192.0.2.1:9999/api")

        services = consumer._get_active_services()
        result = asyncio.run(consumer.call_service(services[0]))
        assert result["status_code"] >= 500
        assert result["recorded"] is True

    def test_platform_buyer_id_constant(self):
        assert PLATFORM_BUYER_ID == "platform-agent"


class TestPlatformConsumptionAPI:
    """Integration tests for platform consumption admin endpoint."""

    def test_consumption_stats_endpoint(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/platform-consumption",
            headers=_headers(admin_creds),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total_calls" in data
        assert "services_consumed" in data
        assert "per_service" in data

    def test_consumption_stats_with_days(self, client, admin_creds):
        resp = client.get(
            "/api/v1/admin/platform-consumption?days=7",
            headers=_headers(admin_creds),
        )
        assert resp.status_code == 200
        assert resp.json()["period_days"] == 7
