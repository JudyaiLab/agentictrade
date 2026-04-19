"""
Rate limiter for API endpoints.

Two implementations:
- RateLimiter: In-memory token bucket (fast, single-instance)
- DatabaseRateLimiter: DB-backed sliding window (works across instances)

Use RATE_LIMIT_BACKEND env var to select: "memory" (default) or "database".
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


class RateLimiterProtocol(Protocol):
    """Common interface for rate limiters."""

    def allow(self, key: str) -> bool: ...
    def reset(self) -> None: ...


@dataclass
class _Bucket:
    tokens: float = 0.0
    last_refill: float = field(default_factory=time.monotonic)


class RateLimiter:
    """In-memory token-bucket rate limiter keyed by client identifier.

    Fast and suitable for single-instance deployments.
    State is lost on restart and not shared across instances.
    """

    def __init__(
        self,
        rate: int = 60,
        per: float = 60.0,
        burst: int | None = None,
    ):
        self.rate = rate
        self.per = per
        self.burst = burst if burst is not None else rate
        self._buckets: dict[str, _Bucket] = defaultdict(
            lambda: _Bucket(tokens=self.burst, last_refill=time.monotonic())
        )

    def allow(self, key: str) -> bool:
        """Return True if the request is allowed, False if rate limited."""
        bucket = self._buckets[key]
        now = time.monotonic()
        elapsed = now - bucket.last_refill
        bucket.last_refill = now

        # Refill tokens
        bucket.tokens = min(
            self.burst,
            bucket.tokens + elapsed * (self.rate / self.per),
        )

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True
        return False

    def get_limit_info(self, key: str) -> tuple[int, int]:
        """Return ``(remaining, reset_seconds)`` for the given key.

        ``remaining`` is the number of requests the client can still make
        before hitting the limit.  ``reset_seconds`` is the number of
        seconds until the bucket is fully replenished.
        """
        bucket = self._buckets[key]
        remaining = max(0, int(bucket.tokens))
        # Time until the bucket is fully refilled from its current level
        if remaining >= self.burst:
            reset_seconds = 0
        else:
            tokens_needed = self.burst - bucket.tokens
            refill_rate = self.rate / self.per  # tokens per second
            reset_seconds = int(tokens_needed / refill_rate) if refill_rate > 0 else 0
            reset_seconds = max(1, min(reset_seconds, int(self.per)))
        return remaining, reset_seconds

    def reset(self) -> None:
        """Clear all buckets (useful for test isolation)."""
        self._buckets.clear()

    def cleanup(self, max_age: float = 3600.0) -> int:
        """Remove stale buckets older than max_age seconds. Returns count removed."""
        now = time.monotonic()
        stale = [
            k for k, v in self._buckets.items()
            if (now - v.last_refill) > max_age
        ]
        for k in stale:
            del self._buckets[k]
        return len(stale)


class DatabaseRateLimiter:
    """Database-backed sliding window rate limiter.

    Stores rate limit counters in the database, enabling shared state
    across multiple application instances for horizontal scaling.

    Uses a fixed-window counter approach: each key gets a row with
    a window start time and request count. When the window expires,
    the counter resets.
    """

    def __init__(self, db, rate: int = 60, window_seconds: float = 60.0):
        self.db = db
        self.rate = rate
        self.window_seconds = window_seconds
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS rate_limit_windows ("
                "key TEXT PRIMARY KEY, "
                "window_start TEXT NOT NULL, "
                "request_count INTEGER NOT NULL DEFAULT 0"
                ")"
            )

    def allow(self, key: str, rate_override: int | None = None) -> bool:
        """Check and increment the rate limit counter.

        Uses Python-side window expiry checking to avoid SQLite-specific
        ``julianday()`` date math, making this backend-agnostic.

        *rate_override*: when provided, uses this rate limit for the check
        instead of ``self.rate``.  This avoids mutating shared state and
        is thread-safe for per-key rate limits.

        Algorithm:
          1. Read the current row for the key (if any).
          2. Check in Python whether the window has expired.
          3. If expired (or new key), reset window_start and count=1.
          4. Otherwise increment count.
          5. Return True if the final count is within the limit.
        """
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        max_rate = rate_override if rate_override is not None else self.rate
        window_delta = timedelta(seconds=self.window_seconds)

        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT window_start, request_count "
                "FROM rate_limit_windows WHERE key = ?",
                (key,),
            ).fetchone()

            if row is None:
                # Brand-new key — insert with count 1.
                # Use INSERT ... ON CONFLICT to handle concurrent inserts
                # (portable across SQLite ≥3.24 and PostgreSQL).
                conn.execute(
                    "INSERT INTO rate_limit_windows (key, window_start, request_count) "
                    "VALUES (?, ?, 1) "
                    "ON CONFLICT (key) DO UPDATE SET request_count = request_count + 1",
                    (key, now_iso),
                )
                updated = conn.execute(
                    "SELECT request_count FROM rate_limit_windows WHERE key = ?",
                    (key,),
                ).fetchone()
                return (updated["request_count"] if updated else 1) <= max_rate

            # Parse stored window start and check expiry in Python.
            window_start_str = row["window_start"]
            # Handle both offset-aware and offset-naive stored timestamps.
            try:
                window_start = datetime.fromisoformat(window_start_str)
                if window_start.tzinfo is None:
                    window_start = window_start.replace(tzinfo=timezone.utc)
            except ValueError:
                # Fallback: treat as expired so we reset.
                window_start = now - window_delta - timedelta(seconds=1)

            window_expired = (now - window_start) >= window_delta

            if window_expired:
                # Reset the window.
                conn.execute(
                    "UPDATE rate_limit_windows "
                    "SET window_start = ?, request_count = 1 "
                    "WHERE key = ?",
                    (now_iso, key),
                )
                new_count = 1
            else:
                new_count = row["request_count"] + 1
                conn.execute(
                    "UPDATE rate_limit_windows "
                    "SET request_count = ? "
                    "WHERE key = ?",
                    (new_count, key),
                )

            return new_count <= max_rate

    def reset(self) -> None:
        """Clear all rate limit windows."""
        with self.db.connect() as conn:
            conn.execute("DELETE FROM rate_limit_windows")

    def cleanup(self, max_age: float = 3600.0) -> int:
        """Remove stale windows older than max_age seconds."""
        from datetime import timedelta
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=max_age)
        ).isoformat()
        with self.db.connect() as conn:
            cur = conn.execute(
                "DELETE FROM rate_limit_windows WHERE window_start < ?",
                (cutoff,),
            )
            return cur.rowcount


def create_rate_limiter(
    backend: str = "memory",
    db=None,
    rate: int = 60,
    per: float = 60.0,
    burst: int | None = None,
) -> RateLimiter | DatabaseRateLimiter:
    """Factory function to create a rate limiter.

    Args:
        backend: "memory" (default) or "database"
        db: Database instance (required for "database" backend)
        rate: Maximum requests per window
        per: Window size in seconds
        burst: Burst capacity (memory backend only)
    """
    if backend == "database":
        if db is None:
            raise ValueError("Database instance required for database rate limiter")
        return DatabaseRateLimiter(db, rate=rate, window_seconds=per)
    return RateLimiter(rate=rate, per=per, burst=burst)
