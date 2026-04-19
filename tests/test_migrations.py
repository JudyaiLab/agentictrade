"""Tests for the migration runner."""
import pytest
from pathlib import Path
from marketplace.db import Database
from migrations.runner import status, migrate, create, _discover_migrations


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test_migrations.db"))


class TestMigrationRunner:
    """Test the lightweight migration system."""

    def test_status_empty_db(self, db):
        """Status should show pending migrations on fresh DB."""
        result = status(db)
        assert len(result) >= 1
        assert result[0]["version"] == "0001"
        assert result[0]["applied"] is False

    def test_migrate_applies_initial(self, db):
        """Migrate should apply the initial schema migration."""
        applied = migrate(db)
        assert len(applied) >= 1
        assert "0001_initial_schema" in applied

        # Verify tracking table records it
        result = status(db)
        assert result[0]["applied"] is True

    def test_migrate_idempotent(self, db):
        """Running migrate twice should not reapply migrations."""
        first = migrate(db)
        second = migrate(db)
        assert len(first) >= 1
        assert len(second) == 0

    def test_migrate_creates_tables(self, db):
        """After migration, all 20 tables should exist."""
        migrate(db)
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            table_names = {r["name"] for r in rows}

        expected = {
            "services", "api_keys", "usage_records", "settlements",
            "agent_identities", "reputation_records", "teams",
            "team_members", "routing_rules", "quality_gates",
            "webhooks", "balances", "deposits", "founding_sellers",
            "subscribers", "agent_providers", "service_reviews",
            "escrow_holds", "dispute_evidence", "service_reports",
        }
        assert expected.issubset(table_names)

    def test_create_migration(self, tmp_path, monkeypatch):
        """Create should generate a new migration file."""
        versions_dir = tmp_path / "versions"
        versions_dir.mkdir()
        import migrations.runner as runner
        monkeypatch.setattr(runner, "VERSIONS_DIR", versions_dir)

        path = create("add user preferences")
        assert path.exists()
        assert path.name.startswith("0001_")
        assert "add_user_preferences" in path.name
        assert path.suffix == ".sql"

    def test_create_increments_version(self, tmp_path, monkeypatch):
        """Create should increment version from the last existing migration."""
        versions_dir = tmp_path / "versions"
        versions_dir.mkdir()
        (versions_dir / "0001_initial.sql").write_text("-- init\n")
        import migrations.runner as runner
        monkeypatch.setattr(runner, "VERSIONS_DIR", versions_dir)

        path = create("second migration")
        assert path.name.startswith("0002_")

    def test_discover_migrations(self):
        """Should discover the initial migration file."""
        migrations = _discover_migrations()
        assert len(migrations) >= 1
        assert migrations[0][0] == "0001"

    def test_migrate_with_target(self, db):
        """Migrate with target version should stop at that version."""
        applied = migrate(db, target="0001")
        assert len(applied) >= 1
        # Should not apply anything beyond 0001
        result = status(db)
        for r in result:
            if r["version"] > "0001":
                assert r["applied"] is False
