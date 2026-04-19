"""
Tests for billing routes — balance, deposits, admin credit, IPN callback.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from marketplace.auth import APIKeyManager
from marketplace.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "billing_test.db")


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


@pytest.fixture
def buyer_creds(auth):
    """Create a buyer API key and return (key_id, secret, owner_id)."""
    key_id, secret = auth.create_key(owner_id="test-buyer", role="buyer")
    return key_id, secret, "test-buyer"


@pytest.fixture
def client(db, auth):
    with TestClient(app, raise_server_exceptions=False) as c:
        app.state.db = db
        app.state.auth = auth
        yield c


def _auth_headers(key_id: str, secret: str) -> dict:
    return {"Authorization": f"Bearer {key_id}:{secret}"}


# ---------------------------------------------------------------------------
# GET /api/v1/balance/{buyer_id}
# ---------------------------------------------------------------------------

class TestGetBalance:
    def test_balance_zero_for_new_buyer(self, client, auth):
        key_id, secret = auth.create_key(owner_id="new-buyer", role="buyer")
        resp = client.get(
            "/api/v1/balance/new-buyer",
            headers=_auth_headers(key_id, secret),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["buyer_id"] == "new-buyer"
        assert data["balance"] == 0
        assert data["total_deposited"] == 0
        assert data["total_spent"] == 0

    def test_balance_after_credit(self, client, db, auth):
        key_id, secret = auth.create_key(owner_id="funded-buyer", role="buyer")
        db.credit_balance("funded-buyer", Decimal("50.00"))
        resp = client.get(
            "/api/v1/balance/funded-buyer",
            headers=_auth_headers(key_id, secret),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["buyer_id"] == "funded-buyer"
        assert data["balance"] == 50.0
        assert data["total_deposited"] == 50.0

    def test_balance_after_multiple_credits(self, client, db, auth):
        key_id, secret = auth.create_key(owner_id="multi-buyer", role="buyer")
        db.credit_balance("multi-buyer", Decimal("10.00"))
        db.credit_balance("multi-buyer", Decimal("25.00"))
        resp = client.get(
            "/api/v1/balance/multi-buyer",
            headers=_auth_headers(key_id, secret),
        )
        assert resp.status_code == 200
        assert resp.json()["balance"] == 35.0

    def test_balance_unauthenticated_rejected(self, client):
        resp = client.get("/api/v1/balance/any-buyer")
        assert resp.status_code == 401

    def test_balance_wrong_buyer_rejected(self, client, auth):
        key_id, secret = auth.create_key(owner_id="buyer-a", role="buyer")
        resp = client.get(
            "/api/v1/balance/buyer-b",
            headers=_auth_headers(key_id, secret),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/v1/deposits
# ---------------------------------------------------------------------------

class TestCreateDeposit:
    def test_deposit_without_nowpayments_key(self, client, auth):
        """Without NOWPAYMENTS_API_KEY, deposit is created in fallback mode."""
        key_id, secret = auth.create_key(owner_id="test-depositor", role="buyer")
        with patch.dict(os.environ, {"NOWPAYMENTS_API_KEY": ""}, clear=False):
            resp = client.post("/api/v1/deposits", json={
                "amount": 25.0,
                "buyer_id": "test-depositor",
            }, headers=_auth_headers(key_id, secret))
        assert resp.status_code == 200
        data = resp.json()
        assert data["amount"] == 25.0
        assert data["status"] == "pending"
        assert data["checkout_url"] is None
        assert "deposit_id" in data
        assert len(data["deposit_id"]) == 36  # UUID format

    def test_deposit_unauthenticated_rejected(self, client):
        resp = client.post("/api/v1/deposits", json={
            "amount": 25.0,
            "buyer_id": "test-buyer",
        })
        assert resp.status_code == 401

    def test_deposit_wrong_buyer_rejected(self, client, auth):
        """Cannot create deposit for another buyer_id."""
        key_id, secret = auth.create_key(owner_id="buyer-x", role="buyer")
        resp = client.post("/api/v1/deposits", json={
            "amount": 25.0,
            "buyer_id": "buyer-y",
        }, headers=_auth_headers(key_id, secret))
        assert resp.status_code == 403

    def test_deposit_validation_amount_zero(self, client, auth):
        key_id, secret = auth.create_key(owner_id="test-buyer-z", role="buyer")
        resp = client.post("/api/v1/deposits", json={
            "amount": 0,
            "buyer_id": "test-buyer-z",
        }, headers=_auth_headers(key_id, secret))
        assert resp.status_code == 422  # Pydantic validation

    def test_deposit_validation_amount_negative(self, client, auth):
        key_id, secret = auth.create_key(owner_id="test-buyer-n", role="buyer")
        resp = client.post("/api/v1/deposits", json={
            "amount": -10,
            "buyer_id": "test-buyer-n",
        }, headers=_auth_headers(key_id, secret))
        assert resp.status_code == 422

    def test_deposit_validation_amount_too_large(self, client, auth):
        key_id, secret = auth.create_key(owner_id="test-buyer-l", role="buyer")
        resp = client.post("/api/v1/deposits", json={
            "amount": 20000,
            "buyer_id": "test-buyer-l",
        }, headers=_auth_headers(key_id, secret))
        assert resp.status_code == 422

    def test_deposit_validation_empty_buyer_id(self, client):
        resp = client.post("/api/v1/deposits", json={
            "amount": 25.0,
            "buyer_id": "",
        })
        assert resp.status_code == 422

    def test_deposit_stored_in_db(self, client, db, auth):
        key_id, secret = auth.create_key(owner_id="db-check-buyer", role="buyer")
        with patch.dict(os.environ, {"NOWPAYMENTS_API_KEY": ""}, clear=False):
            resp = client.post("/api/v1/deposits", json={
                "amount": 30.0,
                "buyer_id": "db-check-buyer",
            }, headers=_auth_headers(key_id, secret))
        deposit_id = resp.json()["deposit_id"]
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM deposits WHERE id = ?", (deposit_id,)
            ).fetchone()
        assert row is not None
        assert float(row["amount"]) == 30.0
        assert row["buyer_id"] == "db-check-buyer"
        assert row["payment_status"] == "pending"


# ---------------------------------------------------------------------------
# POST /api/v1/admin/credit
# ---------------------------------------------------------------------------

class TestAdminCredit:
    def test_admin_credit_success(self, client):
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "test-secret"}, clear=False):
            resp = client.post(
                "/api/v1/admin/credit",
                params={"buyer_id": "admin-buyer", "amount": 100.0},
                headers={"x-admin-key": "test-secret"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["buyer_id"] == "admin-buyer"
        assert data["credited"] == 100.0
        assert data["new_balance"] == 100.0

    def test_admin_credit_wrong_key(self, client):
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "real-secret"}, clear=False):
            resp = client.post(
                "/api/v1/admin/credit",
                params={"buyer_id": "admin-buyer", "amount": 50.0},
                headers={"x-admin-key": "wrong-key"},
            )
        assert resp.status_code == 401

    def test_admin_credit_no_secret_configured(self, client):
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": ""}, clear=False):
            resp = client.post(
                "/api/v1/admin/credit",
                params={"buyer_id": "admin-buyer", "amount": 50.0},
                headers={"x-admin-key": "anything"},
            )
        assert resp.status_code == 503

    def test_admin_credit_accumulates(self, client, db, auth):
        key_id, secret = auth.create_key(owner_id="accum-buyer", role="buyer")
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "s"}, clear=False):
            client.post("/api/v1/admin/credit", params={
                "buyer_id": "accum-buyer", "amount": 10.0,
            }, headers={"x-admin-key": "s"})
            client.post("/api/v1/admin/credit", params={
                "buyer_id": "accum-buyer", "amount": 15.0,
            }, headers={"x-admin-key": "s"})
            resp = client.get(
                "/api/v1/balance/accum-buyer",
                headers=_auth_headers(key_id, secret),
            )
        assert resp.json()["balance"] == 25.0


# ---------------------------------------------------------------------------
# POST /api/v1/ipn/nowpayments
# ---------------------------------------------------------------------------

class TestNowPaymentsIPN:
    IPN_SECRET = "test-ipn-secret-for-testing"

    def _make_deposit(self, db, deposit_id: str, buyer_id: str, amount: float, payment_id: str | None = None):
        """Insert a pending deposit directly into DB."""
        from datetime import datetime, timezone
        db.insert_deposit({
            "id": deposit_id,
            "buyer_id": buyer_id,
            "amount": amount,
            "currency": "USDC",
            "payment_provider": "nowpayments",
            "payment_id": payment_id,
            "payment_status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    def _signed_post(self, client, payload: dict):
        """POST to IPN endpoint with valid HMAC signature."""
        sorted_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        sig = hmac.new(
            self.IPN_SECRET.encode(), sorted_payload.encode(), hashlib.sha512,
        ).hexdigest()
        with patch("api.routes.billing.NOWPAYMENTS_IPN_SECRET", self.IPN_SECRET):
            return client.post(
                "/api/v1/ipn/nowpayments",
                json=payload,
                headers={"x-nowpayments-sig": sig},
            )

    def test_ipn_confirmed_credits_balance(self, client, db, auth):
        """IPN with 'finished' status should credit buyer balance."""
        self._make_deposit(db, "dep-001", "ipn-buyer", 50.0, "pay-123")
        key_id, secret = auth.create_key(owner_id="ipn-buyer", role="buyer")
        resp = self._signed_post(client, {
            "payment_id": "pay-123",
            "payment_status": "finished",
            "order_id": "dep-001",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "balance_credited"
        assert data["amount"] == 50.0
        # Verify balance
        bal = client.get(
            "/api/v1/balance/ipn-buyer",
            headers=_auth_headers(key_id, secret),
        ).json()
        assert bal["balance"] == 50.0

    def test_ipn_pending_status_ignored(self, client, db, auth):
        """IPN with non-confirmed status should be ignored."""
        self._make_deposit(db, "dep-002", "ipn-buyer-2", 30.0, "pay-456")
        key_id, secret = auth.create_key(owner_id="ipn-buyer-2", role="buyer")
        resp = self._signed_post(client, {
            "payment_id": "pay-456",
            "payment_status": "waiting",
            "order_id": "dep-002",
        })
        assert resp.status_code == 200
        assert resp.json()["action"] == "ignored"
        # Balance should not be credited
        bal = client.get(
            "/api/v1/balance/ipn-buyer-2",
            headers=_auth_headers(key_id, secret),
        ).json()
        assert bal["balance"] == 0

    def test_ipn_no_matching_deposit(self, client):
        """IPN for unknown payment should return no_matching_deposit."""
        resp = self._signed_post(client, {
            "payment_id": "unknown-pay",
            "payment_status": "finished",
            "order_id": "unknown-dep",
        })
        assert resp.status_code == 200
        assert resp.json()["action"] == "no_matching_deposit"

    def test_ipn_by_order_id_fallback(self, client, db):
        """If payment_id doesn't match, fall back to order_id (deposit_id)."""
        self._make_deposit(db, "dep-fallback", "fallback-buyer", 40.0, "pay-no-match")
        resp = self._signed_post(client, {
            "payment_id": "totally-different",
            "payment_status": "confirmed",
            "order_id": "dep-fallback",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "balance_credited"
        assert data["buyer_id"] == "fallback-buyer"

    def test_ipn_invalid_json(self, client):
        """Non-JSON body should return 400."""
        with patch("api.routes.billing.NOWPAYMENTS_IPN_SECRET", self.IPN_SECRET):
            resp = client.post(
                "/api/v1/ipn/nowpayments",
                content=b"not json",
                headers={
                    "content-type": "application/json",
                    "x-nowpayments-sig": "dummy",
                },
            )
        assert resp.status_code == 400

    def test_ipn_signature_verification(self, client, db):
        """Valid HMAC signature should be accepted."""
        self._make_deposit(db, "dep-sig", "sig-buyer", 25.0, "pay-sig")
        resp = self._signed_post(client, {
            "payment_id": "pay-sig",
            "payment_status": "finished",
            "order_id": "dep-sig",
        })
        assert resp.status_code == 200
        assert resp.json()["action"] == "balance_credited"

    def test_ipn_invalid_signature_rejected(self, client, db):
        """Invalid HMAC signature should be rejected with 400."""
        self._make_deposit(db, "dep-badsig", "badsig-buyer", 25.0, "pay-badsig")
        payload = {
            "payment_id": "pay-badsig",
            "payment_status": "finished",
            "order_id": "dep-badsig",
        }
        with patch("api.routes.billing.NOWPAYMENTS_IPN_SECRET", self.IPN_SECRET):
            resp = client.post(
                "/api/v1/ipn/nowpayments",
                json=payload,
                headers={"x-nowpayments-sig": "invalid-signature"},
            )
        assert resp.status_code == 400

    def test_ipn_rejected_when_secret_not_configured(self, client):
        """IPN should be rejected with 503 when secret is not set."""
        with patch("api.routes.billing.NOWPAYMENTS_IPN_SECRET", ""):
            resp = client.post("/api/v1/ipn/nowpayments", json={
                "payment_id": "test",
                "payment_status": "finished",
            })
        assert resp.status_code == 503

    def test_ipn_missing_signature_header_rejected(self, client):
        """IPN without signature header should be rejected."""
        with patch("api.routes.billing.NOWPAYMENTS_IPN_SECRET", self.IPN_SECRET):
            resp = client.post("/api/v1/ipn/nowpayments", json={
                "payment_id": "test",
                "payment_status": "finished",
            })
        assert resp.status_code == 400
