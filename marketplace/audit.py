"""
Audit logging for security-relevant events in Agent Commerce Framework.
Lightweight audit trail for authentication, key management, admin actions,
and settlement events. Works with both SQLite and PostgreSQL backends by
delegating all database access to the main Database class.

Tamper detection: each entry stores a SHA-256 hash that chains from the
previous entry's hash, forming an append-only hash chain. Use
``verify_chain()`` to validate integrity.
"""
from __future__ import annotations

import hashlib
import json as _json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Union

_DEFAULT_DB = Path(__file__).parent.parent / "data" / "marketplace.db"

_AUDIT_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS audit_log ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "event_type TEXT NOT NULL, "
    "actor TEXT NOT NULL, "
    "target TEXT NOT NULL DEFAULT '', "
    "details TEXT NOT NULL DEFAULT '', "
    "ip_address TEXT NOT NULL DEFAULT '', "
    "timestamp TEXT NOT NULL, "
    "prev_hash TEXT NOT NULL DEFAULT ''"
    ")"
)
_AUDIT_IDX1 = "CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log(event_type)"
_AUDIT_IDX2 = "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor)"
_AUDIT_IDX3 = "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)"

# ---------------------------------------------------------------------------
# Hash chain helpers
# ---------------------------------------------------------------------------

_GENESIS_HASH = "0" * 64  # sentinel for the very first entry


