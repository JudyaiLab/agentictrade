"""Tests for billing race condition — concurrent proxy calls.

Verifies that concurrent proxy calls handle balance deduction atomically:
- No double deduction
- Balance never goes negative
- Usage records match successful calls
"""
from __future__ import annotations

import asyncio
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from marketplace.db import Database
from marketplace.proxy import PaymentProxy, ProxyError, ProxyResult


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def proxy(db):
    return PaymentProxy(db, platform_fee_pct=Decimal("0.10"))


def _make_service(**overrides) -> dict:
    """Create a service dict for testing."""
    defaults = {
        "id": "svc-race",
        "provider_id": "prov-race",
        "endpoint": "https://example.com/api",
        "price_per_call": "0.10",
        "payment_method": "x402",
        "free_tier_calls": 0,
        "status": "active",
    }
    defaults.update(overrides)
    return defaults


def _fund_buyer(db: Database, buyer_id: str, amount: str) -> None:
    """Credit buyer balance."""
    db.credit_balance(buyer_id, Decimal(amount))


def _mock_httpx():
    """Create a mock httpx client that returns 200."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.content = b'{"ok": true}'
    mock_response.headers = {"content-type": "application/json"}

    mock_instance = AsyncMock()
    mock_instance.request = AsyncMock(return_value=mock_response)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    return mock_instance


@pytest.mark.asyncio
async def test_concurrent_calls_no_double_deduction(db, proxy):
    """
    Two concurrent proxy calls should not both succeed if balance
    only covers one call.
    """
    service = _make_service(price_per_call="0.10")
    buyer = "race-buyer-1"
    _fund_buyer(db, buyer, "0.15")  # Only enough for 1 call at $0.10

    async def call_once():
        return await proxy.forward_request(
            service=service,
            buyer_id=buyer,
            method="GET",
            path="/test",
        )

    with patch("httpx.AsyncClient", return_value=_mock_httpx()):
        results = await asyncio.gather(
            call_once(), call_once(), return_exceptions=True
        )

    # Count successes and failures
    successes = [r for r in results if isinstance(r, ProxyResult)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(results) == 2

    # Balance should never go below 0 — at most 1 deduction of $0.10
    final_balance = db.get_balance(buyer)
    assert final_balance >= Decimal("0"), f"Balance went negative: {final_balance}"
    assert final_balance == Decimal("0.05"), f"Expected 0.05, got {final_balance}"


@pytest.mark.asyncio
async def test_concurrent_calls_no_negative_balance(db, proxy):
    """
    Balance should never go negative even with many concurrent calls.
    """
    service = _make_service(price_per_call="0.10")
    buyer = "race-buyer-2"
    _fund_buyer(db, buyer, "0.20")  # Can afford 2 calls

    async def make_call():
        try:
            return await proxy.forward_request(
                service=service,
                buyer_id=buyer,
                method="GET",
                path="/test",
            )
        except Exception as e:
            return e

    with patch("httpx.AsyncClient", return_value=_mock_httpx()):
        results = await asyncio.gather(*[make_call() for _ in range(5)])

    final_balance = db.get_balance(buyer)
    assert final_balance >= Decimal("0"), f"Balance went negative: {final_balance}"


@pytest.mark.asyncio
async def test_usage_records_match_successful_calls(db, proxy):
    """
    Usage records should accurately reflect successful calls only.
    """
    service = _make_service(id="svc-count", price_per_call="0.10")
    buyer = "race-buyer-3"
    _fund_buyer(db, buyer, "0.25")  # Can afford 2 calls

    async def make_call():
        try:
            await proxy.forward_request(
                service=service,
                buyer_id=buyer,
                method="GET",
                path="/test",
            )
            return "success"
        except Exception:
            return "fail"

    with patch("httpx.AsyncClient", return_value=_mock_httpx()):
        results = await asyncio.gather(*[make_call() for _ in range(4)])

    successes = [r for r in results if r == "success"]

    with db.connect() as conn:
        usage_count = conn.execute(
            "SELECT COUNT(*) FROM usage_records WHERE buyer_id = ?",
            (buyer,),
        ).fetchone()[0]

    assert usage_count == len(successes), (
        f"Expected {len(successes)} records, got {usage_count}"
    )
