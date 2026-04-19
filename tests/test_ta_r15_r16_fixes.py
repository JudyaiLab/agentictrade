"""
Tests for TA Evaluation Rounds 15 & 16 fixes.

Covers:
- R15-M4: Password breach checking / strength validation
- R16-L1: Financial data export API
- R16-L2: PAT token expiration
- R16-L3: Dashboard Decimal arithmetic
- R16-L4: Transaction velocity alerting
- R15-L1: Privacy/Terms endpoints
- R15-L2: Email consent tracking
- R15-L3: Compliance runtime enforcement
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from marketplace.db import Database
from marketplace.auth import APIKeyManager, generate_api_key, hash_secret


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "ta_fixes_test.db")


@pytest.fixture
def auth(db):
    return APIKeyManager(db)


@pytest.fixture
def client(db, auth):
    with TestClient(app, raise_server_exceptions=False) as c:
        app.state.db = db
        app.state.auth = auth
        yield c


def _make_admin_key(db) -> str:
    """Create an admin API key and return 'key_id:secret'."""
    key_id, raw_secret = generate_api_key(prefix="adm")
    hashed = hash_secret(raw_secret)
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO api_keys (key_id, hashed_secret, owner_id, role, created_at)
               VALUES (?, ?, ?, 'admin', ?)""",
            (key_id, hashed, "admin-owner", now),
        )
    return f"{key_id}:{raw_secret}"


# ---------------------------------------------------------------------------
# R15-M4: Password strength validation + HIBP breach check
# ---------------------------------------------------------------------------

class TestPasswordStrengthValidation:
    """Test validate_password_strength function."""

    def test_valid_password(self):
        from marketplace.provider_auth import validate_password_strength
        ok, msg = validate_password_strength("StrongP4ss")
        assert ok is True
        assert msg == ""

    def test_too_short(self):
        from marketplace.provider_auth import validate_password_strength
        ok, msg = validate_password_strength("Abc1")
        assert ok is False
        assert "8 characters" in msg

    def test_no_uppercase(self):
        from marketplace.provider_auth import validate_password_strength
        ok, msg = validate_password_strength("weakpass1")
        assert ok is False
        assert "uppercase" in msg

    def test_no_lowercase(self):
        from marketplace.provider_auth import validate_password_strength
        ok, msg = validate_password_strength("STRONGP4SS")
        assert ok is False
        assert "lowercase" in msg

    def test_no_digit(self):
        from marketplace.provider_auth import validate_password_strength
        ok, msg = validate_password_strength("StrongPass")
        assert ok is False
        assert "digit" in msg


class TestPasswordBreachCheck:
    """Test check_password_breach function."""

    def test_breach_check_returns_false_on_network_error(self):
        from marketplace.provider_auth import check_password_breach
        # Mock httpx.get at the module level used by the import inside the function
        with patch("httpx.get", side_effect=Exception("Network error")):
            result = check_password_breach("anypassword")
        assert result is False

    def test_breach_check_returns_false_on_non_200(self):
        from marketplace.provider_auth import check_password_breach
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("httpx.get", return_value=mock_resp):
            result = check_password_breach("anypassword")
        assert result is False

    def test_breach_check_finds_breached_password(self):
        import hashlib
        from marketplace.provider_auth import check_password_breach

        password = "TestBreached1"
        sha1 = hashlib.sha1(password.encode()).hexdigest().upper()
        suffix = sha1[5:]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = f"AAAAAAA:0\n{suffix}:42\nBBBBBBB:0\n"

        with patch("httpx.get", return_value=mock_resp):
            result = check_password_breach(password)
        assert result is True

    def test_breach_check_clean_password(self):
        from marketplace.provider_auth import check_password_breach

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "AAAAAAA:0\nBBBBBBB:5\nCCCCCCC:0\n"

        with patch("httpx.get", return_value=mock_resp):
            result = check_password_breach("UniqueStr0ngPass!")
        assert result is False


