"""
Escrow payment hold system for Agent Provider transactions.

Holds buyer payments for 7 days before releasing to providers.
Supports structured dispute evidence, tiered dispute timeouts,
provider counter-responses, and admin arbitration.

Statuses: held -> released | refunded | disputed
Dispute outcomes: refund_buyer | release_to_provider | partial_refund
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from .db import Database

logger = logging.getLogger("escrow")

DISPUTE_CATEGORIES = frozenset({
    "service_not_delivered",
    "quality_issue",
    "unauthorized_charge",
    "wrong_output",
    "timeout_or_error",
    "other",
})

RESOLUTION_OUTCOMES = frozenset({
    "refund_buyer",
    "release_to_provider",
    "partial_refund",
})


_MAX_EVIDENCE_URLS = 10
_MAX_EVIDENCE_URL_LEN = 2048


def _validate_evidence_urls(urls: list[str] | None) -> list[str]:
    """Validate and sanitize evidence URLs.

    Rules: https:// only, max 10 URLs, max 2048 chars each.
    Returns cleaned list.
    """
    if not urls:
        return []
    if len(urls) > _MAX_EVIDENCE_URLS:
        raise EscrowError(
            f"Too many evidence URLs ({len(urls)}). Maximum is {_MAX_EVIDENCE_URLS}."
        )
    cleaned = []
    for url in urls:
        if not isinstance(url, str):
            raise EscrowError("Evidence URLs must be strings")
        url = url.strip()
        if len(url) > _MAX_EVIDENCE_URL_LEN:
            raise EscrowError(
                f"Evidence URL exceeds {_MAX_EVIDENCE_URL_LEN} characters"
            )
        if not url.startswith("https://"):
            raise EscrowError(
                f"Evidence URL must use https:// scheme, got: {url[:50]}"
            )
        cleaned.append(url)
    return cleaned


class EscrowError(Exception):
    """Escrow processing errors."""


class EscrowManager:
    """Tiered escrow hold for Agent Provider payments.

    Hold periods scale with transaction amount:
    - Under $1:   1 day  (micropayments)
    - $1–$100:    3 days (standard)
    - Over $100:  7 days (high-value)

    Dispute timeout also scales with amount:
    - Under $1:   24 hours
    - $1–$100:    72 hours (3 days)
    - Over $100:  168 hours (7 days)
    """

    # Tiered hold periods (amount thresholds in USD)
    HOLD_TIERS = [
        (1.0, 1),     # < $1 → 1 day
        (100.0, 3),   # < $100 → 3 days
        (float("inf"), 7),  # >= $100 → 7 days
    ]

    # Tiered dispute timeouts (amount → hours before auto-resolve)
    DISPUTE_TIMEOUT_TIERS = [
        (1.0, 24),      # < $1 → 24h
        (100.0, 72),    # < $100 → 72h (3 days)
        (float("inf"), 168),  # >= $100 → 168h (7 days)
    ]

    DISPUTE_TIMEOUT_HOURS = 72  # Default fallback

    def __init__(self, db: Database):
        self.db = db

    def _hold_days_for_amount(self, amount: float) -> int:
        """Return the hold period in days based on transaction amount."""
        for threshold, days in self.HOLD_TIERS:
            if amount < threshold:
                return days
        return 7  # fallback

    def _dispute_timeout_hours_for_amount(self, amount: float) -> int:
        """Return the dispute timeout in hours based on transaction amount.

        Higher-value transactions get longer dispute windows:
        <$1 = 24h, $1-$100 = 72h, $100+ = 168h (7 days).
        """
        for threshold, hours in self.DISPUTE_TIMEOUT_TIERS:
            if amount < threshold:
                return hours
        return self.DISPUTE_TIMEOUT_HOURS  # fallback

    def create_hold(
        self,
        provider_id: str,
        service_id: str,
        buyer_id: str,
        amount: float | Decimal,
        usage_record_id: str,
        currency: str = "USDC",
    ) -> dict:
        """Create an escrow hold with tiered release timing.

        Hold period: <$1 = 1 day, <$100 = 3 days, $100+ = 7 days.
        Validates that the provider is a registered agent provider and
        that the amount is positive. Returns the created hold record.
        """
        if not provider_id or not service_id or not buyer_id:
            raise EscrowError("provider_id, service_id, and buyer_id are required")

        amount_dec = Decimal(str(amount))
        if amount_dec <= 0:
            raise EscrowError("amount must be positive")

        if not self.is_agent_provider(provider_id):
            raise EscrowError(
                f"Provider {provider_id} is not a registered agent provider"
            )

        now = datetime.now(timezone.utc)
        hold_days = self._hold_days_for_amount(float(amount_dec))
        release_at = now + timedelta(days=hold_days)
        hold_id = str(uuid.uuid4())

        record = {
            "id": hold_id,
            "provider_id": provider_id,
            "service_id": service_id,
            "buyer_id": buyer_id,
            "amount": float(amount_dec),
            "currency": currency,
            "status": "held",
            "usage_record_id": usage_record_id,
            "held_at": now.isoformat(),
            "release_at": release_at.isoformat(),
            "released_at": None,
            "created_at": now.isoformat(),
        }
        self.db.insert_escrow_hold(record)

        logger.info(
            "Escrow hold created: %s for provider %s, amount=%s %s, release_at=%s",
            hold_id, provider_id, amount_dec, currency, release_at.isoformat(),
        )
        return record

    def release_hold(self, hold_id: str) -> dict:
        """Mark a held escrow as 'released' and set released_at.

        Only holds with status='held' can be released.  Uses an atomic
        UPDATE ... WHERE status='held' to prevent TOCTOU race conditions
        when multiple processes attempt to release the same hold.
        Returns the updated hold record.
        """
        hold = self._get_or_raise(hold_id)

        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            cur = conn.execute(
                """UPDATE escrow_holds
                      SET status = 'released',
                          released_at = ?,
                          updated_at = ?
                    WHERE id = ? AND status = 'held'""",
                (now, now, hold_id),
            )
        if cur.rowcount == 0:
            raise EscrowError(
                f"Cannot release hold {hold_id}: status changed concurrently "
                f"(was '{hold['status']}')"
            )

        logger.info("Escrow hold released: %s", hold_id)
        return {**hold, "status": "released", "released_at": now}

    def refund_hold(self, hold_id: str, reason: str = "") -> dict:
        """Mark a held or disputed escrow as 'refunded'.

        Returns the updated hold record.
        """
        hold = self._get_or_raise(hold_id)

        if hold["status"] not in ("held", "disputed"):
            raise EscrowError(
                f"Cannot refund hold {hold_id}: current status is '{hold['status']}'"
            )

        now = datetime.now(timezone.utc).isoformat()
        self.db.update_escrow_hold(hold_id, {
            "status": "refunded",
            "released_at": now,
        })

        logger.info(
            "Escrow hold refunded: %s, reason=%s", hold_id, reason or "(none)",
        )
        return {**hold, "status": "refunded", "released_at": now}

    def dispute_hold(
        self,
        hold_id: str,
        reason: str = "",
        category: str = "other",
        evidence_urls: list[str] | None = None,
        submitted_by: str = "",
    ) -> dict:
        """Mark a held escrow as 'disputed' with structured evidence.

        Only holds with status='held' can be disputed.
        Dispute timeout scales with transaction amount:
        <$1 = 24h, $1-$100 = 72h, $100+ = 168h.

        Args:
            hold_id: Escrow hold ID.
            reason: Free-text description of the dispute.
            category: One of DISPUTE_CATEGORIES.
            evidence_urls: Optional list of URLs supporting the claim.
            submitted_by: ID of the buyer submitting the dispute.

        Returns the updated hold record.
        """
        hold = self._get_or_raise(hold_id)
        evidence_urls = _validate_evidence_urls(evidence_urls)

        if hold["status"] != "held":
            raise EscrowError(
                f"Cannot dispute hold {hold_id}: current status is '{hold['status']}'"
            )

        if category and category not in DISPUTE_CATEGORIES:
            raise EscrowError(
                f"Invalid dispute category '{category}'. "
                f"Must be one of: {', '.join(sorted(DISPUTE_CATEGORIES))}"
            )

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        timeout_hours = self._dispute_timeout_hours_for_amount(hold["amount"])
        timeout_at = (now + timedelta(hours=timeout_hours)).isoformat()

        self.db.update_escrow_hold(hold_id, {
            "status": "disputed",
            "updated_at": now_iso,
            "dispute_reason": reason,
            "dispute_category": category or "other",
            "dispute_timeout_at": timeout_at,
        })

        # Store structured evidence if provided
        if reason or evidence_urls:
            evidence_record = {
                "id": str(uuid.uuid4()),
                "hold_id": hold_id,
                "submitted_by": submitted_by or hold.get("buyer_id", ""),
                "role": "buyer",
                "category": category or "other",
                "description": reason,
                "evidence_urls": json.dumps(evidence_urls or []),
                "created_at": now_iso,
            }
            self.db.insert_dispute_evidence(evidence_record)

        logger.info(
            "Escrow hold disputed: %s, category=%s, timeout=%sh",
            hold_id, category, timeout_hours,
        )
        return {
            **hold,
            "status": "disputed",
            "updated_at": now_iso,
            "dispute_reason": reason,
            "dispute_category": category or "other",
            "dispute_timeout_at": timeout_at,
        }

    def respond_to_dispute(
        self,
        hold_id: str,
        responder_id: str,
        description: str,
        evidence_urls: list[str] | None = None,
    ) -> dict:
        """Allow the provider to submit a counter-response to a dispute.

        Only disputed holds can receive responses. The provider's evidence
        is stored alongside the buyer's for admin review.
        """
        hold = self._get_or_raise(hold_id)
        evidence_urls = _validate_evidence_urls(evidence_urls)

        if hold["status"] != "disputed":
            raise EscrowError(
                f"Cannot respond to hold {hold_id}: status is '{hold['status']}', "
                "expected 'disputed'"
            )

        if not description:
            raise EscrowError("Response description is required")

        now_iso = datetime.now(timezone.utc).isoformat()

        evidence_record = {
            "id": str(uuid.uuid4()),
            "hold_id": hold_id,
            "submitted_by": responder_id,
            "role": "provider",
            "category": hold.get("dispute_category", "other"),
            "description": description,
            "evidence_urls": json.dumps(evidence_urls or []),
            "created_at": now_iso,
        }
        self.db.insert_dispute_evidence(evidence_record)

        self.db.update_escrow_hold(hold_id, {"updated_at": now_iso})

        logger.info("Provider %s responded to dispute on hold %s", responder_id, hold_id)
        return {**hold, "updated_at": now_iso}

    def resolve_dispute(
        self,
        hold_id: str,
        outcome: str,
        note: str = "",
        refund_amount: float | None = None,
    ) -> dict:
        """Admin resolves a disputed escrow hold.

        Args:
            hold_id: The disputed escrow hold ID.
            outcome: One of 'refund_buyer', 'release_to_provider', 'partial_refund'.
            note: Admin note explaining the resolution.
            refund_amount: Required for partial_refund — the amount refunded to the buyer.
                           Must be > 0 and <= original hold amount.

        Returns the updated hold record.
        """
        hold = self._get_or_raise(hold_id)

        if hold["status"] != "disputed":
            raise EscrowError(
                f"Cannot resolve hold {hold_id}: status is '{hold['status']}', "
                "expected 'disputed'"
            )

        if outcome not in RESOLUTION_OUTCOMES:
            raise EscrowError(
                f"Invalid resolution outcome '{outcome}'. "
                f"Must be one of: {', '.join(sorted(RESOLUTION_OUTCOMES))}"
            )

        # Validate refund_amount for partial_refund
        if outcome == "partial_refund":
            if refund_amount is None:
                raise EscrowError(
                    "refund_amount is required for partial_refund outcome"
                )
            if refund_amount <= 0:
                raise EscrowError("refund_amount must be positive")
            if refund_amount >= hold["amount"]:
                raise EscrowError(
                    f"refund_amount ({refund_amount}) must be less than "
                    f"hold amount ({hold['amount']}). Use refund_buyer for full refunds."
                )

        now_iso = datetime.now(timezone.utc).isoformat()

        if outcome == "refund_buyer":
            new_status = "refunded"
        elif outcome == "release_to_provider":
            new_status = "released"
        else:  # partial_refund
            new_status = "refunded"

        update_fields = {
            "status": new_status,
            "resolved_at": now_iso,
            "resolution_outcome": outcome,
            "resolution_note": note,
            "released_at": now_iso,
            "updated_at": now_iso,
        }
        if outcome == "partial_refund" and refund_amount is not None:
            from decimal import Decimal
            update_fields["refund_amount"] = refund_amount
            provider_payout = Decimal(str(hold["amount"])) - Decimal(str(refund_amount))
            update_fields["provider_payout"] = float(str(provider_payout))

        self.db.update_escrow_hold(hold_id, update_fields)

        logger.info(
            "Dispute resolved: hold %s → %s (refund_amount=%s, note=%s)",
            hold_id, outcome, refund_amount, note or "no note",
        )
        result = {
            **hold,
            "status": new_status,
            "resolved_at": now_iso,
            "resolution_outcome": outcome,
            "resolution_note": note,
            "released_at": now_iso,
        }
        if outcome == "partial_refund" and refund_amount is not None:
            from decimal import Decimal
            result["refund_amount"] = refund_amount
            result["provider_payout"] = float(str(
                Decimal(str(hold["amount"])) - Decimal(str(refund_amount))
            ))
        return result

    def get_dispute_evidence(self, hold_id: str) -> list[dict]:
        """Retrieve all evidence submissions for a disputed hold."""
        evidence_list = self.db.list_dispute_evidence(hold_id)
        for ev in evidence_list:
            if isinstance(ev.get("evidence_urls"), str):
                try:
                    ev["evidence_urls"] = json.loads(ev["evidence_urls"])
                except (json.JSONDecodeError, TypeError):
                    ev["evidence_urls"] = []
        return evidence_list

    def process_releasable(self) -> list[dict]:
        """Find and release all holds past their release_at time.

        Also auto-releases disputed holds that have exceeded the 72h
        dispute timeout without admin action.

        Intended for hourly cron execution. Returns a list of
        all holds that were released during this run.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        releasable = self.db.list_releasable_escrow(now_iso)

        released: list[dict] = []
        for hold in releasable:
            try:
                updated = self.release_hold(hold["id"])
                released.append(updated)
            except EscrowError as exc:
                logger.warning(
                    "Skipping hold %s during batch release: %s",
                    hold["id"], exc,
                )

        # Auto-resolve expired disputes (tiered timeout)
        disputed = self.db.list_escrow_holds(status="disputed")
        for hold in disputed:
            # Use dispute_timeout_at if available (new system), else fall back
            timeout_at_str = hold.get("dispute_timeout_at")
            if timeout_at_str:
                try:
                    timeout_at = datetime.fromisoformat(timeout_at_str)
                    expired = now > timeout_at
                except (ValueError, TypeError):
                    expired = False
            else:
                # Legacy fallback: use flat 72h from updated_at
                updated_at = hold.get("updated_at") or hold.get("created_at", "")
                try:
                    dispute_time = datetime.fromisoformat(updated_at)
                    timeout_hours = self._dispute_timeout_hours_for_amount(
                        hold.get("amount", 0),
                    )
                    expired = dispute_time < (now - timedelta(hours=timeout_hours))
                except (ValueError, TypeError):
                    expired = False

            if expired:
                # Atomic force-release: dispute expired without admin action.
                # Single UPDATE with WHERE status='disputed' prevents race conditions.
                with self.db.connect() as conn:
                    cur = conn.execute(
                        """UPDATE escrow_holds
                              SET status = 'released',
                                  released_at = ?,
                                  updated_at = ?,
                                  resolution_outcome = 'auto_released',
                                  resolution_note = 'Dispute timeout expired without admin action',
                                  resolved_at = ?
                            WHERE id = ? AND status = 'disputed'""",
                        (now_iso, now_iso, now_iso, hold["id"]),
                    )
                if cur.rowcount > 0:
                    updated_hold = {
                        **hold,
                        "status": "released",
                        "released_at": now_iso,
                        "updated_at": now_iso,
                        "resolution_outcome": "auto_released",
                        "resolution_note": "Dispute timeout expired without admin action",
                        "resolved_at": now_iso,
                    }
                    released.append(updated_hold)
                    logger.info(
                        "Dispute auto-resolved (timeout expired): hold %s released",
                        hold["id"],
                    )
                else:
                    logger.warning(
                        "Failed to auto-resolve dispute %s: status changed concurrently",
                        hold["id"],
                    )

        if released:
            logger.info(
                "Batch release complete: %d holds released", len(released),
            )
        return released

    def get_provider_escrow_summary(self, provider_id: str) -> dict:
        """Return aggregate escrow summary for a provider.

        Returns dict with total_held, total_released, total_refunded,
        and pending_count.
        """
        all_holds = self.db.list_escrow_holds(provider_id=provider_id)

        total_held = Decimal("0")
        total_released = Decimal("0")
        total_refunded = Decimal("0")
        pending_count = 0

        for hold in all_holds:
            amount = Decimal(str(hold["amount"]))
            status = hold["status"]
            if status == "held":
                total_held += amount
                pending_count += 1
            elif status == "released":
                total_released += amount
            elif status == "refunded":
                total_refunded += amount
            elif status == "disputed":
                total_held += amount
                pending_count += 1

        return {
            "provider_id": provider_id,
            "total_held": float(total_held),
            "total_released": float(total_released),
            "total_refunded": float(total_refunded),
            "pending_count": pending_count,
        }

    def is_agent_provider(self, provider_id: str) -> bool:
        """Check if provider_id belongs to a registered agent provider."""
        provider = self.db.get_agent_provider(provider_id)
        return provider is not None

    def _get_or_raise(self, hold_id: str) -> dict:
        """Fetch a hold by ID or raise EscrowError if not found."""
        hold = self.db.get_escrow_hold(hold_id)
        if hold is None:
            raise EscrowError(f"Escrow hold not found: {hold_id}")
        return hold
