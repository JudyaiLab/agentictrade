"""
Transaction velocity alerting — detect unusual transaction patterns.

Flags when a buyer or provider exceeds configurable thresholds:
- Transaction count per hour (default: 100)
- Transaction amount per hour (default: $10,000)

Designed as a lightweight check called from the proxy/payment flow.
Logs warnings when thresholds are exceeded.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from .db import Database

logger = logging.getLogger("acf.velocity")

# Configurable thresholds via environment variables
DEFAULT_TX_COUNT_PER_HOUR = int(os.environ.get("ACF_VELOCITY_TX_COUNT", "100"))
DEFAULT_TX_AMOUNT_PER_HOUR = Decimal(os.environ.get("ACF_VELOCITY_TX_AMOUNT", "10000"))


@dataclass(frozen=True)
class VelocityAlert:
    """Immutable record of a velocity threshold violation."""
    entity_id: str
    entity_type: str  # "buyer" or "provider"
    alert_type: str  # "tx_count" or "tx_amount"
    current_value: str
    threshold: str
    window_hours: int
    timestamp: str


def check_transaction_velocity(
    db: Database,
    buyer_id: str = "",
    provider_id: str = "",
    window_hours: int = 1,
    max_tx_count: int | None = None,
    max_tx_amount: Decimal | None = None,
) -> list[VelocityAlert]:
    """Check transaction velocity for a buyer and/or provider.

    Returns a list of VelocityAlert objects for any threshold violations.
    Empty list means no alerts.

    This function is designed to be called from the payment proxy flow
    after a transaction is recorded.
    """
    if max_tx_count is None:
        max_tx_count = DEFAULT_TX_COUNT_PER_HOUR
    if max_tx_amount is None:
        max_tx_amount = DEFAULT_TX_AMOUNT_PER_HOUR

    alerts: list[VelocityAlert] = []
    now = datetime.now(timezone.utc)

    entities = []
    if buyer_id:
        entities.append(("buyer", buyer_id, "buyer_id"))
    if provider_id:
        entities.append(("provider", provider_id, "provider_id"))

    # Compute the cutoff timestamp in Python to avoid SQLite-specific
    # datetime('now', ?) syntax, making the query portable across backends.
    cutoff = (now - timedelta(hours=window_hours)).isoformat()

    with db.connect() as conn:
        for entity_type, entity_id, column in entities:
            # Count transactions in window
            row = conn.execute(
                f"SELECT COUNT(*) AS cnt, COALESCE(SUM(amount_usd), 0) AS total "
                f"FROM usage_records "
                f"WHERE {column} = ? AND timestamp >= ?",
                (entity_id, cutoff),
            ).fetchone()

            tx_count = row["cnt"]
            tx_amount = Decimal(str(row["total"] or 0))

            if tx_count > max_tx_count:
                alert = VelocityAlert(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    alert_type="tx_count",
                    current_value=str(tx_count),
                    threshold=str(max_tx_count),
                    window_hours=window_hours,
                    timestamp=now.isoformat(),
                )
                alerts.append(alert)
                logger.warning(
                    "VELOCITY ALERT: %s %s exceeded tx count threshold "
                    "(%d > %d in %dh)",
                    entity_type, entity_id, tx_count, max_tx_count, window_hours,
                )

            if tx_amount > max_tx_amount:
                alert = VelocityAlert(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    alert_type="tx_amount",
                    current_value=str(tx_amount),
                    threshold=str(max_tx_amount),
                    window_hours=window_hours,
                    timestamp=now.isoformat(),
                )
                alerts.append(alert)
                logger.warning(
                    "VELOCITY ALERT: %s %s exceeded tx amount threshold "
                    "($%s > $%s in %dh)",
                    entity_type, entity_id, tx_amount, max_tx_amount, window_hours,
                )

    return alerts


def should_block_transaction(alerts: list[VelocityAlert]) -> bool:
    """Determine if a transaction should be blocked based on velocity alerts.

    Blocks when any threshold is exceeded by 2x or more, signalling a severe
    spike that warrants holding the transaction for review.
    """
    for alert in alerts:
        current = Decimal(alert.current_value)
        threshold = Decimal(alert.threshold)
        if current >= threshold * 2:
            return True
    return False


def check_velocity_simple(
    db: Database,
    buyer_id: str = "",
    provider_id: str = "",
) -> list[dict]:
    """Simplified velocity check returning plain dicts (for API responses).

    Uses default thresholds. Returns list of alert dicts.
    """
    alerts = check_transaction_velocity(
        db, buyer_id=buyer_id, provider_id=provider_id,
    )
    return [
        {
            "entity_id": a.entity_id,
            "entity_type": a.entity_type,
            "alert_type": a.alert_type,
            "current_value": a.current_value,
            "threshold": a.threshold,
            "window_hours": a.window_hours,
            "timestamp": a.timestamp,
        }
        for a in alerts
    ]