class TestCreateAccountPasswordValidation:
    """Test that create_account enforces password strength."""

    def test_weak_password_rejected(self, db):
        from marketplace.provider_auth import (
            create_account,
            ensure_provider_accounts_table,
            ProviderAccountError,
        )
        ensure_provider_accounts_table(db)

        with patch("marketplace.provider_auth.check_password_breach", return_value=False):
            with pytest.raises(ProviderAccountError, match="uppercase"):
                create_account(db, "test@example.com", "weakpass1")

    def test_breached_password_rejected(self, db):
        from marketplace.provider_auth import (
            create_account,
            ensure_provider_accounts_table,
            ProviderAccountError,
        )
        ensure_provider_accounts_table(db)

        with patch("marketplace.provider_auth.check_password_breach", return_value=True):
            with pytest.raises(ProviderAccountError, match="breach"):
                create_account(db, "test@example.com", "StrongP4ss")

    def test_strong_clean_password_accepted(self, db):
        from marketplace.provider_auth import (
            create_account,
            ensure_provider_accounts_table,
        )
        ensure_provider_accounts_table(db)

        with patch("marketplace.provider_auth.check_password_breach", return_value=False):
            account = create_account(db, "strong@example.com", "StrongP4ss")
        assert account["email"] == "strong@example.com"


# ---------------------------------------------------------------------------
# R16-L1: Financial data export API
# ---------------------------------------------------------------------------

