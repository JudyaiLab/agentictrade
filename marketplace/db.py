"""
Database operations for Agent Commerce Framework.
Supports both SQLite (default / tests) and PostgreSQL (production).

Backend selection:
  - If DATABASE_URL env var is set and starts with "postgresql://", uses psycopg2.
  - Otherwise falls back to SQLite (path provided to Database() or default).
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Generator

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.errors
    import psycopg2.pool
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False


_DEFAULT_DB = Path(__file__).parent.parent / "data" / "marketplace.db"

# Thread pool for dispatching sync DB calls from async handlers.
# Size mirrors PG_POOL_MAX to avoid thread starvation.
_db_executor = ThreadPoolExecutor(
    max_workers=int(os.environ.get("PG_POOL_MAX", "100")),
    thread_name_prefix="db-async",
)


# ---------------------------------------------------------------------------
# SQL translation helpers
# ---------------------------------------------------------------------------

def _to_pg_sql(sql: str) -> str:
    """Convert SQLite-style SQL to PostgreSQL-compatible SQL.

    - Replaces positional ``?`` placeholders with ``%s``.
    - Replaces named ``:name`` placeholders with ``%(name)s``.
    - Replaces ``BEGIN EXCLUSIVE`` with ``BEGIN`` (PG has no EXCLUSIVE mode).
    """
    # Named params first (must be before positional to avoid double-replacing)
    sql = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", sql)
    # Positional params
    sql = sql.replace("?", "%s")
    # SQLite-specific transaction syntax
    sql = re.sub(r"\bBEGIN\s+EXCLUSIVE\b", "BEGIN", sql, flags=re.IGNORECASE)
    return sql


def _to_pg_params(params):
    """Convert named-param dict from SQLite ``:name`` style to ``%(name)s`` style.

    For SQLite named params the dict keys are plain strings; psycopg2 expects
    the same plain string keys (used with ``%(name)s`` in the query), so this
    is a no-op for dicts.  For tuples/lists nothing needs to change either.
    Returns the params unchanged — kept as a hook for future needs.
    """
    return params


# ---------------------------------------------------------------------------
# PostgreSQL connection wrapper
# ---------------------------------------------------------------------------

class _PGCursorResult:
    """Wraps a psycopg2 cursor after execute() to provide fetchone/fetchall."""

    def __init__(self, cursor):
        self._cursor = cursor
        # rowcount mirrors the cursor's rowcount
        self.rowcount = cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class _PGConnWrapper:
    """Thin wrapper around a psycopg2 connection that mimics the sqlite3
    connection API used throughout this module.

    sqlite3 connections expose:
      conn.execute(sql, params)  → cursor with .fetchone() / .fetchall() / .rowcount
      conn.executescript(sql)    → executes multiple statements
    psycopg2 connections do NOT have these; only cursors do.

    This wrapper creates a single RealDictCursor and delegates calls to it,
    returning a _PGCursorResult that exposes .fetchone() / .fetchall() /
    .rowcount exactly like sqlite3's implicit cursor.
    """

    def __init__(self, pg_conn):
        self._conn = pg_conn
        self._cursor = pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Transaction control statements that sqlite3 allows via conn.execute()
    # but that psycopg2 handles via explicit connection methods.
    _TX_STMTS = {
        "begin": "_begin",
        "begin exclusive": "_begin",
        "commit": "commit",
        "rollback": "rollback",
    }

    def execute(self, sql: str, params=None):
        stripped = sql.strip().rstrip(";").lower()
        tx_method = self._TX_STMTS.get(stripped)
        if tx_method:
            getattr(self, tx_method)()
            # Return a dummy result with rowcount=0 for compatibility
            return _PGCursorResult(self._cursor)

        pg_sql = _to_pg_sql(sql)
        if params is None:
            self._cursor.execute(pg_sql)
        else:
            self._cursor.execute(pg_sql, params)
        return _PGCursorResult(self._cursor)

    def _begin(self):
        """Start an explicit transaction (no-op when autocommit is off)."""
        # psycopg2 with autocommit=False is always in a transaction;
        # calling BEGIN explicitly would be a no-op or cause an error.
        # We simply do nothing here.
        pass

    def executescript(self, sql: str) -> None:
        """Execute a multi-statement SQL script on PostgreSQL.

        psycopg2 does not support executescript().  We split on semicolons
        and execute each statement individually.  This is only used for
        schema initialisation so performance is not a concern.
        """
        # Use a plain cursor (not RealDict) for DDL statements
        with self._conn.cursor() as cur:
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass
        self._conn.close()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS services (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    endpoint TEXT NOT NULL,
    price_per_call REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    payment_method TEXT DEFAULT 'x402',
    free_tier_calls INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    category TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    key_id TEXT PRIMARY KEY,
    hashed_secret TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'buyer',
    rate_limit INTEGER DEFAULT 60,
    wallet_address TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS usage_records (
    id TEXT PRIMARY KEY,
    buyer_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    latency_ms INTEGER DEFAULT 0,
    status_code INTEGER DEFAULT 200,
    amount_usd REAL DEFAULT 0,
    payment_method TEXT DEFAULT 'x402',
    payment_tx TEXT,
    commission_rate TEXT,
    request_id TEXT,
    settlement_id TEXT
);

CREATE TABLE IF NOT EXISTS settlements (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    total_amount REAL DEFAULT 0,
    platform_fee REAL DEFAULT 0,
    net_amount REAL DEFAULT 0,
    payment_tx TEXT,
    status TEXT DEFAULT 'pending',
    notes TEXT DEFAULT '',
    updated_at TEXT,
    UNIQUE(provider_id, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_usage_buyer
    ON usage_records(buyer_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_service
    ON usage_records(service_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_services_status
    ON services(status, category);
CREATE INDEX IF NOT EXISTS idx_keys_owner
    ON api_keys(owner_id);

-- Agent Identity
CREATE TABLE IF NOT EXISTS agent_identities (
    agent_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    identity_type TEXT DEFAULT 'api_key_only',
    capabilities TEXT DEFAULT '[]',
    wallet_address TEXT,
    verified INTEGER DEFAULT 0,
    reputation_score REAL DEFAULT 0.0,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_agents_owner
    ON agent_identities(owner_id);
CREATE INDEX IF NOT EXISTS idx_agents_status
    ON agent_identities(status);

-- Reputation Records
CREATE TABLE IF NOT EXISTS reputation_records (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    overall_score REAL DEFAULT 0.0,
    latency_score REAL DEFAULT 0.0,
    reliability_score REAL DEFAULT 0.0,
    response_quality REAL DEFAULT 0.0,
    call_count INTEGER DEFAULT 0,
    period TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reputation_agent
    ON reputation_records(agent_id, period);
CREATE INDEX IF NOT EXISTS idx_reputation_service
    ON reputation_records(service_id, period);

-- Teams
CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    description TEXT DEFAULT '',
    config TEXT DEFAULT '{}',
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_teams_owner
    ON teams(owner_id);

-- Team Members
CREATE TABLE IF NOT EXISTS team_members (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role TEXT DEFAULT 'worker',
    skills TEXT DEFAULT '[]',
    joined_at TEXT NOT NULL,
    UNIQUE(team_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_members_team
    ON team_members(team_id);
CREATE INDEX IF NOT EXISTS idx_members_agent
    ON team_members(agent_id);

-- Routing Rules
CREATE TABLE IF NOT EXISTS routing_rules (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    name TEXT NOT NULL,
    keywords TEXT DEFAULT '[]',
    target_agent_id TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_routing_team
    ON routing_rules(team_id, priority);

-- Quality Gates
CREATE TABLE IF NOT EXISTS quality_gates (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    gate_type TEXT NOT NULL,
    threshold REAL NOT NULL,
    gate_order INTEGER DEFAULT 0,
    config TEXT DEFAULT '{}',
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gates_team
    ON quality_gates(team_id, gate_order);

-- Webhooks
CREATE TABLE IF NOT EXISTS webhooks (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    url TEXT NOT NULL,
    events TEXT DEFAULT '[]',
    secret TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webhooks_owner
    ON webhooks(owner_id);

-- Buyer Balances (pre-paid credit system)
CREATE TABLE IF NOT EXISTS balances (
    buyer_id TEXT PRIMARY KEY,
    balance REAL NOT NULL DEFAULT 0,
    total_deposited REAL NOT NULL DEFAULT 0,
    total_spent REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- Deposit records (IPN-confirmed only)
CREATE TABLE IF NOT EXISTS deposits (
    id TEXT PRIMARY KEY,
    buyer_id TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    payment_provider TEXT NOT NULL,
    payment_id TEXT,
    payment_status TEXT DEFAULT 'pending',
    confirmed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deposits_buyer
    ON deposits(buyer_id, payment_status);
CREATE INDEX IF NOT EXISTS idx_deposits_payment
    ON deposits(payment_id);

-- Founding Sellers (first 50 permanent badges)
CREATE TABLE IF NOT EXISTS founding_sellers (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL UNIQUE,
    sequence_number INTEGER NOT NULL UNIQUE,
    badge_tier TEXT DEFAULT 'founding',
    commission_rate REAL DEFAULT 0.08,
    awarded_at TEXT NOT NULL,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_founding_provider
    ON founding_sellers(provider_id);
CREATE INDEX IF NOT EXISTS idx_founding_sequence
    ON founding_sellers(sequence_number);

-- Email subscribers (download gate)
CREATE TABLE IF NOT EXISTS subscribers (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    source TEXT DEFAULT 'starter-kit',
    subscribed_at TEXT NOT NULL,
    confirmed INTEGER DEFAULT 0,
    drip_stage INTEGER DEFAULT 0,
    drip_next_at TEXT,
    unsubscribed INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_subscribers_email
    ON subscribers(email);
CREATE INDEX IF NOT EXISTS idx_subscribers_drip
    ON subscribers(drip_next_at, unsubscribed);

-- Agent Providers (agents registered as service providers)
CREATE TABLE IF NOT EXISTS agent_providers (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL UNIQUE,
    owner_email TEXT NOT NULL,
    wallet_address TEXT NOT NULL,
    did TEXT NOT NULL,
    declaration TEXT NOT NULL,
    status TEXT DEFAULT 'pending_review',
    reputation_score REAL DEFAULT 0.0,
    fast_track_eligible INTEGER DEFAULT 0,
    daily_tx_cap REAL DEFAULT 500.0,
    daily_tx_used REAL DEFAULT 0.0,
    daily_tx_reset_at TEXT,
    probation_ends_at TEXT,
    total_reports INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_agent_providers_agent
    ON agent_providers(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_providers_owner
    ON agent_providers(owner_email);
CREATE INDEX IF NOT EXISTS idx_agent_providers_status
    ON agent_providers(status);

-- Service Reviews (automated quality checks for new listings)
CREATE TABLE IF NOT EXISTS service_reviews (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    review_type TEXT DEFAULT 'automated',
    status TEXT DEFAULT 'pending',
    endpoint_reachable INTEGER DEFAULT 0,
    response_format_valid INTEGER DEFAULT 0,
    response_time_ms INTEGER DEFAULT 0,
    malicious_check_passed INTEGER DEFAULT 0,
    error_details TEXT DEFAULT '',
    reviewer_notes TEXT DEFAULT '',
    reviewed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_service
    ON service_reviews(service_id);
CREATE INDEX IF NOT EXISTS idx_reviews_status
    ON service_reviews(status, created_at);

-- Escrow Holds (payment escrow for Agent Provider transactions)
CREATE TABLE IF NOT EXISTS escrow_holds (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    buyer_id TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    status TEXT DEFAULT 'held',
    usage_record_id TEXT,
    held_at TEXT NOT NULL,
    release_at TEXT NOT NULL,
    released_at TEXT,
    updated_at TEXT,
    created_at TEXT NOT NULL,
    -- Dispute fields (v0.7.1)
    dispute_reason TEXT,
    dispute_category TEXT,
    dispute_timeout_at TEXT,
    resolved_at TEXT,
    resolution_outcome TEXT,
    resolution_note TEXT,
    refund_amount REAL,
    provider_payout REAL
);

CREATE INDEX IF NOT EXISTS idx_escrow_provider
    ON escrow_holds(provider_id, status);
CREATE INDEX IF NOT EXISTS idx_escrow_release
    ON escrow_holds(release_at, status);

-- Dispute Evidence (structured evidence for escrow disputes)
CREATE TABLE IF NOT EXISTS dispute_evidence (
    id TEXT PRIMARY KEY,
    hold_id TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    role TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence_urls TEXT DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dispute_evidence_hold
    ON dispute_evidence(hold_id);

-- Service Reports (abuse reporting by buyers)
CREATE TABLE IF NOT EXISTS service_reports (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    reporter_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    details TEXT DEFAULT '',
    status TEXT DEFAULT 'open',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_service
    ON service_reports(service_id);
CREATE INDEX IF NOT EXISTS idx_reports_provider
    ON service_reports(provider_id);

-- Webhook Delivery Log (persistent retry queue)
CREATE TABLE IF NOT EXISTS webhook_delivery_log (
    id TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    next_retry_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_delivery_log_status
    ON webhook_delivery_log(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_delivery_log_subscription
    ON webhook_delivery_log(subscription_id);

-- Consent Records (immutable audit trail for GDPR consent evidence)
CREATE TABLE IF NOT EXISTS consent_records (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    consent_type TEXT NOT NULL,
    consent_given_at TEXT NOT NULL,
    consent_ip TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_consent_email ON consent_records(email);

-- Owner email verification tokens for agent onboarding
CREATE TABLE IF NOT EXISTS owner_verification_tokens (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    owner_email TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    verified_at TEXT,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ovt_agent ON owner_verification_tokens(agent_id);
CREATE INDEX IF NOT EXISTS idx_ovt_token_hash ON owner_verification_tokens(token_hash);

-- Referral system
CREATE TABLE IF NOT EXISTS referrals (
    id TEXT PRIMARY KEY,
    referrer_provider_id TEXT NOT NULL,
    referred_provider_id TEXT,
    referral_code TEXT NOT NULL UNIQUE,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    activated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_provider_id);
CREATE INDEX IF NOT EXISTS idx_referrals_code ON referrals(referral_code);
CREATE INDEX IF NOT EXISTS idx_referrals_referred ON referrals(referred_provider_id);

CREATE TABLE IF NOT EXISTS referral_payouts (
    id TEXT PRIMARY KEY,
    referral_id TEXT NOT NULL,
    period TEXT NOT NULL,
    platform_revenue REAL NOT NULL DEFAULT 0,
    payout_amount REAL NOT NULL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payouts_referral ON referral_payouts(referral_id);
CREATE INDEX IF NOT EXISTS idx_payouts_period ON referral_payouts(period);
"""


