"""Tests for drip_sender CLI — processes scheduled drip emails."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from cli.drip_sender import DRIP_SCHEDULE, process_drip
from marketplace.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "drip_test.db")


@pytest.fixture
def db_path(db, tmp_path):
    return tmp_path / "drip_test.db"


def _add_subscriber(db: Database, email: str, stage: int = 0, due: bool = True) -> str:
    """Helper to insert a subscriber with drip scheduling."""
    import uuid

    now = datetime.now(timezone.utc)
    next_at = (now - timedelta(hours=1)).isoformat() if due else (now + timedelta(days=5)).isoformat()
    sub = {
        "id": str(uuid.uuid4()),
        "email": email,
        "source": "test",
        "subscribed_at": now.isoformat(),
        "confirmed": 0,
        "drip_stage": stage,
        "drip_next_at": next_at,
        "metadata": "{}",
    }
    db.insert_subscriber(sub)
    return sub["id"]


class TestDripProcessing:
    def test_no_due_subscribers_returns_zero(self, db, db_path):
        sent = process_drip(db_path, dry_run=True)
        assert sent == 0

    def test_dry_run_does_not_send(self, db, db_path):
        _add_subscriber(db, "dry@example.com", stage=0, due=True)
        sent = process_drip(db_path, dry_run=True)
        assert sent == 1
        # Stage should NOT advance in dry run
        sub = db.get_subscriber("dry@example.com")
        assert sub["drip_stage"] == 0

    def test_not_due_subscriber_skipped(self, db, db_path):
        _add_subscriber(db, "future@example.com", stage=0, due=False)
        sent = process_drip(db_path, dry_run=True)
        assert sent == 0

    @patch("cli.drip_sender._send_drip_email", return_value=True)
    def test_successful_send_advances_stage(self, mock_send, db, db_path):
        _add_subscriber(db, "advance@example.com", stage=0, due=True)
        sent = process_drip(db_path)
        assert sent == 1
        mock_send.assert_called_once_with(
            "advance@example.com",
            DRIP_SCHEDULE[0][1],
            DRIP_SCHEDULE[0][2],
        )
        sub = db.get_subscriber("advance@example.com")
        assert sub["drip_stage"] == 1
        assert sub["drip_next_at"] is not None

    @patch("cli.drip_sender._send_drip_email", return_value=True)
    def test_last_stage_clears_next_at(self, mock_send, db, db_path):
        last_stage = len(DRIP_SCHEDULE) - 1
        _add_subscriber(db, "last@example.com", stage=last_stage, due=True)
        sent = process_drip(db_path)
        assert sent == 1
        sub = db.get_subscriber("last@example.com")
        assert sub["drip_stage"] == last_stage + 1
        assert sub["drip_next_at"] is None

    @patch("cli.drip_sender._send_drip_email", return_value=False)
    def test_failed_send_does_not_advance(self, mock_send, db, db_path):
        _add_subscriber(db, "fail@example.com", stage=0, due=True)
        sent = process_drip(db_path)
        assert sent == 0
        sub = db.get_subscriber("fail@example.com")
        assert sub["drip_stage"] == 0

    def test_past_all_stages_clears(self, db, db_path):
        _add_subscriber(db, "done@example.com", stage=len(DRIP_SCHEDULE), due=True)
        sent = process_drip(db_path, dry_run=True)
        # Past all stages — should just clear, not count as sent
        assert sent == 0
        sub = db.get_subscriber("done@example.com")
        assert sub["drip_next_at"] is None

    @patch("cli.drip_sender._send_drip_email", return_value=True)
    def test_multiple_subscribers_processed(self, mock_send, db, db_path):
        for i in range(3):
            _add_subscriber(db, f"multi{i}@example.com", stage=0, due=True)
        sent = process_drip(db_path)
        assert sent == 3
        assert mock_send.call_count == 3