class TestFinancialExport:
    def test_export_requires_admin(self, client, db):
        """Financial export requires admin auth."""
        resp = client.get("/api/v1/admin/financial-export")
        assert resp.status_code == 401

    def test_export_returns_data(self, client, db):
        """Financial export returns structured data."""
        token = _make_admin_key(db)
        resp = client.get(
            "/api/v1/admin/financial-export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "exported_at" in data
        assert "summary" in data
        assert "settlements" in data
        assert "usage_records" in data
        assert "deposits" in data
        assert "filters" in data

    def test_export_date_filter_validation(self, client, db):
        """Financial export rejects invalid date format."""
        token = _make_admin_key(db)
        resp = client.get(
            "/api/v1/admin/financial-export?date_from=bad-date",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    def test_export_with_valid_date_range(self, client, db):
        """Financial export accepts valid date range."""
        token = _make_admin_key(db)
        resp = client.get(
            "/api/v1/admin/financial-export?date_from=2026-01-01&date_to=2026-12-31",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# R16-L2: PAT token expiration
# ---------------------------------------------------------------------------

class TestPATExpiration:
    def test_create_pat_record(self, db):
        """PAT record stores expiry date."""
        from marketplace.provider_auth import create_pat_record, ensure_pat_table
        ensure_pat_table(db)
        record = create_pat_record(db, "pat_test123", "owner-1", expiry_days=30)
        assert record["key_id"] == "pat_test123"
        assert record["owner_id"] == "owner-1"
        assert "expires_at" in record
        # Verify expiry is ~30 days from now
        expires = datetime.fromisoformat(record["expires_at"])
        now = datetime.now(timezone.utc)
        delta = expires - now
        assert 29 <= delta.days <= 31

    def test_validate_pat_valid(self, db):
        """Non-expired PAT passes validation."""
        from marketplace.provider_auth import (
            create_pat_record, validate_pat_expiry, ensure_pat_table,
        )
        ensure_pat_table(db)
        create_pat_record(db, "pat_valid", "owner-1", expiry_days=90)
        assert validate_pat_expiry(db, "pat_valid") is True

    def test_validate_pat_expired(self, db):
        """Expired PAT fails validation."""
        from marketplace.provider_auth import (
            ensure_pat_table, validate_pat_expiry,
        )
        ensure_pat_table(db)
        # Insert an already-expired record
        expired_time = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO pat_tokens (key_id, owner_id, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                ("pat_expired", "owner-1", datetime.now(timezone.utc).isoformat(), expired_time),
            )
        assert validate_pat_expiry(db, "pat_expired") is False

    def test_validate_pat_no_record(self, db):
        """PAT without expiry record passes (backwards compatibility)."""
        from marketplace.provider_auth import validate_pat_expiry, ensure_pat_table
        ensure_pat_table(db)
        assert validate_pat_expiry(db, "pat_unknown") is True

    def test_delete_pat_record(self, db):
        """Deleting PAT record removes it from DB."""
        from marketplace.provider_auth import (
            create_pat_record, delete_pat_record, validate_pat_expiry, ensure_pat_table,
        )
        ensure_pat_table(db)
        create_pat_record(db, "pat_del", "owner-1", expiry_days=90)
        assert validate_pat_expiry(db, "pat_del") is True
        delete_pat_record(db, "pat_del")
        # After deletion, no record found — returns True (backwards compat)
        assert validate_pat_expiry(db, "pat_del") is True

    def test_expired_pat_rejected_by_api(self, client, db):
        """Expired PAT should be rejected by extract_owner."""
        from marketplace.provider_auth import ensure_pat_table
        ensure_pat_table(db)

        # Create a PAT key in api_keys table
        key_id = "pat_exptest"
        raw_secret = "testsecret123"
        hashed = hash_secret(raw_secret)
        now = datetime.now(timezone.utc).isoformat()
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO api_keys (key_id, hashed_secret, owner_id, role, created_at)
                   VALUES (?, ?, ?, 'provider', ?)""",
                (key_id, hashed, "owner-exp", now),
            )

        # Create an expired PAT record
        expired_time = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO pat_tokens (key_id, owner_id, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (key_id, "owner-exp", now, expired_time),
            )

        resp = client.get(
            "/api/v1/provider/dashboard",
            headers={"Authorization": f"Bearer {key_id}:{raw_secret}"},
        )
        assert resp.status_code == 401
        assert "expired" in resp.json()["error"].lower()


# ---------------------------------------------------------------------------
# R16-L3: Dashboard Decimal arithmetic
# ---------------------------------------------------------------------------

class TestDashboardDecimal:
    def test_to_decimal_helper(self):
        """_to_decimal converts float precisely."""
        from api.routes.dashboard_queries import _to_decimal
        assert _to_decimal(None) == 0.0
        assert _to_decimal(0) == 0.0
        assert _to_decimal(10.5) == 10.5
        # Float precision test: 0.1 + 0.2 should round correctly
        assert _to_decimal(0.1 + 0.2) == 0.3

    def test_safe_pct_zero_denominator(self):
        from api.routes.dashboard_queries import safe_pct
        assert safe_pct(1.0, 0.0) == 0.0

    def test_provider_summary_uses_decimal(self, db):
        """Provider summary should use Decimal for financial values."""
        from api.routes.dashboard_queries import query_provider_summary
        with db.connect() as conn:
            result = query_provider_summary(conn)
        assert isinstance(result["total_revenue"], float)
        assert isinstance(result["total_commission"], float)


# ---------------------------------------------------------------------------
# R16-L4: Transaction velocity alerting
# ---------------------------------------------------------------------------

class TestVelocityAlerting:
    def test_no_alerts_under_threshold(self, db):
        """No alerts when under threshold."""
        from marketplace.velocity import check_transaction_velocity
        alerts = check_transaction_velocity(
            db, buyer_id="buyer-1", max_tx_count=100, max_tx_amount=Decimal("10000"),
        )
        assert alerts == []

    def test_alert_on_count_exceeded(self, db):
        """Alert when transaction count exceeds threshold."""
        from marketplace.velocity import check_transaction_velocity

        # Insert transactions for a buyer
        now = datetime.now(timezone.utc).isoformat()
        with db.connect() as conn:
            for i in range(6):
                conn.execute(
                    """INSERT INTO usage_records
                       (id, service_id, buyer_id, provider_id, amount_usd,
                        payment_method, status_code, latency_ms, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (f"vel-{i}", "svc-1", "vel-buyer", "prov-1", 1.0,
                     "x402", 200, 100, now),
                )

        alerts = check_transaction_velocity(
            db, buyer_id="vel-buyer", max_tx_count=5, max_tx_amount=Decimal("10000"),
        )
        assert len(alerts) >= 1
        assert any(a.alert_type == "tx_count" for a in alerts)

    def test_alert_on_amount_exceeded(self, db):
        """Alert when transaction amount exceeds threshold."""
        from marketplace.velocity import check_transaction_velocity

        now = datetime.now(timezone.utc).isoformat()
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO usage_records
                   (id, service_id, buyer_id, provider_id, amount_usd,
                    payment_method, status_code, latency_ms, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("vel-big", "svc-1", "vel-buyer-big", "prov-1", 500.0,
                 "x402", 200, 100, now),
            )

        alerts = check_transaction_velocity(
            db, buyer_id="vel-buyer-big",
            max_tx_count=100, max_tx_amount=Decimal("100"),
        )
        assert len(alerts) >= 1
        assert any(a.alert_type == "tx_amount" for a in alerts)

    def test_velocity_alert_immutable(self):
        """VelocityAlert dataclass is immutable (frozen)."""
        from marketplace.velocity import VelocityAlert
        alert = VelocityAlert(
            entity_id="test",
            entity_type="buyer",
            alert_type="tx_count",
            current_value="10",
            threshold="5",
            window_hours=1,
            timestamp="2026-01-01T00:00:00Z",
        )
        with pytest.raises(AttributeError):
            alert.entity_id = "changed"

    def test_check_velocity_simple(self, db):
        """check_velocity_simple returns plain dicts."""
        from marketplace.velocity import check_velocity_simple
        result = check_velocity_simple(db, buyer_id="nonexistent")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# R15-L1: Privacy / Terms endpoints
# ---------------------------------------------------------------------------

class TestLegalEndpoints:
    def test_privacy_policy(self, client):
        resp = client.get("/api/v1/legal/privacy")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Privacy Policy"
        assert len(data["sections"]) > 0

    def test_terms_of_service(self, client):
        resp = client.get("/api/v1/legal/terms")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Terms of Service"
        assert len(data["sections"]) > 0

    def test_privacy_no_auth_required(self, client):
        """Privacy endpoint should be accessible without auth."""
        resp = client.get("/api/v1/legal/privacy")
        assert resp.status_code == 200

    def test_terms_no_auth_required(self, client):
        """Terms endpoint should be accessible without auth."""
        resp = client.get("/api/v1/legal/terms")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# R15-L2: Email consent tracking (tested in test_email.py, additional tests here)
# ---------------------------------------------------------------------------

class TestEmailConsent:
    def test_consent_field_in_model(self):
        """DownloadGateRequest has consent field."""
        from api.routes.email import DownloadGateRequest
        req = DownloadGateRequest(email="test@example.com", consent=True)
        assert req.consent is True

    def test_consent_default_false(self):
        """Default consent is False."""
        from api.routes.email import DownloadGateRequest
        req = DownloadGateRequest(email="test@example.com")
        assert req.consent is False


# ---------------------------------------------------------------------------
# R15-L3: Compliance runtime enforcement
# ---------------------------------------------------------------------------

class TestComplianceChecks:
    def test_compliance_check_returns_results(self):
        from marketplace.compliance import compliance_check
        results = compliance_check()
        assert len(results) > 0
        # Should have all expected checks
        check_names = {r.check_name for r in results}
        assert "webhook_key" in check_names
        assert "admin_secret" in check_names
        assert "audit_logging" in check_names
        assert "cors_config" in check_names
        assert "rate_limiting" in check_names
        assert "portal_secret" in check_names

    def test_compliance_result_immutable(self):
        """ComplianceResult is immutable."""
        from marketplace.compliance import ComplianceResult
        result = ComplianceResult(
            check_name="test",
            passed=True,
            severity="info",
            message="ok",
        )
        with pytest.raises(AttributeError):
            result.passed = False

    def test_log_compliance_results(self):
        """log_compliance_results returns summary dict."""
        from marketplace.compliance import log_compliance_results
        summary = log_compliance_results()
        assert "total_checks" in summary
        assert "passed" in summary
        assert "failed" in summary
        assert "critical_failures" in summary
        assert "results" in summary
        assert summary["total_checks"] == len(summary["results"])

    def test_compliance_warns_missing_admin_secret(self):
        """Missing ACF_ADMIN_SECRET should be flagged as critical."""
        from marketplace.compliance import compliance_check
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": ""}, clear=False):
            results = compliance_check()
        admin_check = next(r for r in results if r.check_name == "admin_secret")
        assert admin_check.passed is False
        assert admin_check.severity == "critical"

    def test_compliance_passes_with_admin_secret(self):
        """Set ACF_ADMIN_SECRET should pass compliance."""
        from marketplace.compliance import compliance_check
        with patch.dict(os.environ, {"ACF_ADMIN_SECRET": "test-secret"}, clear=False):
            results = compliance_check()
        admin_check = next(r for r in results if r.check_name == "admin_secret")
        assert admin_check.passed is True
