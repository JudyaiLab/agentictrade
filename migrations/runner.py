"""
Lightweight SQL migration runner for Agent Commerce Framework.

Tracks applied migrations in a `schema_migrations` table and applies
pending .sql files from migrations/versions/ in order.

Works with both SQLite and PostgreSQL via the existing Database class.

Usage:
    python -m migrations.runner status   # show applied/pending migrations
    python -m migrations.runner migrate  # apply all pending migrations
    python -m migrations.runner create <name>  # create a new migration file
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSIONS_DIR = Path(__file__).parent / "versions"
_MIGRATION_PATTERN = re.compile(r"^(\d{4})_.+\.sql$")


def _ensure_tracking_table(conn) -> None:
    """Create the sql_migrations tracking table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sql_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
    """)


def _applied_versions(conn) -> set[str]:
    """Return set of already-applied migration version numbers."""
    rows = conn.execute(
        "SELECT version FROM sql_migrations ORDER BY version"
    ).fetchall()
    return {row["version"] if hasattr(row, "keys") else row[0] for row in rows}


def _discover_migrations() -> list[tuple[str, str, Path]]:
    """Discover migration files, return sorted list of (version, name, path)."""
    migrations = []
    if not VERSIONS_DIR.exists():
        return migrations
    for f in sorted(VERSIONS_DIR.iterdir()):
        m = _MIGRATION_PATTERN.match(f.name)
        if m:
            version = m.group(1)
            name = f.stem
            migrations.append((version, name, f))
    return migrations


def status(db) -> list[dict]:
    """Return migration status: list of dicts with version, name, applied."""
    with db.connect() as conn:
        _ensure_tracking_table(conn)
        applied = _applied_versions(conn)

    result = []
    for version, name, _path in _discover_migrations():
        result.append({
            "version": version,
            "name": name,
            "applied": version in applied,
        })
    return result


def migrate(db, target: str | None = None) -> list[str]:
    """Apply all pending migrations (or up to target version).

    Returns list of applied migration names.
    """
    applied_names = []
    with db.connect() as conn:
        _ensure_tracking_table(conn)
        applied = _applied_versions(conn)

        for version, name, path in _discover_migrations():
            if version in applied:
                continue
            if target and version > target:
                break

            sql = path.read_text(encoding="utf-8")
            # Split on semicolons for multi-statement migrations
            for statement in sql.split(";"):
                statement = statement.strip()
                if statement:
                    conn.execute(statement)

            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO sql_migrations (version, name, applied_at) "
                "VALUES (?, ?, ?)",
                (version, name, now_iso),
            )
            applied_names.append(name)

    return applied_names


def create(name: str) -> Path:
    """Create a new empty migration file with the next version number."""
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    existing = _discover_migrations()
    if existing:
        last_version = int(existing[-1][0])
    else:
        last_version = 0

    new_version = f"{last_version + 1:04d}"
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    filename = f"{new_version}_{slug}.sql"
    filepath = VERSIONS_DIR / filename

    filepath.write_text(
        f"-- Migration {new_version}: {name}\n"
        f"-- Created: {datetime.now(timezone.utc).isoformat()}\n\n",
        encoding="utf-8",
    )
    return filepath


def main() -> None:
    """CLI entrypoint."""
    if len(sys.argv) < 2:
        print("Usage: python -m migrations.runner <status|migrate|create> [args]")
        sys.exit(1)

    command = sys.argv[1]

    if command == "create":
        if len(sys.argv) < 3:
            print("Usage: python -m migrations.runner create <migration_name>")
            sys.exit(1)
        path = create(sys.argv[2])
        print(f"Created: {path}")
        return

    # status and migrate need a DB connection
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from marketplace.db import Database

    db_path = os.environ.get("ACF_DB_PATH", "data/marketplace.db")
    db = Database(db_path)

    if command == "status":
        rows = status(db)
        if not rows:
            print("No migrations found.")
            return
        for r in rows:
            mark = "✅" if r["applied"] else "⏳"
            print(f"  {mark} {r['version']} {r['name']}")

    elif command == "migrate":
        applied = migrate(db)
        if applied:
            print(f"Applied {len(applied)} migration(s):")
            for name in applied:
                print(f"  ✅ {name}")
        else:
            print("No pending migrations.")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
