"""
Service abuse reporting with auto-delist for Agent Commerce Framework.

Buyers can file reports against services for malicious behavior,
inaccurate results, unavailability, or other issues. When a service
accumulates 3 or more non-dismissed reports, it is automatically
delisted and its provider suspended.

Report statuses: open | dismissed
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from .audit import AuditLogger
from .db import Database

logger = logging.getLogger("report")


class ReportError(Exception):
    """Report processing errors."""


class ReportManager:
    """Service reporting and auto-delist system."""

    DELIST_THRESHOLD = 5
    VALID_REASONS = frozenset({"malicious", "inaccurate", "unavailable", "other"})

    def __init__(self, db: Database):
        self.db = db

    # Minimum usage records a reporter must have to file a report (anti-sybil)
    MIN_USAGE_TO_REPORT = 1

    def file_report(
        self,
        service_id: str,
        reporter_id: str,
        reason: str,
        details: str = "",
    ) -> dict:
        """File a report against a service.

        Validates the reason, prevents duplicate reports from the same
        reporter for the same service, requires the reporter to have at
        least one usage record (anti-sybil), and auto-delists if the
        threshold is reached (3 non-dismissed reports).

        Returns the created report record.
        """
        if not service_id or not reporter_id:
            raise ReportError("service_id and reporter_id are required")

        if reason not in self.VALID_REASONS:
            raise ReportError(
                f"Invalid reason '{reason}'. "
                f"Must be one of: {sorted(self.VALID_REASONS)}"
            )

        # Look up the service to get provider_id
        service = self.db.get_service(service_id)
        if service is None:
            raise ReportError(f"Service not found: {service_id}")

        # Anti-sybil: reporter must have used this service at least once
        with self.db.connect() as conn:
            usage_count = conn.execute(
                "SELECT COUNT(*) FROM usage_records "
                "WHERE buyer_id = ? AND service_id = ?",
                (reporter_id, service_id),
            ).fetchone()[0]
        if usage_count < self.MIN_USAGE_TO_REPORT:
            raise ReportError(
                "You must have used this service at least once before filing a report"
            )

        provider_id = service["provider_id"]

        # Prevent duplicate report from same reporter on same service
        existing_reports = self.db.list_reports_for_service(service_id)
        for report in existing_reports:
            if (
                report["reporter_id"] == reporter_id
                and report["status"] != "dismissed"
            ):
                raise ReportError(
                    f"Reporter {reporter_id} has already filed "
                    f"an active report for service {service_id}"
                )

        now = datetime.now(timezone.utc).isoformat()
        report_id = str(uuid.uuid4())

        record = {
            "id": report_id,
            "service_id": service_id,
            "provider_id": provider_id,
            "reporter_id": reporter_id,
            "reason": reason,
            "details": details,
            "status": "open",
            "created_at": now,
        }
        self.db.insert_service_report(record)

        logger.info(
            "Report filed: %s against service %s by %s, reason=%s",
            report_id, service_id, reporter_id, reason,
        )

        # Check auto-delist threshold
        self._auto_delist_if_needed(service_id, provider_id)

        return record

    def _auto_delist_if_needed(
        self, service_id: str, provider_id: str,
    ) -> bool:
        """Check non-dismissed report count and auto-delist if >= threshold.

        Delists the service (status='removed') and suspends the provider.
        Returns True if delist was triggered.
        """
        report_count = self.db.count_reports_for_service(service_id)
        if report_count < self.DELIST_THRESHOLD:
            return False

        # Delist the service
        now = datetime.now(timezone.utc).isoformat()
        self.db.update_service(service_id, {
            "status": "removed",
            "updated_at": now,
        })
        logger.warning(
            "Service %s auto-delisted: %d reports (threshold=%d)",
            service_id, report_count, self.DELIST_THRESHOLD,
        )

        # Suspend the provider
        self.db.update_agent_provider(provider_id, {
            "status": "suspended",
            "updated_at": now,
        })
        logger.warning(
            "Provider %s suspended due to auto-delist of service %s",
            provider_id, service_id,
        )

        self._notify_owner(provider_id, service_id, "auto_delist")
        return True

    def _notify_owner(
        self, provider_id: str, service_id: str, reason: str,
    ) -> None:
        """Log the delist/suspension event to the audit trail.

        Could be extended to send email or webhook notifications.
        """
        try:
            audit = AuditLogger(self.db.db_path)
            audit.log_event(
                event_type="service_deleted",
                actor="system:report_manager",
                target=service_id,
                details=(
                    f"Auto-delisted service {service_id} "
                    f"(provider={provider_id}, reason={reason})"
                ),
            )
        except Exception as exc:
            logger.error(
                "Failed to write audit log for delist of %s: %s",
                service_id, exc,
            )

    def get_reports(self, service_id: str) -> list[dict]:
        """Get all reports for a service, ordered by created_at descending."""
        return self.db.list_reports_for_service(service_id)

    def dismiss_report(self, report_id: str) -> bool:
        """Admin action: dismiss an invalid report.

        Updates the report status to 'dismissed'. Returns True if
        the report was found and updated, False otherwise.
        """
        # The DB layer doesn't have update_service_report, so we use
        # a direct query through the connection.
        with self.db.connect() as conn:
            cur = conn.execute(
                "UPDATE service_reports SET status = ? WHERE id = ? AND status = 'open'",
                ("dismissed", report_id),
            )
            dismissed = cur.rowcount > 0

        if dismissed:
            logger.info("Report dismissed: %s", report_id)
        else:
            logger.warning(
                "Report dismiss failed (not found or not open): %s", report_id,
            )
        return dismissed
