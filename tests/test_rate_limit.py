"""Tests for rate limiter module."""
from __future__ import annotations

import time

import pytest

from marketplace.db import Database
from marketplace.rate_limit import (
    RateLimiter,
    DatabaseRateLimiter,
    create_rate_limiter,
)


class TestRateLimiter:
    def test_allows_within_limit(self):
        limiter = RateLimiter(rate=10, per=60.0)
        for _ in range(10):
            assert limiter.allow("client-1")

    def test_blocks_over_limit(self):
        limiter = RateLimiter(rate=3, per=60.0, burst=3)
        assert limiter.allow("client-1")
        assert limiter.allow("client-1")
        assert limiter.allow("client-1")
        assert not limiter.allow("client-1")

    def test_separate_clients(self):
        limiter = RateLimiter(rate=2, per=60.0, burst=2)
        assert limiter.allow("client-1")
        assert limiter.allow("client-1")
        assert not limiter.allow("client-1")
        # Different client should still be allowed
        assert limiter.allow("client-2")

    def test_burst_allows_more(self):
        limiter = RateLimiter(rate=5, per=60.0, burst=10)
        for _ in range(10):
            assert limiter.allow("client-1")
        assert not limiter.allow("client-1")

    def test_refills_over_time(self):
        limiter = RateLimiter(rate=100, per=1.0, burst=1)
        assert limiter.allow("client-1")
        assert not limiter.allow("client-1")
        time.sleep(0.02)  # Enough for ~2 tokens at 100/sec
        assert limiter.allow("client-1")

    def test_cleanup_removes_stale(self):
        limiter = RateLimiter(rate=10, per=60.0)
        limiter.allow("old-client")
        # Manually age the bucket
        limiter._buckets["old-client"].last_refill = time.monotonic() - 7200
        removed = limiter.cleanup(max_age=3600)
        assert removed == 1
        assert "old-client" not in limiter._buckets

    def test_cleanup_keeps_active(self):
        limiter = RateLimiter(rate=10, per=60.0)
        limiter.allow("active-client")
        removed = limiter.cleanup(max_age=3600)
        assert removed == 0
        assert "active-client" in limiter._buckets

    def test_default_burst_equals_rate(self):
        limiter = RateLimiter(rate=50, per=60.0)
        assert limiter.burst == 50

    def test_empty_string_key(self):
        limiter = RateLimiter(rate=5, per=60.0)
        assert limiter.allow("")

    def test_reset_clears_all(self):
        limiter = RateLimiter(rate=5, per=60.0)
        limiter.allow("a")
        limiter.allow("b")
        limiter.reset()
        assert len(limiter._buckets) == 0


# --- DatabaseRateLimiter ---


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test_rl.db")


class TestDatabaseRateLimiter:
    def test_allows_within_limit(self, db):
        limiter = DatabaseRateLimiter(db, rate=5, window_seconds=60.0)
        for _ in range(5):
            assert limiter.allow("client-1")

    def test_blocks_over_limit(self, db):
        limiter = DatabaseRateLimiter(db, rate=3, window_seconds=60.0)
        assert limiter.allow("client-1")
        assert limiter.allow("client-1")
        assert limiter.allow("client-1")
        assert not limiter.allow("client-1")

    def test_separate_clients(self, db):
        limiter = DatabaseRateLimiter(db, rate=2, window_seconds=60.0)
        assert limiter.allow("client-1")
        assert limiter.allow("client-1")
        assert not limiter.allow("client-1")
        # Different client should still be allowed
        assert limiter.allow("client-2")

    def test_window_reset(self, db):
        """Counter resets after window expires."""
        limiter = DatabaseRateLimiter(db, rate=2, window_seconds=0.05)
        assert limiter.allow("client-1")
        assert limiter.allow("client-1")
        time.sleep(0.06)
        # Window expired — counter should reset
        assert limiter.allow("client-1")

    def test_reset_clears_all(self, db):
        limiter = DatabaseRateLimiter(db, rate=5, window_seconds=60.0)
        limiter.allow("a")
        limiter.allow("b")
        limiter.reset()
        # After reset, all clients should be allowed again
        for _ in range(5):
            assert limiter.allow("a")

    def test_cleanup_removes_old(self, db):
        limiter = DatabaseRateLimiter(db, rate=5, window_seconds=60.0)
        limiter.allow("old-client")
        # Manually age the window
        from datetime import datetime, timedelta, timezone
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with db.connect() as conn:
            conn.execute(
                "UPDATE rate_limit_windows SET window_start = ? WHERE key = ?",
                (old_time, "old-client"),
            )
        removed = limiter.cleanup(max_age=3600)
        assert removed == 1

    def test_cleanup_keeps_recent(self, db):
        limiter = DatabaseRateLimiter(db, rate=5, window_seconds=60.0)
        limiter.allow("recent-client")
        removed = limiter.cleanup(max_age=3600)
        assert removed == 0

    def test_empty_string_key(self, db):
        limiter = DatabaseRateLimiter(db, rate=5, window_seconds=60.0)
        assert limiter.allow("")


# --- create_rate_limiter factory ---


class TestCreateRateLimiter:
    def test_default_memory(self):
        limiter = create_rate_limiter()
        assert isinstance(limiter, RateLimiter)

    def test_explicit_memory(self):
        limiter = create_rate_limiter(backend="memory", rate=30, per=30.0, burst=60)
        assert isinstance(limiter, RateLimiter)
        assert limiter.rate == 30
        assert limiter.per == 30.0
        assert limiter.burst == 60

    def test_database_backend(self, db):
        limiter = create_rate_limiter(backend="database", db=db, rate=100, per=60.0)
        assert isinstance(limiter, DatabaseRateLimiter)
        assert limiter.rate == 100

    def test_database_requires_db(self):
        with pytest.raises(ValueError, match="Database instance required"):
            create_rate_limiter(backend="database")

    def test_factory_limiter_works(self, db):
        limiter = create_rate_limiter(backend="database", db=db, rate=2, per=60.0)
        assert limiter.allow("x")
        assert limiter.allow("x")
        assert not limiter.allow("x")
