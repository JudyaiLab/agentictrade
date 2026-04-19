"""Tests for PostgreSQL connection pool in marketplace/db.py.

These tests use unittest.mock to avoid requiring a live PostgreSQL server.
SQLite behaviour is also verified to remain unchanged.
"""
from __future__ import annotations

import asyncio
import os
import threading
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_pool(maxconn: int = 20):
    """Return a MagicMock that behaves like ThreadedConnectionPool."""
    pool = MagicMock()
    connections = [MagicMock() for _ in range(maxconn)]
    # Each getconn() call returns the next connection
    pool.getconn.side_effect = connections
    return pool, connections


def _pg_env(monkeypatch, url: str = "postgresql://user:pw@host/db"):
    monkeypatch.setenv("DATABASE_URL", url)


# ---------------------------------------------------------------------------
# Test: pool created on PostgreSQL init
# ---------------------------------------------------------------------------

class TestPoolCreation:
    def test_pool_created_when_database_url_is_postgres(self, monkeypatch, tmp_path):
        """ThreadedConnectionPool must be instantiated when DATABASE_URL is postgresql://."""
        _pg_env(monkeypatch)
        monkeypatch.delenv("PG_POOL_MIN", raising=False)
        monkeypatch.delenv("PG_POOL_MAX", raising=False)

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("psycopg2.pool.ThreadedConnectionPool", return_value=mock_pool) as mock_cls, \
             patch("psycopg2.connect"):  # guard against accidental direct connect
            from importlib import reload
            import marketplace.db as db_mod
            reload(db_mod)

            db = db_mod.Database()

            mock_cls.assert_called_once_with(
                2, 100, "postgresql://user:pw@host/db",
                keepalives=1, keepalives_idle=30,
                keepalives_interval=10, keepalives_count=3,
            )
            assert db._pg_pool is mock_pool

    def test_pool_uses_env_var_sizes(self, monkeypatch, tmp_path):
        """PG_POOL_MIN and PG_POOL_MAX override the defaults."""
        _pg_env(monkeypatch)
        monkeypatch.setenv("PG_POOL_MIN", "5")
        monkeypatch.setenv("PG_POOL_MAX", "50")

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("psycopg2.pool.ThreadedConnectionPool", return_value=mock_pool) as mock_cls, \
             patch("psycopg2.connect"):
            from importlib import reload
            import marketplace.db as db_mod
            reload(db_mod)

            db_mod.Database()

            mock_cls.assert_called_once_with(
                5, 50, "postgresql://user:pw@host/db",
                keepalives=1, keepalives_idle=30,
                keepalives_interval=10, keepalives_count=3,
            )

    def test_no_pool_for_sqlite(self, monkeypatch, tmp_path):
        """SQLite backend must not create a connection pool."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        from importlib import reload
        import marketplace.db as db_mod
        reload(db_mod)

        db = db_mod.Database(db_path=tmp_path / "test.db")
        assert db._pg_pool is None


# ---------------------------------------------------------------------------
# Test: pool.getconn / pool.putconn called in connect()
# ---------------------------------------------------------------------------

class TestPoolConnectionLifecycle:
    def _build_db_with_pool(self, monkeypatch):
        """Return a Database instance whose pool is fully mocked."""
        _pg_env(monkeypatch)
        monkeypatch.delenv("PG_POOL_MIN", raising=False)
        monkeypatch.delenv("PG_POOL_MAX", raising=False)

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.autocommit = False
        mock_pool.getconn.return_value = mock_conn

        # Mock cursor used for executescript during _init_schema
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch("psycopg2.pool.ThreadedConnectionPool", return_value=mock_pool), \
             patch("psycopg2.connect"):
            from importlib import reload
            import marketplace.db as db_mod
            reload(db_mod)
            db = db_mod.Database()

        # Reset call counts so _init_schema noise is gone
        mock_pool.reset_mock()
        mock_conn.reset_mock()
        mock_conn.autocommit = False
        mock_pool.getconn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        return db, mock_pool, mock_conn

    def test_getconn_called_on_connect(self, monkeypatch):
        """connect() must call pool.getconn() to obtain a connection."""
        db, mock_pool, mock_conn = self._build_db_with_pool(monkeypatch)

        with db.connect():
            mock_pool.getconn.assert_called_once()

    def test_putconn_called_after_connect(self, monkeypatch):
        """connect() must return the connection to the pool in the finally block."""
        db, mock_pool, mock_conn = self._build_db_with_pool(monkeypatch)

        with db.connect():
            pass

        mock_pool.putconn.assert_called_once_with(mock_conn)

    def test_putconn_called_even_on_exception(self, monkeypatch):
        """Pool.putconn must be called even when an exception escapes the block."""
        db, mock_pool, mock_conn = self._build_db_with_pool(monkeypatch)

        with pytest.raises(RuntimeError):
            with db.connect():
                raise RuntimeError("boom")

        mock_pool.putconn.assert_called_once_with(mock_conn)
        mock_conn.rollback.assert_called_once()

    def test_direct_connect_not_called(self, monkeypatch):
        """psycopg2.connect() must NOT be called during normal usage (pool only)."""
        _pg_env(monkeypatch)
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.autocommit = False
        mock_pool.getconn.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch("psycopg2.pool.ThreadedConnectionPool", return_value=mock_pool), \
             patch("psycopg2.connect") as mock_direct:
            from importlib import reload
            import marketplace.db as db_mod
            reload(db_mod)
            db = db_mod.Database()
            mock_direct.reset_mock()

            with db.connect():
                pass

        mock_direct.assert_not_called()


# ---------------------------------------------------------------------------
# Test: pool respects maxconn (PoolError on exhaustion)
# ---------------------------------------------------------------------------

class TestPoolExhaustion:
    def test_pool_raises_when_exhausted(self, monkeypatch):
        """When the pool has no connections left it raises PoolError."""
        import psycopg2.pool as pg_pool_mod
        _pg_env(monkeypatch)

        mock_pool = MagicMock()
        mock_pool.getconn.side_effect = pg_pool_mod.PoolError("exhausted")

        mock_conn = MagicMock()
        mock_conn.autocommit = False
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        # Allow init to succeed with a working connection, then simulate exhaustion
        init_pool = MagicMock()
        init_conn = MagicMock()
        init_conn.autocommit = False
        init_conn.cursor.return_value = mock_cursor
        init_pool.getconn.return_value = init_conn

        with patch("psycopg2.pool.ThreadedConnectionPool", return_value=init_pool):
            from importlib import reload
            import marketplace.db as db_mod
            reload(db_mod)
            db = db_mod.Database()

        # Now replace the pool with the exhausted one
        db._pg_pool = mock_pool

        with pytest.raises(pg_pool_mod.PoolError):
            with db.connect():
                pass


# ---------------------------------------------------------------------------
# Test: SQLite path is unchanged
# ---------------------------------------------------------------------------

class TestSQLiteUnchanged:
    def test_sqlite_connect_still_works(self, monkeypatch, tmp_path):
        """SQLite mode must work identically and not involve any pool."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        from importlib import reload
        import marketplace.db as db_mod
        reload(db_mod)

        db = db_mod.Database(db_path=tmp_path / "test.db")
        assert db._pg_pool is None

        with db.connect() as conn:
            conn.execute("SELECT 1")

    def test_close_pool_noop_for_sqlite(self, monkeypatch, tmp_path):
        """close_pool() must be a no-op for SQLite (no pool to close)."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        from importlib import reload
        import marketplace.db as db_mod
        reload(db_mod)

        db = db_mod.Database(db_path=tmp_path / "test.db")
        # Must not raise
        db.close_pool()
        assert db._pg_pool is None


# ---------------------------------------------------------------------------
# Test: close_pool()
# ---------------------------------------------------------------------------

class TestClosePool:
    def test_close_pool_calls_closeall(self, monkeypatch):
        """close_pool() must call pool.closeall() and set _pg_pool to None."""
        _pg_env(monkeypatch)

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.autocommit = False
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_pool.getconn.return_value = mock_conn

        with patch("psycopg2.pool.ThreadedConnectionPool", return_value=mock_pool), \
             patch("psycopg2.connect"):
            from importlib import reload
            import marketplace.db as db_mod
            reload(db_mod)
            db = db_mod.Database()

        db.close_pool()

        mock_pool.closeall.assert_called_once()
        assert db._pg_pool is None

    def test_close_pool_idempotent(self, monkeypatch):
        """Calling close_pool() twice must not raise."""
        _pg_env(monkeypatch)

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.autocommit = False
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_pool.getconn.return_value = mock_conn

        with patch("psycopg2.pool.ThreadedConnectionPool", return_value=mock_pool), \
             patch("psycopg2.connect"):
            from importlib import reload
            import marketplace.db as db_mod
            reload(db_mod)
            db = db_mod.Database()

        db.close_pool()
        db.close_pool()  # second call must be safe


# ---------------------------------------------------------------------------
# Test: arun() async helper
# ---------------------------------------------------------------------------

class TestArun:
    @pytest.mark.asyncio
    async def test_arun_dispatches_sync_method(self, monkeypatch, tmp_path):
        """arun() should execute a sync Database method via ThreadPoolExecutor."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        from importlib import reload
        import marketplace.db as db_mod
        reload(db_mod)

        db = db_mod.Database(db_path=tmp_path / "arun_test.db")
        result = await db.arun(db.check_connection)
        assert result is True

    @pytest.mark.asyncio
    async def test_arun_passes_args_and_kwargs(self, monkeypatch, tmp_path):
        """arun() must forward positional and keyword arguments."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        from importlib import reload
        import marketplace.db as db_mod
        reload(db_mod)

        db = db_mod.Database(db_path=tmp_path / "arun_args.db")

        # Insert a usage record, then get stats via arun
        now = "2026-01-15T00:00:00Z"
        db.insert_usage({
            "id": "u-arun-1",
            "buyer_id": "buyer-1",
            "service_id": "svc-arun",
            "provider_id": "prov-1",
            "timestamp": now,
            "amount_usd": "5.00",
        })

        stats = await db.arun(db.get_usage_stats, service_id="svc-arun")
        assert stats["total_calls"] == 1

    @pytest.mark.asyncio
    async def test_arun_propagates_exceptions(self, monkeypatch, tmp_path):
        """Exceptions raised inside arun() must propagate to the caller."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        from importlib import reload
        import marketplace.db as db_mod
        reload(db_mod)

        db = db_mod.Database(db_path=tmp_path / "arun_exc.db")

        def _boom():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            await db.arun(_boom)

    @pytest.mark.asyncio
    async def test_arun_runs_on_different_thread(self, monkeypatch, tmp_path):
        """arun() must execute the callable on a non-main thread."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        from importlib import reload
        import marketplace.db as db_mod
        reload(db_mod)

        db = db_mod.Database(db_path=tmp_path / "arun_thread.db")

        main_thread = threading.current_thread().ident

        def _get_thread_id():
            return threading.current_thread().ident

        worker_thread = await db.arun(_get_thread_id)
        assert worker_thread != main_thread


# ---------------------------------------------------------------------------
# Test: keepalive parameters
# ---------------------------------------------------------------------------

class TestKeepaliveParams:
    def test_keepalive_params_in_pool_creation(self, monkeypatch):
        """Pool creation must include TCP keepalive parameters."""
        _pg_env(monkeypatch)
        monkeypatch.delenv("PG_POOL_MIN", raising=False)
        monkeypatch.delenv("PG_POOL_MAX", raising=False)

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("psycopg2.pool.ThreadedConnectionPool", return_value=mock_pool) as mock_cls, \
             patch("psycopg2.connect"):
            from importlib import reload
            import marketplace.db as db_mod
            reload(db_mod)

            db_mod.Database()

            _, kwargs = mock_cls.call_args
            assert kwargs["keepalives"] == 1
            assert kwargs["keepalives_idle"] == 30
            assert kwargs["keepalives_interval"] == 10
            assert kwargs["keepalives_count"] == 3


# ---------------------------------------------------------------------------
# Test: get_usage_stats default 30-day window
# ---------------------------------------------------------------------------

class TestUsageStatsDefaultWindow:
    def _make_db(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from importlib import reload
        import marketplace.db as db_mod
        reload(db_mod)
        return db_mod.Database(db_path=tmp_path / "usage_stats.db")

    def test_no_filters_defaults_to_30_days(self, monkeypatch, tmp_path):
        """get_usage_stats with no filters should only scan last 30 days."""
        db = self._make_db(monkeypatch, tmp_path)
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(days=5)).isoformat()
        old_ts = (now - timedelta(days=60)).isoformat()

        # Insert a recent and an old record
        db.insert_usage({
            "id": "u-recent",
            "buyer_id": "b1",
            "service_id": "s1",
            "provider_id": "p1",
            "timestamp": recent_ts,
            "amount_usd": "10.00",
        })
        db.insert_usage({
            "id": "u-old",
            "buyer_id": "b2",
            "service_id": "s2",
            "provider_id": "p2",
            "timestamp": old_ts,
            "amount_usd": "20.00",
        })

        # No filters → should default to 30-day window, excluding the old record
        stats = db.get_usage_stats()
        assert stats["total_calls"] == 1
        from decimal import Decimal
        assert stats["total_revenue"] == Decimal("10.00")

    def test_explicit_since_overrides_default(self, monkeypatch, tmp_path):
        """Explicit since parameter should be used instead of the 30-day default."""
        db = self._make_db(monkeypatch, tmp_path)
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(days=60)).isoformat()

        db.insert_usage({
            "id": "u-1",
            "buyer_id": "b1",
            "service_id": "s1",
            "provider_id": "p1",
            "timestamp": old_ts,
            "amount_usd": "10.00",
        })

        # With explicit since=90 days ago, should include the old record
        since_90 = (now - timedelta(days=90)).isoformat()
        stats = db.get_usage_stats(since=since_90)
        assert stats["total_calls"] == 1

    def test_service_id_filter_skips_default_window(self, monkeypatch, tmp_path):
        """When service_id is provided, no default 30-day window should apply."""
        db = self._make_db(monkeypatch, tmp_path)
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(days=60)).isoformat()

        db.insert_usage({
            "id": "u-1",
            "buyer_id": "b1",
            "service_id": "svc-target",
            "provider_id": "p1",
            "timestamp": old_ts,
            "amount_usd": "10.00",
        })

        # service_id provided → no automatic since default
        stats = db.get_usage_stats(service_id="svc-target")
        assert stats["total_calls"] == 1

    def test_buyer_id_filter_skips_default_window(self, monkeypatch, tmp_path):
        """When buyer_id is provided, no default 30-day window should apply."""
        db = self._make_db(monkeypatch, tmp_path)
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(days=60)).isoformat()

        db.insert_usage({
            "id": "u-1",
            "buyer_id": "buyer-target",
            "service_id": "s1",
            "provider_id": "p1",
            "timestamp": old_ts,
            "amount_usd": "10.00",
        })

        # buyer_id provided → no automatic since default
        stats = db.get_usage_stats(buyer_id="buyer-target")
        assert stats["total_calls"] == 1


# ---------------------------------------------------------------------------
# Test: list_escrow_holds pagination
# ---------------------------------------------------------------------------

class TestEscrowHoldsPagination:
    def _make_db(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from importlib import reload
        import marketplace.db as db_mod
        reload(db_mod)
        return db_mod.Database(db_path=tmp_path / "escrow_pag.db")

    def _insert_hold(self, db, hold_id, provider_id="prov-1", status="held"):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO escrow_holds
                   (id, provider_id, service_id, buyer_id, amount, status,
                    held_at, created_at, release_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (hold_id, provider_id, "svc-1", "buyer-1", "10.00",
                 status, now, now, now),
            )

    def test_default_limit_is_50(self, monkeypatch, tmp_path):
        """Default limit should be 50, not 1000."""
        db = self._make_db(monkeypatch, tmp_path)
        # Insert 60 holds
        for i in range(60):
            self._insert_hold(db, f"hold-{i:03d}")

        holds = db.list_escrow_holds()
        assert len(holds) == 50

    def test_offset_parameter(self, monkeypatch, tmp_path):
        """offset parameter should skip records."""
        db = self._make_db(monkeypatch, tmp_path)
        for i in range(10):
            self._insert_hold(db, f"hold-{i:03d}")

        all_holds = db.list_escrow_holds(limit=10)
        offset_holds = db.list_escrow_holds(limit=10, offset=5)
        assert len(offset_holds) == 5
        # The offset results should be the last 5 of the full list
        assert [h["id"] for h in offset_holds] == [h["id"] for h in all_holds[5:]]

    def test_limit_capped_at_1000(self, monkeypatch, tmp_path):
        """Requesting limit > 1000 should be capped at 1000."""
        db = self._make_db(monkeypatch, tmp_path)
        self._insert_hold(db, "hold-1")

        # Should not raise; limit gets capped internally
        holds = db.list_escrow_holds(limit=5000)
        assert len(holds) == 1

    def test_negative_offset_treated_as_zero(self, monkeypatch, tmp_path):
        """Negative offset should be treated as 0."""
        db = self._make_db(monkeypatch, tmp_path)
        self._insert_hold(db, "hold-1")

        holds = db.list_escrow_holds(offset=-10)
        assert len(holds) == 1

    def test_negative_limit_treated_as_one(self, monkeypatch, tmp_path):
        """Negative/zero limit should be treated as 1."""
        db = self._make_db(monkeypatch, tmp_path)
        for i in range(5):
            self._insert_hold(db, f"hold-{i}")

        holds = db.list_escrow_holds(limit=0)
        assert len(holds) == 1

    def test_pagination_with_filters(self, monkeypatch, tmp_path):
        """Pagination should work together with provider_id and status filters."""
        db = self._make_db(monkeypatch, tmp_path)
        for i in range(10):
            self._insert_hold(db, f"hold-a-{i}", provider_id="prov-a")
        for i in range(5):
            self._insert_hold(db, f"hold-b-{i}", provider_id="prov-b")

        page1 = db.list_escrow_holds(provider_id="prov-a", limit=3, offset=0)
        page2 = db.list_escrow_holds(provider_id="prov-a", limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        # No overlap between pages
        ids_1 = {h["id"] for h in page1}
        ids_2 = {h["id"] for h in page2}
        assert ids_1.isdisjoint(ids_2)