def _compute_entry_hash(
    prev_hash: str,
    event_type: str,
    actor: str,
    target: str,
    details: str,
    ip_address: str,
    timestamp: str,
) -> str:
    """Compute SHA-256 hash for an audit entry chained from *prev_hash*."""
    payload = _json.dumps(
        [prev_hash, event_type, actor, target, details, ip_address, timestamp],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


VALID_EVENT_TYPES = frozenset({
    "auth_failure",
    "auth_success",
    "key_created",
    "key_revoked",
    "admin_action",
    "service_registered",
    "service_deleted",
    "settlement_executed",
    "config_changed",
    "escrow_created",
    "escrow_released",
    "escrow_disputed",
    "escrow_resolved",
    "balance_deposited",
    "balance_deducted",
    "service_called",
    "probe_executed",
})


# ---------------------------------------------------------------------------
# Event Sourcing: state reconstruction from audit events
# ---------------------------------------------------------------------------

# Maps event_type → how it modifies entity state.
# Each handler receives (current_state: dict, event: dict) → new_state: dict.
_EVENT_HANDLERS: dict[str, object] = {}


def _event_handler(event_type: str):
    """Decorator to register an event replay handler."""
    def decorator(fn):
        _EVENT_HANDLERS[event_type] = fn
        return fn
    return decorator


@_event_handler("service_registered")
def _apply_service_registered(state: dict, event: dict) -> dict:
    details = _json.loads(event.get("details", "{}")) if event.get("details") else {}
    return {**state, "status": "active", "registered_at": event["timestamp"], **details}


@_event_handler("service_deleted")
def _apply_service_deleted(state: dict, event: dict) -> dict:
    return {**state, "status": "deleted", "deleted_at": event["timestamp"]}


@_event_handler("escrow_created")
def _apply_escrow_created(state: dict, event: dict) -> dict:
    details = _json.loads(event.get("details", "{}")) if event.get("details") else {}
    amount = details.get("amount", 0)
    return {
        **state,
        "escrow_status": "held",
        "escrow_amount": state.get("escrow_amount", 0) + amount,
        "last_escrow_at": event["timestamp"],
    }


@_event_handler("escrow_released")
def _apply_escrow_released(state: dict, event: dict) -> dict:
    details = _json.loads(event.get("details", "{}")) if event.get("details") else {}
    amount = details.get("amount", 0)
    return {
        **state,
        "escrow_status": "released",
        "escrow_amount": max(0, state.get("escrow_amount", 0) - amount),
    }


@_event_handler("settlement_executed")
def _apply_settlement(state: dict, event: dict) -> dict:
    details = _json.loads(event.get("details", "{}")) if event.get("details") else {}
    total = state.get("total_settled", 0) + details.get("amount", 0)
    count = state.get("settlement_count", 0) + 1
    return {**state, "total_settled": total, "settlement_count": count,
            "last_settlement_at": event["timestamp"]}


@_event_handler("balance_deposited")
def _apply_deposit(state: dict, event: dict) -> dict:
    details = _json.loads(event.get("details", "{}")) if event.get("details") else {}
    amount = details.get("amount", 0)
    return {**state, "balance": state.get("balance", 0) + amount}


@_event_handler("balance_deducted")
def _apply_deduction(state: dict, event: dict) -> dict:
    details = _json.loads(event.get("details", "{}")) if event.get("details") else {}
    amount = details.get("amount", 0)
    return {**state, "balance": state.get("balance", 0) - amount}


class AuditLogger:
    """Database-agnostic audit logger for security events.

    Accepts either a ``Database`` instance (preferred — works with SQLite and
    PostgreSQL) or a file path string/Path (legacy, for standalone / test use).

    Auto-creates the audit_log table on initialization.
    """

    def __init__(self, db_or_path=None):
        """
        Args:
            db_or_path: One of:
                - A ``Database`` instance (uses db.connect() — preferred).
                - A file-path string or ``pathlib.Path`` (creates its own
                  SQLite connection, backward-compatible for tests).
                - ``None`` → uses the default SQLite path.
        """
        # Detect whether we received a Database object or a path-like value.
        # Avoid importing Database at module level to prevent circular imports;
        # check duck-typing instead (Database exposes a .connect() method).
        if db_or_path is None or isinstance(db_or_path, (str, Path)):
            # Legacy / standalone mode: manage our own SQLite connection.
            self.db_path = Path(db_or_path) if db_or_path else _DEFAULT_DB
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = None  # will use self._connect()
        else:
            # Database-instance mode: delegate all connections to db.connect().
            self._db = db_or_path
            self.db_path = None  # not used in this mode
        self._init_schema()

    def _init_schema(self) -> None:
        with self._get_conn() as conn:
            conn.execute(_AUDIT_TABLE_SQL)
            conn.execute(_AUDIT_IDX1)
            conn.execute(_AUDIT_IDX2)
            conn.execute(_AUDIT_IDX3)

    @contextmanager
    def _get_conn(self):
        """Yield a connection from either the Database instance or SQLite."""
        if self._db is not None:
            with self._db.connect() as conn:
                yield conn
        else:
            with self._connect() as conn:
                yield conn

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Open a direct SQLite connection (legacy / standalone mode only)."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def log_event(
        self,
        event_type: str,
        actor: str,
        target: str = "",
        details: str = "",
        ip_address: str = "",
    ) -> int:
        """Write an audit entry. Returns the row id of the inserted record.

        Each entry is chained via SHA-256 to the previous entry's hash,
        enabling tamper detection with ``verify_chain()``.

        Raises ValueError if event_type is not in the allowed set.
        """
        if event_type not in VALID_EVENT_TYPES:
            raise ValueError(
                f"Invalid event_type '{event_type}'. "
                f"Must be one of: {sorted(VALID_EVENT_TYPES)}"
            )
        if not actor:
            raise ValueError("actor is required")

        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            # Serialize hash chain writes with EXCLUSIVE lock to prevent
            # concurrent SELECT+INSERT from forking the chain (TOCTOU).
            conn.execute("BEGIN EXCLUSIVE")
            try:
                last_row = conn.execute(
                    "SELECT prev_hash, event_type, actor, target, details, "
                    "ip_address, timestamp FROM audit_log ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if last_row is None:
                    prev_hash = _GENESIS_HASH
                else:
                    prev_hash = _compute_entry_hash(
                        last_row["prev_hash"],
                        last_row["event_type"],
                        last_row["actor"],
                        last_row["target"],
                        last_row["details"],
                        last_row["ip_address"],
                        last_row["timestamp"],
                    )

                cursor = conn.execute(
                    """INSERT INTO audit_log
                       (event_type, actor, target, details, ip_address, timestamp, prev_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (event_type, actor, target, details, ip_address, now, prev_hash),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return cursor.lastrowid  # type: ignore[return-value]

    def get_events(
        self,
        event_type: str | None = None,
        actor: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Query audit events with optional filters.

        Parameters
        ----------
        since : str | None
            ISO-8601 lower bound for timestamp (inclusive).
        until : str | None
            ISO-8601 upper bound for timestamp (inclusive).

        Returns list of event dicts ordered by timestamp descending.
        """
        conditions: list[str] = []
        params: list = []

        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)
        if actor is not None:
            conditions.append("actor = ?")
            params.append(actor)
        if since is not None:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            conditions.append("timestamp <= ?")
            params.append(until)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM audit_log WHERE {where} "
                f"ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_recent(self, hours: int = 24) -> list[dict]:
        """Get audit events from the last N hours."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE timestamp >= ? "
                "ORDER BY timestamp DESC",
                (cutoff,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_summary(self, hours: int = 24) -> dict[str, int]:
        """Get event counts grouped by event_type for the last N hours."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT event_type, COUNT(*) as count "
                "FROM audit_log WHERE timestamp >= ? "
                "GROUP BY event_type ORDER BY count DESC",
                (cutoff,),
            ).fetchall()
            return {row["event_type"]: row["count"] for row in rows}

    # ------------------------------------------------------------------
    # Event Sourcing: state reconstruction
    # ------------------------------------------------------------------

    def replay_events(
        self,
        target: str,
        event_types: list[str] | None = None,
        until: str | None = None,
    ) -> list[dict]:
        """Retrieve ordered events for an entity, suitable for state replay.

        Args:
            target: The entity identifier (service_id, provider_id, etc.)
            event_types: Optional filter for specific event types.
            until: Optional ISO-8601 upper bound (replay up to this point).

        Returns:
            Events in chronological order (oldest first).
        """
        conditions = ["target = ?"]
        params: list = [target]

        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            conditions.append(f"event_type IN ({placeholders})")
            params.extend(event_types)

        if until:
            conditions.append("timestamp <= ?")
            params.append(until)

        where = " AND ".join(conditions)

        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM audit_log WHERE {where} ORDER BY id ASC",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def reconstruct_state(
        self,
        target: str,
        event_types: list[str] | None = None,
        until: str | None = None,
    ) -> dict:
        """Reconstruct entity state by replaying its event history.

        This is the core event-sourcing primitive: instead of reading mutable
        state from a table, we derive state by applying each event in sequence.

        Args:
            target: Entity identifier to reconstruct.
            event_types: Optional event type filter.
            until: Optional point-in-time for historical reconstruction.

        Returns:
            Reconstructed state dict. Contains ``_event_count`` and
            ``_last_event_at`` metadata fields.
        """
        events = self.replay_events(target, event_types, until)
        state: dict = {"_entity": target, "_event_count": 0}

        for event in events:
            handler = _EVENT_HANDLERS.get(event["event_type"])
            if handler is not None:
                state = handler(state, event)
            state["_event_count"] += 1
            state["_last_event_at"] = event["timestamp"]

        return state

    # ------------------------------------------------------------------
    # GDPR data minimization (R23-M1)
    # ------------------------------------------------------------------

    def anonymize_old_entries(self, retention_days: int = 365) -> int:
        """Replace ip_address with '[retained]' for entries older than *retention_days*.

        This supports GDPR data-minimization requirements by removing IP
        addresses from audit entries that have exceeded the retention period.

        .. note::

            Anonymization is applied **post-chain-verification**.  The hash
            chain includes the original ``ip_address`` in its computation, so
            calling this method will cause ``verify_chain()`` to report
            mismatches for any entry whose IP was anonymized (or any entry
            that follows one).  Verify chain integrity *before* running
            anonymization if tamper detection is required.

        Args:
            retention_days: Number of days to retain IP addresses.
                Entries older than this threshold are anonymized. Defaults to 365.

        Returns:
            The number of rows that were anonymized.
        """
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()

        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE audit_log SET ip_address = '[retained]' "
                "WHERE timestamp < ? AND ip_address != '[retained]'",
                (cutoff,),
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # Tamper detection
    # ------------------------------------------------------------------

    def verify_chain(self) -> tuple[bool, list[dict]]:
        """Validate the integrity of the entire audit hash chain.

        Returns ``(valid, errors)`` where *errors* is a list of dicts
        describing each broken link (empty when *valid* is True).
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, event_type, actor, target, details, ip_address, "
                "timestamp, prev_hash FROM audit_log ORDER BY id ASC"
            ).fetchall()

        errors: list[dict] = []
        expected_prev = _GENESIS_HASH

        for row in rows:
            stored_prev = row["prev_hash"]
            if stored_prev != expected_prev:
                errors.append({
                    "id": row["id"],
                    "expected_prev_hash": expected_prev,
                    "stored_prev_hash": stored_prev,
                })

            # Compute the hash of this row for the next iteration
            expected_prev = _compute_entry_hash(
                stored_prev,
                row["event_type"],
                row["actor"],
                row["target"],
                row["details"],
                row["ip_address"],
                row["timestamp"],
            )

        return (len(errors) == 0, errors)