class Database:
    """Database wrapper supporting both SQLite and PostgreSQL backends.

    Backend selection (evaluated once at construction):
    - If the ``DATABASE_URL`` environment variable is set and begins with
      ``postgresql://``, psycopg2 is used.
    - Otherwise SQLite is used with the given *db_path* (or the default path).
    """

    def __init__(self, db_path: str | Path | None = None):
        database_url = os.environ.get("DATABASE_URL", "")
        self._pg_url: str | None = None
        self._pg_pool = None

        if database_url.startswith("postgresql://"):
            if not _PSYCOPG2_AVAILABLE:
                raise RuntimeError(
                    "DATABASE_URL is set to a PostgreSQL URL but psycopg2 is not "
                    "installed. Run: pip install psycopg2-binary"
                )
            self._pg_url = database_url
            self.db_path = None
            minconn = int(os.environ.get("PG_POOL_MIN", "2"))
            maxconn = int(os.environ.get("PG_POOL_MAX", "100"))
            self._pg_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn, maxconn, self._pg_url,
                keepalives=1, keepalives_idle=30,
                keepalives_interval=10, keepalives_count=3,
            )
        else:
            self.db_path = Path(db_path) if db_path else _DEFAULT_DB
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_schema()

    @property
    def _is_postgres(self) -> bool:
        return self._pg_url is not None

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
        self._migrate_settlements_columns()
        self._migrate_settlements_unique()
        self._migrate_escrow_dispute_columns()
        self._migrate_usage_settlement_id()
        self._migrate_prompt_service_columns()

    def _migrate_settlements_columns(self) -> None:
        """Add notes/updated_at columns to settlements if they don't exist yet (R18-M2)."""
        with self.connect() as conn:
            try:
                conn.execute("ALTER TABLE settlements ADD COLUMN notes TEXT DEFAULT ''")
            except Exception:
                pass  # Column already exists
            try:
                conn.execute("ALTER TABLE settlements ADD COLUMN updated_at TEXT")
            except Exception:
                pass  # Column already exists

    def _migrate_settlements_unique(self) -> None:
        """Add UNIQUE constraint on settlements(provider_id, period_start, period_end) (R20-M1)."""
        with self.connect() as conn:
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_settlements_unique_period "
                    "ON settlements(provider_id, period_start, period_end)"
                )
            except Exception:
                pass  # Index already exists or constraint from CREATE TABLE

    def _migrate_usage_settlement_id(self) -> None:
        """Add settlement_id column to usage_records for audit traceability (R19-M1)."""
        with self.connect() as conn:
            try:
                conn.execute("ALTER TABLE usage_records ADD COLUMN settlement_id TEXT")
            except Exception:
                pass  # Column already exists

    def _migrate_prompt_service_columns(self) -> None:
        """Add service_type and prompt_config columns to services table (Prompt-as-API)."""
        with self.connect() as conn:
            try:
                conn.execute(
                    "ALTER TABLE services ADD COLUMN service_type TEXT DEFAULT 'external'"
                )
            except Exception:
                pass  # Column already exists
            try:
                conn.execute(
                    "ALTER TABLE services ADD COLUMN prompt_config TEXT DEFAULT '{}'"
                )
            except Exception:
                pass  # Column already exists

    def _migrate_escrow_dispute_columns(self) -> None:
        """Add dispute resolution columns to escrow_holds if they don't exist yet."""
        new_cols = [
            ("dispute_reason", "TEXT"),
            ("dispute_category", "TEXT"),
            ("dispute_timeout_at", "TEXT"),
            ("resolved_at", "TEXT"),
            ("resolution_outcome", "TEXT"),
            ("resolution_note", "TEXT"),
            ("refund_amount", "REAL"),
            ("provider_payout", "REAL"),
        ]
        with self.connect() as conn:
            for col_name, col_type in new_cols:
                try:
                    conn.execute(
                        f"ALTER TABLE escrow_holds ADD COLUMN {col_name} {col_type}"
                    )
                except Exception:
                    pass  # Column already exists

    def close_pool(self):
        """Close the PostgreSQL connection pool (no-op for SQLite)."""
        if self._pg_pool is not None:
            self._pg_pool.closeall()
            self._pg_pool = None

    def check_connection(self) -> bool:
        """Verify DB connectivity by executing SELECT 1."""
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    async def arun(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a sync Database method in the thread-pool executor.

        Usage from async route handlers::

            result = await db.arun(db.get_api_key, key_id)

        The call is dispatched to the module-level ``_db_executor``
        to avoid blocking the asyncio event loop.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _db_executor,
            functools.partial(fn, *args, **kwargs),
        )

    @contextmanager
    def connect(self):
        """Context manager that yields a connection-like object.

        For SQLite: yields a ``sqlite3.Connection`` (with Row factory).
        For PostgreSQL: yields a ``_PGConnWrapper`` that mirrors the sqlite3 API.

        Either way the caller can use::

            conn.execute(sql, params)
            conn.executescript(sql)
            row["column_name"]   # dict-like row access
        """
        if self._is_postgres:
            pg_conn = self._pg_pool.getconn()
            pg_conn.autocommit = False
            wrapper = _PGConnWrapper(pg_conn)
            try:
                yield wrapper
                pg_conn.commit()
            except Exception:
                pg_conn.rollback()
                raise
            finally:
                wrapper.close()
                self._pg_pool.putconn(pg_conn)
        else:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    # --- Service operations ---

    def insert_service(self, service: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO services
                   (id, provider_id, name, description, endpoint,
                    price_per_call, currency, payment_method, free_tier_calls,
                    status, category, tags, metadata,
                    service_type, prompt_config,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    service["id"],
                    service["provider_id"],
                    service["name"],
                    service.get("description", ""),
                    service["endpoint"],
                    float(service["price_per_call"]),
                    service.get("currency", "USDC"),
                    service.get("payment_method", "x402"),
                    service.get("free_tier_calls", 0),
                    service.get("status", "active"),
                    service.get("category", ""),
                    json.dumps(service.get("tags", [])),
                    json.dumps(service.get("metadata", {})),
                    service.get("service_type", "external"),
                    json.dumps(service.get("prompt_config", {})),
                    service["created_at"],
                    service["updated_at"],
                ),
            )

    def get_service(self, service_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM services WHERE id = ?", (service_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_service(row)

    def list_services(
        self,
        status: str = "active",
        category: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        conditions = ["status = ?"]
        params: list = [status]

        if category:
            conditions.append("category = ?")
            params.append(category)

        if query:
            conditions.append("(name LIKE ? OR description LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])

        where = " AND ".join(conditions)
        params.extend([limit, offset])

        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM services WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
            return [self._row_to_service(r) for r in rows]

    def update_service(self, service_id: str, updates: dict) -> bool:
        allowed = {"name", "description", "endpoint", "price_per_call",
                    "currency", "payment_method", "status", "category",
                    "tags", "metadata", "updated_at",
                    "service_type", "prompt_config"}
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return False

        if "tags" in filtered:
            filtered["tags"] = json.dumps(filtered["tags"])
        if "metadata" in filtered:
            filtered["metadata"] = json.dumps(filtered["metadata"])
        if "prompt_config" in filtered:
            filtered["prompt_config"] = json.dumps(filtered["prompt_config"])
        if "price_per_call" in filtered:
            filtered["price_per_call"] = float(filtered["price_per_call"])

        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [service_id]

        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE services SET {set_clause} WHERE id = ?", values
            )
            return cursor.rowcount > 0

    def delete_service(self, service_id: str) -> bool:
        return self.update_service(service_id, {"status": "removed"})

    # --- Usage operations ---

    def insert_usage(self, record: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO usage_records
                   (id, buyer_id, service_id, provider_id, timestamp,
                    latency_ms, status_code, amount_usd, payment_method,
                    payment_tx, commission_rate, request_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["buyer_id"],
                    record["service_id"],
                    record["provider_id"],
                    record["timestamp"],
                    record.get("latency_ms", 0),
                    record.get("status_code", 200),
                    float(record.get("amount_usd", 0)),
                    record.get("payment_method", "x402"),
                    record.get("payment_tx"),
                    str(record["commission_rate"]) if record.get("commission_rate") is not None else None,
                    record.get("request_id"),
                ),
            )

    def link_usage_to_settlement(
        self, settlement_id: str, provider_id: str,
        period_start: str, period_end: str,
    ) -> int:
        """Tag usage records with their settlement_id for audit traceability."""
        with self.connect() as conn:
            cur = conn.execute(
                """UPDATE usage_records SET settlement_id = ?
                   WHERE provider_id = ?
                     AND timestamp >= ? AND timestamp < ?
                     AND settlement_id IS NULL
                     AND status_code < 500""",
                (settlement_id, provider_id, period_start, period_end),
            )
            return cur.rowcount

    def get_usage_by_request_id(
        self, request_id: str, ttl_hours: int = 24,
    ) -> dict | None:
        """Look up a usage record by idempotency request_id within a TTL window.

        Only returns a match if the original request was made within the last
        ``ttl_hours`` hours (default 24h, matching Stripe's idempotency model).
        Requests older than the TTL are treated as expired — the same
        request_id may be reused after the window closes.

        Args:
            request_id: The client-generated idempotency key.
            ttl_hours: Time-to-live in hours. Set to 0 for no expiry (legacy).
        """
        with self.connect() as conn:
            if ttl_hours > 0:
                from datetime import datetime, timedelta, timezone
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
                ).isoformat()
                row = conn.execute(
                    "SELECT * FROM usage_records "
                    "WHERE request_id = ? AND timestamp >= ? LIMIT 1",
                    (request_id, cutoff),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM usage_records WHERE request_id = ? LIMIT 1",
                    (request_id,),
                ).fetchone()
            return dict(row) if row else None

    def update_usage_record(self, record_id: str, updates: dict) -> bool:
        """Update an existing usage record (e.g. after HTTP response arrives).

        Only allows updating: latency_ms, status_code, amount_usd,
        payment_method, payment_tx, commission_rate, request_id.
        """
        allowed = {
            "latency_ms", "status_code", "amount_usd",
            "payment_method", "payment_tx", "commission_rate", "request_id",
            "settlement_id",
        }
        safe = {k: v for k, v in updates.items() if k in allowed}
        if not safe:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in safe)
        values = list(safe.values()) + [record_id]
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE usage_records SET {set_clause} WHERE id = ?",
                values,
            )
            return cur.rowcount > 0

    def get_usage_stats(
        self,
        service_id: str | None = None,
        buyer_id: str | None = None,
        since: str | None = None,
    ) -> dict:
        conditions = []
        params: list = []

        if service_id:
            conditions.append("service_id = ?")
            params.append(service_id)
        if buyer_id:
            conditions.append("buyer_id = ?")
            params.append(buyer_id)

        # Always enforce a time bound to prevent unbounded full table scans.
        # Default: 30 days with no filters, 365 days max with filters.
        effective_since = since
        if not since:
            max_days = 30 if (not service_id and not buyer_id) else 365
            effective_since = (
                datetime.now(timezone.utc) - timedelta(days=max_days)
            ).isoformat()
        conditions.append("timestamp >= ?")
        params.append(effective_since)

        where = " AND ".join(conditions) if conditions else "1=1"

        with self.connect() as conn:
            row = conn.execute(
                f"""SELECT COUNT(*) as total_calls,
                           SUM(amount_usd) as total_revenue,
                           AVG(latency_ms) as avg_latency
                    FROM usage_records WHERE {where}""",
                params,
            ).fetchone()
            return {
                "total_calls": row["total_calls"] or 0,
                "total_revenue": Decimal(str(row["total_revenue"] or 0)),
                "avg_latency_ms": round(row["avg_latency"] or 0, 1),
            }

    # --- API Key operations ---

    def insert_api_key(self, key: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO api_keys
                   (key_id, hashed_secret, owner_id, role,
                    rate_limit, wallet_address, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key["key_id"],
                    key["hashed_secret"],
                    key["owner_id"],
                    key.get("role", "buyer"),
                    key.get("rate_limit", 60),
                    key.get("wallet_address"),
                    key["created_at"],
                    key.get("expires_at"),
                ),
            )

    def get_api_key(self, key_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key_id = ?", (key_id,)
            ).fetchone()
            return dict(row) if row else None

    # --- Agent Identity operations ---

    def insert_agent(self, agent: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO agent_identities
                   (agent_id, display_name, owner_id, identity_type,
                    capabilities, wallet_address, verified, reputation_score,
                    status, created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    agent["agent_id"],
                    agent["display_name"],
                    agent["owner_id"],
                    agent.get("identity_type", "api_key_only"),
                    json.dumps(agent.get("capabilities", [])),
                    agent.get("wallet_address"),
                    1 if agent.get("verified") else 0,
                    agent.get("reputation_score", 0.0),
                    agent.get("status", "active"),
                    agent["created_at"],
                    agent["updated_at"],
                    json.dumps(agent.get("metadata", {})),
                ),
            )

    def get_agent(self, agent_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_identities WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_agent(row)

    def list_agents(
        self,
        owner_id: str | None = None,
        status: str = "active",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        conditions = ["status = ?"]
        params: list = [status]
        if owner_id:
            conditions.append("owner_id = ?")
            params.append(owner_id)
        where = " AND ".join(conditions)
        params.extend([limit, offset])
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_identities WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
            return [self._row_to_agent(r) for r in rows]

    def update_agent(self, agent_id: str, updates: dict) -> bool:
        allowed = {
            "display_name", "identity_type", "capabilities",
            "wallet_address", "verified", "reputation_score",
            "status", "updated_at", "metadata",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return False
        if "capabilities" in filtered:
            filtered["capabilities"] = json.dumps(filtered["capabilities"])
        if "metadata" in filtered:
            filtered["metadata"] = json.dumps(filtered["metadata"])
        if "verified" in filtered:
            filtered["verified"] = 1 if filtered["verified"] else 0
        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [agent_id]
        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE agent_identities SET {set_clause} WHERE agent_id = ?", values
            )
            return cursor.rowcount > 0

    def delete_agent(self, agent_id: str) -> bool:
        return self.update_agent(agent_id, {"status": "deactivated"})

    def search_agents(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM agent_identities
                   WHERE status = 'active'
                     AND (display_name LIKE ? OR agent_id LIKE ?)
                   ORDER BY reputation_score DESC
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
            return [self._row_to_agent(r) for r in rows]

    # --- Owner verification token operations ---

    def insert_owner_verification(self, record: dict) -> None:
        """Insert a new owner email verification token."""
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO owner_verification_tokens
                   (id, agent_id, owner_email, token_hash, status,
                    created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["agent_id"],
                    record["owner_email"],
                    record["token_hash"],
                    record.get("status", "pending"),
                    record["created_at"],
                    record["expires_at"],
                ),
            )

    def get_owner_verification_by_token(self, token_hash: str) -> dict | None:
        """Look up an owner verification record by hashed token."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM owner_verification_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            return dict(row) if row else None

    def get_owner_verification_by_agent(self, agent_id: str) -> dict | None:
        """Get the latest owner verification record for an agent."""
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM owner_verification_tokens
                   WHERE agent_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (agent_id,),
            ).fetchone()
            return dict(row) if row else None

    def confirm_owner_verification(self, token_hash: str, verified_at: str) -> bool:
        """Mark an owner verification token as verified."""
        with self.connect() as conn:
            cursor = conn.execute(
                """UPDATE owner_verification_tokens
                   SET status = 'verified', verified_at = ?
                   WHERE token_hash = ? AND status = 'pending'""",
                (verified_at, token_hash),
            )
            return cursor.rowcount > 0

    # --- Reputation operations ---

    def insert_reputation(self, record: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO reputation_records
                   (id, agent_id, service_id, overall_score, latency_score,
                    reliability_score, response_quality, call_count, period, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["agent_id"],
                    record["service_id"],
                    record.get("overall_score", 0.0),
                    record.get("latency_score", 0.0),
                    record.get("reliability_score", 0.0),
                    record.get("response_quality", 0.0),
                    record.get("call_count", 0),
                    record["period"],
                    record["created_at"],
                ),
            )

    def get_reputation(
        self, agent_id: str, period: str = "all-time"
    ) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM reputation_records
                   WHERE agent_id = ? AND period = ?
                   ORDER BY overall_score DESC""",
                (agent_id, period),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_service_reputation(
        self, service_id: str, period: str = "all-time"
    ) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM reputation_records
                   WHERE service_id = ? AND period = ?
                   ORDER BY overall_score DESC""",
                (service_id, period),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_usage_for_reputation(
        self, provider_id: str, service_id: str | None = None,
        period_start: str | None = None, period_end: str | None = None,
    ) -> dict:
        """Aggregate usage stats for reputation calculation."""
        conditions = ["provider_id = ?"]
        params: list = [provider_id]
        if service_id:
            conditions.append("service_id = ?")
            params.append(service_id)
        if period_start:
            conditions.append("timestamp >= ?")
            params.append(period_start)
        if period_end:
            conditions.append("timestamp <= ?")
            params.append(period_end)
        where = " AND ".join(conditions)
        with self.connect() as conn:
            row = conn.execute(
                f"""SELECT COUNT(*) as total_calls,
                           AVG(latency_ms) as avg_latency,
                           SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) as error_count,
                           SUM(CASE WHEN status_code < 400 THEN 1 ELSE 0 END) as success_count
                    FROM usage_records WHERE {where}""",
                params,
            ).fetchone()
            total = row["total_calls"] or 0
            return {
                "total_calls": total,
                "avg_latency": round(row["avg_latency"] or 0, 1),
                "error_count": row["error_count"] or 0,
                "success_count": row["success_count"] or 0,
                "error_rate": round((row["error_count"] or 0) / total * 100, 2) if total > 0 else 0.0,
                "success_rate": round((row["success_count"] or 0) / total * 100, 2) if total > 0 else 0.0,
            }

    # --- Team operations ---

    def insert_team(self, team: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO teams
                   (id, name, owner_id, description, config, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    team["id"],
                    team["name"],
                    team["owner_id"],
                    team.get("description", ""),
                    json.dumps(team.get("config", {})),
                    team.get("status", "active"),
                    team["created_at"],
                    team["updated_at"],
                ),
            )

    def get_team(self, team_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM teams WHERE id = ?", (team_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["config"] = json.loads(d.get("config", "{}"))
            return d

    def list_teams(
        self, owner_id: str | None = None, limit: int = 50
    ) -> list[dict]:
        if owner_id:
            sql = "SELECT * FROM teams WHERE owner_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT ?"
            params = [owner_id, limit]
        else:
            sql = "SELECT * FROM teams WHERE status = 'active' ORDER BY created_at DESC LIMIT ?"
            params = [limit]
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["config"] = json.loads(d.get("config", "{}"))
                result.append(d)
            return result

    def update_team(self, team_id: str, updates: dict) -> bool:
        allowed = {"name", "description", "config", "status", "updated_at"}
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return False
        if "config" in filtered:
            filtered["config"] = json.dumps(filtered["config"])
        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [team_id]
        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE teams SET {set_clause} WHERE id = ?", values
            )
            return cursor.rowcount > 0

    def delete_team(self, team_id: str) -> bool:
        return self.update_team(team_id, {"status": "archived"})

    # --- Team Members ---

    def insert_team_member(self, member: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO team_members
                   (id, team_id, agent_id, role, skills, joined_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    member["id"],
                    member["team_id"],
                    member["agent_id"],
                    member.get("role", "worker"),
                    json.dumps(member.get("skills", [])),
                    member["joined_at"],
                ),
            )

    def get_team_members(self, team_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM team_members WHERE team_id = ?", (team_id,)
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["skills"] = json.loads(d.get("skills", "[]"))
                result.append(d)
            return result

    def remove_team_member(self, team_id: str, agent_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM team_members WHERE team_id = ? AND agent_id = ?",
                (team_id, agent_id),
            )
            return cursor.rowcount > 0

    # --- Routing Rules ---

    def insert_routing_rule(self, rule: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO routing_rules
                   (id, team_id, name, keywords, target_agent_id, priority, enabled, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule["id"],
                    rule["team_id"],
                    rule["name"],
                    json.dumps(rule.get("keywords", [])),
                    rule["target_agent_id"],
                    rule.get("priority", 0),
                    1 if rule.get("enabled", True) else 0,
                    rule["created_at"],
                ),
            )

    def get_routing_rules(self, team_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM routing_rules WHERE team_id = ? AND enabled = 1 ORDER BY priority DESC",
                (team_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["keywords"] = json.loads(d.get("keywords", "[]"))
                d["enabled"] = bool(d.get("enabled", 1))
                result.append(d)
            return result

    def delete_routing_rule(self, rule_id: str, team_id: str) -> bool:
        """Delete a routing rule, scoped to team_id to prevent cross-team deletion."""
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM routing_rules WHERE id = ? AND team_id = ?",
                (rule_id, team_id),
            )
            return cursor.rowcount > 0

    # --- Quality Gates ---

    def insert_quality_gate(self, gate: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO quality_gates
                   (id, team_id, gate_type, threshold, gate_order, config, enabled, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    gate["id"],
                    gate["team_id"],
                    gate["gate_type"],
                    gate["threshold"],
                    gate.get("gate_order", 0),
                    json.dumps(gate.get("config", {})),
                    1 if gate.get("enabled", True) else 0,
                    gate["created_at"],
                ),
            )

    def get_quality_gates(self, team_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM quality_gates WHERE team_id = ? AND enabled = 1 ORDER BY gate_order",
                (team_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["config"] = json.loads(d.get("config", "{}"))
                d["enabled"] = bool(d.get("enabled", 1))
                result.append(d)
            return result

    def delete_quality_gate(self, gate_id: str, team_id: str) -> bool:
        """Delete a quality gate, scoped to team_id to prevent cross-team deletion."""
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM quality_gates WHERE id = ? AND team_id = ?",
                (gate_id, team_id),
            )
            return cursor.rowcount > 0

    # --- Webhook operations ---

    def insert_webhook(self, webhook: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO webhooks
                   (id, owner_id, url, events, secret, active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    webhook["id"],
                    webhook["owner_id"],
                    webhook["url"],
                    json.dumps(webhook.get("events", [])),
                    webhook["secret"],
                    1 if webhook.get("active", True) else 0,
                    webhook["created_at"],
                ),
            )

    def get_webhook(self, webhook_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM webhooks WHERE id = ?", (webhook_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_webhook(row)

    def list_webhooks(self, owner_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM webhooks WHERE owner_id = ? ORDER BY created_at DESC",
                (owner_id,),
            ).fetchall()
            return [self._row_to_webhook(r) for r in rows]

    def delete_webhook(self, webhook_id: str, owner_id: str) -> bool:
        """Delete a webhook, scoped to owner_id to prevent cross-owner deletion."""
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM webhooks WHERE id = ? AND owner_id = ?",
                (webhook_id, owner_id),
            )
            return cursor.rowcount > 0

    def list_webhooks_for_event(self, event: str) -> list[dict]:
        """List all active webhooks subscribed to a given event."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM webhooks WHERE active = 1",
            ).fetchall()
            results = []
            for r in rows:
                wh = self._row_to_webhook(r)
                if event in wh["events"]:
                    results.append(wh)
            return results

    # --- Webhook Delivery Log operations ---

    def insert_delivery_log(self, record: dict) -> None:
        """Insert a new webhook delivery log record."""
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO webhook_delivery_log
                   (id, subscription_id, event_type, payload, status,
                    attempts, max_retries, next_retry_at, last_error,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["subscription_id"],
                    record["event_type"],
                    record.get("payload", "{}"),
                    record.get("status", "pending"),
                    record.get("attempts", 0),
                    record.get("max_retries", 3),
                    record.get("next_retry_at"),
                    record.get("last_error"),
                    record["created_at"],
                    record["updated_at"],
                ),
            )

    # Allowed columns for delivery log updates (whitelist to prevent SQL injection)
    _DELIVERY_LOG_COLUMNS = frozenset({
        "status", "response_code", "response_body", "error", "last_error",
        "retry_count", "attempts", "next_retry_at", "updated_at",
    })

    def update_delivery_log(self, delivery_id: str, updates: dict) -> None:
        """Update fields on an existing delivery log record."""
        if not updates:
            return
        now = datetime.now(timezone.utc).isoformat()
        updates.setdefault("updated_at", now)
        set_clauses = []
        params: list = []
        for key, value in updates.items():
            if key not in self._DELIVERY_LOG_COLUMNS:
                raise ValueError(f"Column '{key}' not allowed in delivery log updates")
            set_clauses.append(f"{key} = ?")
            params.append(value)
        params.append(delivery_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE webhook_delivery_log SET {', '.join(set_clauses)} WHERE id = ?",
                tuple(params),
            )

    def get_delivery_status(self, delivery_id: str) -> dict | None:
        """Get a single delivery log record by ID."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM webhook_delivery_log WHERE id = ?",
                (delivery_id,),
            ).fetchone()
            if not row:
                return None
            return dict(row)

    def list_pending_deliveries(self, before: str | None = None) -> list[dict]:
        """List pending delivery records with next_retry_at <= before."""
        with self.connect() as conn:
            if before:
                rows = conn.execute(
                    """SELECT * FROM webhook_delivery_log
                       WHERE status = 'pending' AND next_retry_at <= ?
                       ORDER BY next_retry_at ASC""",
                    (before,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM webhook_delivery_log
                       WHERE status = 'pending'
                       ORDER BY next_retry_at ASC""",
                ).fetchall()
            return [dict(r) for r in rows]

    def get_delivery_history(
        self,
        subscription_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query webhook_delivery_log with optional filters.

        Args:
            subscription_id: Filter by webhook subscription ID.
            status: Filter by status ('pending', 'delivered', 'exhausted').
            limit: Maximum records to return.

        Returns:
            List of delivery log dicts ordered by created_at DESC.
        """
        conditions: list[str] = []
        params: list = []

        if subscription_id is not None:
            conditions.append("subscription_id = ?")
            params.append(subscription_id)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM webhook_delivery_log
                    WHERE {where}
                    ORDER BY created_at DESC
                    LIMIT ?""",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Helpers ---

    @staticmethod
    def _row_to_agent(row) -> dict:
        d = dict(row)
        d["capabilities"] = json.loads(d.get("capabilities", "[]"))
        d["metadata"] = json.loads(d.get("metadata", "{}"))
        d["verified"] = bool(d.get("verified", 0))
        return d

    # --- Founding Seller operations ---

    def get_founding_seller(self, provider_id: str) -> dict | None:
        """Get founding seller record by provider ID."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM founding_sellers WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["metadata"] = json.loads(d.get("metadata", "{}"))
            return d

    def count_founding_sellers(self) -> int:
        """Count current founding sellers."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM founding_sellers",
            ).fetchone()
            return row["cnt"] if row else 0

    def award_founding_seller(self, record: dict) -> bool:
        """Award founding seller badge. Returns False if limit reached or already awarded."""
        with self.connect() as conn:
            conn.execute("BEGIN EXCLUSIVE")
            try:
                # Check if already a founding seller
                existing = conn.execute(
                    "SELECT id FROM founding_sellers WHERE provider_id = ?",
                    (record["provider_id"],),
                ).fetchone()
                if existing:
                    conn.execute("ROLLBACK")
                    return False

                # Check limit (50)
                count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM founding_sellers",
                ).fetchone()
                if (count["cnt"] or 0) >= 50:
                    conn.execute("ROLLBACK")
                    return False

                # Assign next sequence number
                max_seq = conn.execute(
                    "SELECT MAX(sequence_number) as mx FROM founding_sellers",
                ).fetchone()
                next_seq = (max_seq["mx"] or 0) + 1

                conn.execute(
                    """INSERT INTO founding_sellers
                       (id, provider_id, sequence_number, badge_tier,
                        commission_rate, awarded_at, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record["id"],
                        record["provider_id"],
                        next_seq,
                        record.get("badge_tier", "founding"),
                        record.get("commission_rate", 0.08),
                        record["awarded_at"],
                        json.dumps(record.get("metadata", {})),
                    ),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def list_founding_sellers(self) -> list[dict]:
        """List all founding sellers ordered by sequence number."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM founding_sellers ORDER BY sequence_number",
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["metadata"] = json.loads(d.get("metadata", "{}"))
                result.append(d)
            return result

    # --- Balance operations (pre-paid credit system) ---

    def get_balance(self, buyer_id: str) -> Decimal:
        """Get buyer's current balance. Returns 0 if no record."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT balance FROM balances WHERE buyer_id = ?",
                (buyer_id,),
            ).fetchone()
            return Decimal(str(row["balance"])) if row else Decimal("0")

    def deduct_balance(self, buyer_id: str, amount: Decimal) -> bool:
        """Atomically deduct from buyer balance. Returns False if insufficient."""
        with self.connect() as conn:
            conn.execute("BEGIN EXCLUSIVE")
            try:
                row = conn.execute(
                    "SELECT balance, total_spent FROM balances WHERE buyer_id = ?",
                    (buyer_id,),
                ).fetchone()
                current = Decimal(str(row["balance"])) if row else Decimal("0")
                if current < amount:
                    conn.execute("ROLLBACK")
                    return False
                new_balance = float(current - amount)
                new_spent = float(Decimal(str(row["total_spent"])) + amount) if row else float(amount)
                now = datetime.now(timezone.utc).isoformat()
                if row:
                    conn.execute(
                        "UPDATE balances SET balance = ?, total_spent = ?, updated_at = ? WHERE buyer_id = ?",
                        (new_balance, new_spent, now, buyer_id),
                    )
                else:
                    conn.execute(
                        "INSERT INTO balances (buyer_id, balance, total_deposited, total_spent, updated_at) VALUES (?, ?, 0, ?, ?)",
                        (buyer_id, new_balance, float(amount), now),
                    )
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def credit_balance(
        self,
        buyer_id: str,
        amount: Decimal,
        reason: str = "deposit",
    ) -> Decimal:
        """Add funds to buyer balance.

        *reason* distinguishes deposits from refunds in audit trails
        (e.g. ``"deposit"``, ``"refund"``, ``"escrow_refund"``).
        Returns new balance.
        """
        with self.connect() as conn:
            conn.execute("BEGIN EXCLUSIVE")
            try:
                row = conn.execute(
                    "SELECT balance, total_deposited FROM balances WHERE buyer_id = ?",
                    (buyer_id,),
                ).fetchone()
                now = datetime.now(timezone.utc).isoformat()
                is_refund = reason.startswith("refund") or reason.startswith("escrow_refund")
                if row:
                    new_bal = float(Decimal(str(row["balance"])) + amount)
                    # Only increment total_deposited for actual deposits, not refunds.
                    if is_refund:
                        new_dep = float(Decimal(str(row["total_deposited"])))
                    else:
                        new_dep = float(Decimal(str(row["total_deposited"])) + amount)
                    conn.execute(
                        "UPDATE balances SET balance = ?, total_deposited = ?, updated_at = ? WHERE buyer_id = ?",
                        (new_bal, new_dep, now, buyer_id),
                    )
                else:
                    new_bal = float(amount)
                    conn.execute(
                        "INSERT INTO balances (buyer_id, balance, total_deposited, total_spent, updated_at) VALUES (?, ?, ?, 0, ?)",
                        (buyer_id, new_bal, 0.0 if is_refund else new_bal, now),
                    )
                conn.execute("COMMIT")
                return Decimal(str(new_bal))
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def insert_deposit(self, deposit: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO deposits
                   (id, buyer_id, amount, currency, payment_provider,
                    payment_id, payment_status, confirmed_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    deposit["id"],
                    deposit["buyer_id"],
                    float(deposit["amount"]),
                    deposit.get("currency", "USDC"),
                    deposit["payment_provider"],
                    deposit.get("payment_id"),
                    deposit.get("payment_status", "pending"),
                    deposit.get("confirmed_at"),
                    deposit["created_at"],
                ),
            )

    def confirm_deposit(self, payment_id: str) -> dict | None:
        """Mark deposit as confirmed and credit buyer balance. Returns deposit or None."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM deposits WHERE payment_id = ? AND payment_status = 'pending'",
                (payment_id,),
            ).fetchone()
            if not row:
                return None
            deposit = dict(row)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE deposits SET payment_status = 'confirmed', confirmed_at = ? WHERE id = ?",
                (now, deposit["id"]),
            )
            return deposit

    # --- Subscribers (email gate) ---

    def insert_subscriber(self, subscriber: dict) -> bool:
        """Insert a new subscriber. Returns True if new, False if email exists."""
        # Determine the IntegrityError class for the active backend.
        # psycopg2.errors.UniqueViolation is a subclass of psycopg2.IntegrityError.
        _integrity_errors: tuple = (sqlite3.IntegrityError,)
        if _PSYCOPG2_AVAILABLE:
            _integrity_errors = (sqlite3.IntegrityError, psycopg2.IntegrityError)

        with self.connect() as conn:
            try:
                conn.execute(
                    """INSERT INTO subscribers
                       (id, email, source, subscribed_at, confirmed, drip_stage, drip_next_at, metadata)
                       VALUES (:id, :email, :source, :subscribed_at, :confirmed,
                               :drip_stage, :drip_next_at, :metadata)""",
                    subscriber,
                )
                return True
            except _integrity_errors:
                return False

    def get_subscriber(self, email: str) -> dict | None:
        """Get subscriber by email."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM subscribers WHERE email = ?", (email,),
            ).fetchone()
            return dict(row) if row else None

    def list_subscribers_for_drip(self, before: str) -> list[dict]:
        """List subscribers due for next drip email."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM subscribers
                   WHERE unsubscribed = 0 AND drip_next_at IS NOT NULL
                   AND drip_next_at <= ? ORDER BY drip_next_at""",
                (before,),
            ).fetchall()
            return [dict(r) for r in rows]

    def advance_drip(self, subscriber_id: str, new_stage: int, next_at: str | None) -> None:
        """Advance subscriber to next drip stage."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE subscribers SET drip_stage = ?, drip_next_at = ? WHERE id = ?",
                (new_stage, next_at, subscriber_id),
            )

    def unsubscribe(self, email: str) -> bool:
        """Mark subscriber as unsubscribed. Returns True if found."""
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE subscribers SET unsubscribed = 1 WHERE email = ?",
                (email,),
            )
            return cur.rowcount > 0

    def count_subscribers(self) -> int:
        """Count active (non-unsubscribed) subscribers."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM subscribers WHERE unsubscribed = 0",
            ).fetchone()
            return row["cnt"] if row else 0

    def insert_consent_record(self, record: dict) -> None:
        """Insert an immutable consent evidence record (GDPR compliance)."""
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO consent_records (id, email, consent_type, consent_given_at, consent_ip, source, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record["id"],
                    record["email"],
                    record["consent_type"],
                    record["consent_given_at"],
                    record.get("consent_ip", ""),
                    record.get("source", ""),
                    record.get("metadata", "{}"),
                ),
            )

    def delete_user_data(self, user_id: str) -> dict:
        """Delete all personal data for a user (GDPR right to erasure).

        Returns a summary of what was deleted.  Usage records are anonymised
        (buyer_id replaced with '[deleted]') rather than hard-deleted so that
        financial aggregates remain accurate.
        """
        deleted: dict[str, int] = {}
        with self.connect() as conn:
            # Anonymise usage records — keep for financial records but remove PII
            cur = conn.execute(
                "UPDATE usage_records SET buyer_id = '[deleted]' WHERE buyer_id = ?",
                (user_id,),
            )
            deleted["usage_records_anonymized"] = cur.rowcount

            # Delete API keys
            cur = conn.execute("DELETE FROM api_keys WHERE owner_id = ?", (user_id,))
            deleted["api_keys_deleted"] = cur.rowcount

            # Delete agent identity
            cur = conn.execute(
                "DELETE FROM agent_identities WHERE agent_id = ?", (user_id,)
            )
            deleted["identities_deleted"] = cur.rowcount

            # Delete team memberships (column is agent_id per schema)
            cur = conn.execute(
                "DELETE FROM team_members WHERE agent_id = ?", (user_id,)
            )
            deleted["team_memberships_deleted"] = cur.rowcount

            # Delete webhooks
            cur = conn.execute(
                "DELETE FROM webhooks WHERE owner_id = ?", (user_id,)
            )
            deleted["webhooks_deleted"] = cur.rowcount

            # Delete balance (column is buyer_id per schema)
            cur = conn.execute(
                "DELETE FROM balances WHERE buyer_id = ?", (user_id,)
            )
            deleted["balances_deleted"] = cur.rowcount

            # Delete PAT tokens (Personal Access Tokens — column is owner_id)
            cur = conn.execute(
                "DELETE FROM pat_tokens WHERE owner_id = ?", (user_id,)
            )
            deleted["pat_tokens_deleted"] = cur.rowcount

            # Anonymise deposits — keep for financial records
            cur = conn.execute(
                "UPDATE deposits SET buyer_id = '[deleted]' WHERE buyer_id = ?",
                (user_id,),
            )
            deleted["deposits_anonymized"] = cur.rowcount

            # Anonymise escrow holds — keep for financial records
            cur = conn.execute(
                "UPDATE escrow_holds SET buyer_id = '[deleted]' WHERE buyer_id = ?",
                (user_id,),
            )
            deleted["escrow_holds_anonymized"] = cur.rowcount

            # Anonymise provider account PII (email, password) — keep row for
            # referential integrity but strip personally-identifiable fields.
            # Use user_id suffix on email to avoid UNIQUE constraint violation
            # when multiple providers are deleted.
            deleted_email = f"[deleted-{user_id}]"
            cur = conn.execute(
                "UPDATE provider_accounts SET email = ?, "
                "hashed_password = '[deleted]', display_name = '[deleted]', "
                "company_name = '[deleted]', status = 'deleted', "
                "verify_token_hash = NULL, reset_token_hash = NULL, "
                "reset_token_expires = NULL "
                "WHERE id = ?",
                (deleted_email, user_id),
            )
            deleted["provider_accounts_anonymized"] = cur.rowcount

            # Anonymise subscriber PII (email) — use user_id suffix for UNIQUE safety
            deleted_sub_email = f"[deleted-{user_id}]"
            cur = conn.execute(
                "UPDATE subscribers SET email = ?, "
                "unsubscribed = 1 WHERE id = ?",
                (deleted_sub_email, user_id),
            )
            deleted["subscribers_anonymized"] = cur.rowcount

        return deleted

    @staticmethod
    def _row_to_service(row) -> dict:
        d = dict(row)
        d["tags"] = json.loads(d.get("tags", "[]"))
        d["metadata"] = json.loads(d.get("metadata", "{}"))
        d["prompt_config"] = json.loads(d.get("prompt_config", "{}"))
        d.setdefault("service_type", "external")
        return d

    @staticmethod
    def _row_to_webhook(row) -> dict:
        d = dict(row)
        d["events"] = json.loads(d.get("events", "[]"))
        d["active"] = bool(d.get("active", 1))
        return d

    # ── Agent Provider CRUD ──

    def insert_agent_provider(self, record: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO agent_providers
                   (id, agent_id, owner_email, wallet_address, did, declaration,
                    status, reputation_score, fast_track_eligible, daily_tx_cap,
                    daily_tx_used, daily_tx_reset_at, probation_ends_at,
                    total_reports, created_at, updated_at, metadata)
                   VALUES (:id, :agent_id, :owner_email, :wallet_address, :did,
                           :declaration, :status, :reputation_score,
                           :fast_track_eligible, :daily_tx_cap, :daily_tx_used,
                           :daily_tx_reset_at, :probation_ends_at,
                           :total_reports, :created_at, :updated_at, :metadata)""",
                record,
            )

    def get_agent_provider(self, agent_provider_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_providers WHERE id = ?",
                (agent_provider_id,),
            ).fetchone()
            return self._row_to_agent_provider(row) if row else None

    def get_agent_provider_by_agent_id(self, agent_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_providers WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            return self._row_to_agent_provider(row) if row else None

    def update_agent_provider(self, agent_provider_id: str, updates: dict) -> bool:
        allowed = {
            "status", "reputation_score", "fast_track_eligible",
            "daily_tx_cap", "daily_tx_used", "daily_tx_reset_at",
            "probation_ends_at", "total_reports", "updated_at", "metadata",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [agent_provider_id]
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE agent_providers SET {set_clause} WHERE id = ?",
                values,
            )
            return cur.rowcount > 0

    def list_agent_providers(
        self, status: str | None = None, limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        if status:
            sql = "SELECT * FROM agent_providers WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params: list = [status, limit, offset]
        else:
            sql = "SELECT * FROM agent_providers ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params = [limit, offset]
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_agent_provider(r) for r in rows]

    @staticmethod
    def _row_to_agent_provider(row) -> dict:
        d = dict(row)
        d["metadata"] = json.loads(d.get("metadata", "{}"))
        d["fast_track_eligible"] = bool(d.get("fast_track_eligible", 0))
        return d

    # ── Service Review CRUD ──

    def insert_service_review(self, record: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO service_reviews
                   (id, service_id, provider_id, review_type, status,
                    endpoint_reachable, response_format_valid, response_time_ms,
                    malicious_check_passed, error_details, reviewer_notes,
                    reviewed_at, created_at)
                   VALUES (:id, :service_id, :provider_id, :review_type, :status,
                           :endpoint_reachable, :response_format_valid,
                           :response_time_ms, :malicious_check_passed,
                           :error_details, :reviewer_notes,
                           :reviewed_at, :created_at)""",
                record,
            )

    def get_service_review(self, review_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM service_reviews WHERE id = ?", (review_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_reviews_for_service(self, service_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM service_reviews WHERE service_id = ? ORDER BY created_at DESC",
                (service_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_service_review(self, review_id: str, updates: dict) -> bool:
        allowed = {
            "status", "endpoint_reachable", "response_format_valid",
            "response_time_ms", "malicious_check_passed", "error_details",
            "reviewer_notes", "reviewed_at",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [review_id]
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE service_reviews SET {set_clause} WHERE id = ?",
                values,
            )
            return cur.rowcount > 0

    def list_pending_reviews(self, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM service_reviews WHERE status = 'pending' ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Escrow Hold CRUD ──

    def insert_escrow_hold(self, record: dict) -> None:
        record.setdefault("updated_at", record.get("created_at"))
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO escrow_holds
                   (id, provider_id, service_id, buyer_id, amount, currency,
                    status, usage_record_id, held_at, release_at, released_at,
                    updated_at, created_at)
                   VALUES (:id, :provider_id, :service_id, :buyer_id, :amount,
                           :currency, :status, :usage_record_id, :held_at,
                           :release_at, :released_at, :updated_at, :created_at)""",
                record,
            )

    def get_escrow_hold(self, hold_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM escrow_holds WHERE id = ?", (hold_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_escrow_holds(
        self,
        provider_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        sql = "SELECT * FROM escrow_holds WHERE 1=1"
        params: list = []
        if provider_id:
            sql += " AND provider_id = ?"
            params.append(provider_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def list_releasable_escrow(self, now: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM escrow_holds WHERE status = 'held' AND release_at <= ?",
                (now,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_escrow_hold(self, hold_id: str, updates: dict) -> bool:
        allowed = {
            "status", "release_at", "released_at", "updated_at",
            "dispute_reason", "dispute_category", "dispute_timeout_at",
            "resolved_at", "resolution_outcome", "resolution_note",
            "refund_amount", "provider_payout",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [hold_id]
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE escrow_holds SET {set_clause} WHERE id = ?",
                values,
            )
            return cur.rowcount > 0

    # ── Dispute Evidence CRUD ──

    def insert_dispute_evidence(self, record: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO dispute_evidence
                   (id, hold_id, submitted_by, role, category, description,
                    evidence_urls, created_at)
                   VALUES (:id, :hold_id, :submitted_by, :role, :category,
                           :description, :evidence_urls, :created_at)""",
                record,
            )

    def list_dispute_evidence(self, hold_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM dispute_evidence WHERE hold_id = ? ORDER BY created_at ASC",
                (hold_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Service Report CRUD ──

    def insert_service_report(self, record: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO service_reports
                   (id, service_id, provider_id, reporter_id, reason,
                    details, status, created_at)
                   VALUES (:id, :service_id, :provider_id, :reporter_id,
                           :reason, :details, :status, :created_at)""",
                record,
            )

    def count_reports_for_service(self, service_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM service_reports WHERE service_id = ? AND status != 'dismissed'",
                (service_id,),
            ).fetchone()
            return row["cnt"] if row else 0

    def list_reports_for_service(self, service_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM service_reports WHERE service_id = ? ORDER BY created_at DESC",
                (service_id,),
            ).fetchall()
            return [dict(r) for r in rows]
