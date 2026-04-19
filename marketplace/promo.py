"""
Promo code system for AgenticTrade.

Supports time-limited promo codes that grant extended zero-commission periods
to new providers on registration.

Schema:
  promo_codes       — code definitions (code, free_months, expires_at, etc.)
  promo_redemptions — tracks which providers redeemed which codes
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from .db import Database

logger = logging.getLogger("acf.promo")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

PROMO_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT PRIMARY KEY,
    description TEXT DEFAULT '',
    free_months INTEGER NOT NULL DEFAULT 1,
    expires_at TEXT NOT NULL,
    max_redemptions INTEGER DEFAULT NULL,
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS promo_redemptions (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    free_months INTEGER NOT NULL,
    redeemed_at TEXT NOT NULL,
    UNIQUE(code, provider_id)
);

CREATE INDEX IF NOT EXISTS idx_promo_redemptions_provider
    ON promo_redemptions(provider_id);
CREATE INDEX IF NOT EXISTS idx_promo_redemptions_code
    ON promo_redemptions(code);
"""


def ensure_promo_tables(db: Database) -> None:
    """Create promo tables if they don't exist."""
    with db.connect() as conn:
        conn.executescript(PROMO_SCHEMA_SQL)


def seed_default_promos(db: Database) -> None:
    """Seed the PRODUCTHUNT promo code if it doesn't already exist."""
    ensure_promo_tables(db)
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT code FROM promo_codes WHERE code = 'PRODUCTHUNT'"
        ).fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO promo_codes
                   (code, description, free_months, expires_at, max_redemptions, active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "PRODUCTHUNT",
                    "Product Hunt launch promo - 3 months zero commission",
                    3,
                    "2026-06-06T23:59:59+00:00",
                    None,  # unlimited redemptions
                    1,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            logger.info("Seeded PRODUCTHUNT promo code")


# ---------------------------------------------------------------------------
# Validation & Redemption
# ---------------------------------------------------------------------------

def validate_promo_code(db: Database, code: str) -> tuple[bool, str, Optional[dict]]:
    """Validate a promo code.

    Returns (is_valid, error_message, promo_record).
    - is_valid: True if code can be redeemed
    - error_message: empty string if valid, human-readable error otherwise
    - promo_record: the promo_codes row dict if found, None otherwise
    """
    code = code.strip().upper()
    if not code:
        return False, "No promo code provided", None

    ensure_promo_tables(db)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM promo_codes WHERE code = ?",
            (code,),
        ).fetchone()

    if not row:
        return False, "Invalid promo code", None

    promo = dict(row)

    if not promo["active"]:
        return False, "This promo code is no longer active", None

    # Check expiration
    try:
        expires_at = datetime.fromisoformat(promo["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            return False, "This promo code has expired", None
    except (ValueError, TypeError):
        return False, "Invalid promo code expiration", None

    # Check max redemptions if set
    if promo["max_redemptions"] is not None:
        with db.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM promo_redemptions WHERE code = ?",
                (code,),
            ).fetchone()["cnt"]
        if count >= promo["max_redemptions"]:
            return False, "This promo code has reached its redemption limit", None

    return True, "", promo


def redeem_promo_code(
    db: Database, provider_id: str, code: str
) -> tuple[bool, str, int]:
    """Redeem a promo code for a provider.

    Returns (success, message, free_months).
    - success: True if redemption was successful
    - message: human-readable result message
    - free_months: number of free commission months granted (0 on failure)
    """
    code = code.strip().upper()
    is_valid, error, promo = validate_promo_code(db, code)
    if not is_valid or promo is None:
        return False, error, 0

    ensure_promo_tables(db)

    # Check if provider already redeemed this code
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT id FROM promo_redemptions WHERE code = ? AND provider_id = ?",
            (code, provider_id),
        ).fetchone()

    if existing:
        return False, "You have already used this promo code", 0

    # Record redemption
    redemption_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    free_months = promo["free_months"]

    with db.connect() as conn:
        conn.execute(
            """INSERT INTO promo_redemptions (id, code, provider_id, free_months, redeemed_at)
               VALUES (?, ?, ?, ?, ?)""",
            (redemption_id, code, provider_id, free_months, now),
        )

    logger.info(
        "Promo code %s redeemed by provider %s — %d free months",
        code, provider_id, free_months,
    )
    return True, f"Promo code applied! You get {free_months} months of 0% commission.", free_months


def get_provider_promo_free_months(db: Database, provider_id: str) -> int:
    """Get the total promo-granted free commission months for a provider.

    If a provider redeemed multiple promos, returns the maximum free_months
    value (they don't stack — the best promo wins).

    Returns 0 if no promo was redeemed.
    """
    ensure_promo_tables(db)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT MAX(free_months) as max_free FROM promo_redemptions WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()

    if row and row["max_free"] is not None:
        return int(row["max_free"])
    return 0
