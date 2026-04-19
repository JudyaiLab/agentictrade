"""
Provider Drip Email Scheduler — automated email sequences for provider lifecycle.

Drip types:
  1. Welcome (day 0) — sent immediately after provider registration
  2. Onboarding (day 1) — tips for listing first API
  3. First sale celebration — triggered when first transaction recorded
  4. Weekly digest — summary of calls + revenue for active providers

All emails tracked in SQLite to prevent duplicates. Uses Resend API for delivery.
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .db import Database

logger = logging.getLogger("drip_email")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@agentictrade.io")
FROM_NAME = os.environ.get("FROM_NAME", "AgenticTrade")

# ---------------------------------------------------------------------------
# Drip email type constants
# ---------------------------------------------------------------------------

DRIP_WELCOME = "welcome"
DRIP_ONBOARDING = "onboarding"
DRIP_FIRST_SALE = "first_sale"
DRIP_WEEKLY_DIGEST = "weekly_digest"

ALL_DRIP_TYPES = frozenset({DRIP_WELCOME, DRIP_ONBOARDING, DRIP_FIRST_SALE, DRIP_WEEKLY_DIGEST})

# Scheduled drips: (delay_days, drip_type)
SCHEDULED_DRIPS = [
    (0, DRIP_WELCOME),
    (1, DRIP_ONBOARDING),
]


@dataclass(frozen=True)
class DripEmailRecord:
    """Immutable record of a sent drip email."""
    id: str
    provider_id: str
    email: str
    drip_type: str
    scheduled_at: str
    sent_at: Optional[str]
    status: str  # "pending", "sent", "failed"


# ---------------------------------------------------------------------------
# Template loader
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates",
    "email",
)


class TemplateNotFoundError(Exception):
    """Raised when a drip email template file is missing."""


def _load_template(drip_type: str, **kwargs: str) -> str:
    """Load HTML template from file and substitute placeholders.

    Raises ``TemplateNotFoundError`` if the template file does not exist,
    preventing blank emails from being sent silently.
    """
    template_path = os.path.join(_TEMPLATES_DIR, f"{drip_type}.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()
        for key, value in kwargs.items():
            html = html.replace(f"{{{{{key}}}}}", value)
        return html
    except FileNotFoundError:
        logger.error("Template not found: %s — email will NOT be sent", template_path)
        raise TemplateNotFoundError(f"Template missing: {template_path}")


def validate_templates() -> list[str]:
    """Check that all expected drip email templates exist.

    Call at application startup to catch missing templates early.
    Returns a list of missing template names (empty if all present).
    """
    missing: list[str] = []
    for drip_type in ALL_DRIP_TYPES:
        template_path = os.path.join(_TEMPLATES_DIR, f"{drip_type}.html")
        if not os.path.isfile(template_path):
            logger.error("Missing drip template at startup: %s", template_path)
            missing.append(drip_type)
    return missing


_DRIP_SUBJECTS: dict[str, dict[str, str]] = {
    DRIP_WELCOME: {
        "en": "Welcome to AgenticTrade — Your Provider Account is Ready",
        "zh-tw": "歡迎加入 AgenticTrade — 你的供應商帳號已啟用",
        "ko": "AgenticTrade에 오신 것을 환영합니다 — 제공자 계정이 준비되었습니다",
        "ja": "AgenticTrade へようこそ — プロバイダーアカウントの準備が完了しました",
        "fr": "Bienvenue sur AgenticTrade — Votre compte fournisseur est prêt",
        "de": "Willkommen bei AgenticTrade — Ihr Provider-Konto ist bereit",
        "ru": "Добро пожаловать в AgenticTrade — Ваш аккаунт провайдера готов",
        "es": "Bienvenido a AgenticTrade — Tu cuenta de proveedor está lista",
        "pt": "Bem-vindo ao AgenticTrade — Sua conta de provedor está pronta",
    },
    DRIP_ONBOARDING: {
        "en": "List Your First API on AgenticTrade (5-Minute Guide)",
        "zh-tw": "在 AgenticTrade 上架你的第一個 API（5 分鐘指南）",
        "ko": "AgenticTrade에 첫 번째 API 등록하기 (5분 가이드)",
        "ja": "AgenticTrade で最初の API を登録する（5分ガイド）",
        "fr": "Publiez votre première API sur AgenticTrade (guide de 5 min)",
        "de": "Listen Sie Ihre erste API auf AgenticTrade (5-Minuten-Anleitung)",
        "ru": "Разместите ваш первый API на AgenticTrade (5-минутный гайд)",
        "es": "Publica tu primera API en AgenticTrade (guía de 5 minutos)",
        "pt": "Publique sua primeira API no AgenticTrade (guia de 5 minutos)",
    },
    DRIP_FIRST_SALE: {
        "en": "Congratulations — You Made Your First Sale on AgenticTrade!",
        "zh-tw": "恭喜 — 你在 AgenticTrade 上完成了第一筆交易！",
        "ko": "축하합니다 — AgenticTrade에서 첫 번째 판매를 달성했습니다!",
        "ja": "おめでとうございます — AgenticTrade で最初の売上を達成しました！",
        "fr": "Félicitations — Vous avez réalisé votre première vente sur AgenticTrade !",
        "de": "Herzlichen Glückwunsch — Sie haben Ihren ersten Verkauf auf AgenticTrade erzielt!",
        "ru": "Поздравляем — Вы совершили первую продажу на AgenticTrade!",
        "es": "¡Felicidades — Realizaste tu primera venta en AgenticTrade!",
        "pt": "Parabéns — Você fez sua primeira venda no AgenticTrade!",
    },
    DRIP_WEEKLY_DIGEST: {
        "en": "Your Weekly AgenticTrade Summary — {period}",
        "zh-tw": "AgenticTrade 每週摘要 — {period}",
        "ko": "AgenticTrade 주간 요약 — {period}",
        "ja": "AgenticTrade 週間サマリー — {period}",
        "fr": "Résumé hebdomadaire AgenticTrade — {period}",
        "de": "Ihre wöchentliche AgenticTrade-Zusammenfassung — {period}",
        "ru": "Еженедельная сводка AgenticTrade — {period}",
        "es": "Resumen semanal de AgenticTrade — {period}",
        "pt": "Resumo semanal do AgenticTrade — {period}",
    },
}


def _get_subject(drip_type: str, locale: str = "en", **kwargs: str) -> str:
    """Return localized email subject line for a given drip type."""
    type_subjects = _DRIP_SUBJECTS.get(drip_type, {})
    subject = type_subjects.get(locale, type_subjects.get("en", "Update from AgenticTrade"))
    for key, value in kwargs.items():
        subject = subject.replace(f"{{{key}}}", value)
    return subject


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

DRIP_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS drip_emails (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    email TEXT NOT NULL,
    drip_type TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    sent_at TEXT,
    status TEXT DEFAULT 'pending',
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_drip_provider
    ON drip_emails(provider_id, drip_type);
CREATE INDEX IF NOT EXISTS idx_drip_status
    ON drip_emails(status, scheduled_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_drip_unique_type
    ON drip_emails(provider_id, drip_type)
    WHERE drip_type != 'weekly_digest';
"""


def _ensure_drip_table(db: Database) -> None:
    """Create drip_emails table if not exists."""
    with db.connect() as conn:
        conn.executescript(DRIP_TABLE_SQL)


# ---------------------------------------------------------------------------
# Core scheduler class
# ---------------------------------------------------------------------------

class DripEmailScheduler:
    """
    Manages provider drip email lifecycle.

    - Schedules time-based drips (welcome, onboarding) on provider registration
    - Triggers event-based drips (first_sale) when milestones happen
    - Processes pending drips via cron-friendly process_pending()
    - Tracks all sent emails to prevent duplicates
    """

    def __init__(self, db: Database):
        self.db = db
        _ensure_drip_table(db)

    # --- Scheduling ---

    def schedule_welcome_sequence(self, provider_id: str, email: str, locale: str = "en") -> list[dict]:
        """
        Schedule the welcome + onboarding drip sequence for a new provider.
        Called at provider registration time.

        Returns list of scheduled drip records.
        """
        if not provider_id or not email:
            raise ValueError("provider_id and email are required")

        now = datetime.now(timezone.utc)
        scheduled = []

        for delay_days, drip_type in SCHEDULED_DRIPS:
            scheduled_at = now + timedelta(days=delay_days)
            record = self._schedule_drip(
                provider_id=provider_id,
                email=email,
                drip_type=drip_type,
                scheduled_at=scheduled_at,
                locale=locale,
            )
            if record is not None:
                scheduled.append(record)

        return scheduled

    def trigger_first_sale(self, provider_id: str, email: str, locale: str = "en") -> Optional[dict]:
        """
        Trigger the first-sale celebration email.
        Called when a provider's first transaction is recorded.

        Returns the scheduled record or None if already sent.
        """
        if not provider_id or not email:
            raise ValueError("provider_id and email are required")

        # Check if first sale drip was already sent or scheduled
        if self._has_drip(provider_id, DRIP_FIRST_SALE):
            return None

        now = datetime.now(timezone.utc)
        return self._schedule_drip(
            provider_id=provider_id,
            email=email,
            drip_type=DRIP_FIRST_SALE,
            scheduled_at=now,
            locale=locale,
        )

    def schedule_weekly_digest(
        self,
        provider_id: str,
        email: str,
        total_calls: int,
        total_revenue_usd: float,
        period: str,
        locale: str = "en",
    ) -> Optional[dict]:
        """
        Schedule a weekly digest email for an active provider.

        Unlike other drip types, weekly digests can be sent multiple times
        (one per week). The period string (e.g. "Mar 17-23, 2026") is used
        in the subject and body.

        Returns the scheduled record or None on error.
        """
        if not provider_id or not email:
            raise ValueError("provider_id and email are required")

        import json as _json
        now = datetime.now(timezone.utc)
        record_id = str(uuid.uuid4())
        metadata = _json.dumps({
            "total_calls": total_calls,
            "total_revenue_usd": round(total_revenue_usd, 2),
            "period": period,
            "locale": locale,
        })

        record = {
            "id": record_id,
            "provider_id": provider_id,
            "email": email,
            "drip_type": DRIP_WEEKLY_DIGEST,
            "scheduled_at": now.isoformat(),
            "sent_at": None,
            "status": "pending",
            "metadata": metadata,
        }

        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO drip_emails
                   (id, provider_id, email, drip_type, scheduled_at, sent_at, status, metadata)
                   VALUES (:id, :provider_id, :email, :drip_type, :scheduled_at, :sent_at, :status, :metadata)""",
                record,
            )

        return record

    # --- Processing ---

    def process_pending(self, dry_run: bool = False) -> dict:
        """
        Process all pending drip emails whose scheduled_at has passed.

        Returns summary: {"sent": int, "failed": int, "skipped": int, "details": list}
        """
        now = datetime.now(timezone.utc).isoformat()
        pending = self._get_pending(before=now)

        result = {"sent": 0, "failed": 0, "skipped": 0, "details": []}

        if not pending:
            logger.info("No pending drip emails to process")
            return result

        for record in pending:
            drip_type = record["drip_type"]
            email = record["email"]
            provider_id = record["provider_id"]

            if dry_run:
                result["skipped"] += 1
                result["details"].append({
                    "id": record["id"],
                    "drip_type": drip_type,
                    "email": email,
                    "action": "dry_run_skip",
                })
                continue

            # Parse metadata once
            import json
            try:
                meta = json.loads(record.get("metadata", "{}"))
            except (json.JSONDecodeError, TypeError):
                meta = {}
            locale = meta.get("locale", "en")

            # Build template kwargs
            template_kwargs = {"provider_id": provider_id}
            if drip_type == DRIP_WEEKLY_DIGEST:
                template_kwargs["total_calls"] = str(meta.get("total_calls", 0))
                template_kwargs["total_revenue_usd"] = f"{meta.get('total_revenue_usd', 0):.2f}"
                template_kwargs["period"] = meta.get("period", "This Week")

            try:
                html = _load_template(drip_type, **template_kwargs)
            except TemplateNotFoundError:
                self._mark_failed(record["id"])
                result["failed"] += 1
                result["details"].append({
                    "id": record["id"],
                    "drip_type": drip_type,
                    "email": email,
                    "action": "failed",
                    "error": "template_not_found",
                })
                continue

            if not html:
                self._mark_failed(record["id"])
                result["failed"] += 1
                result["details"].append({
                    "id": record["id"],
                    "drip_type": drip_type,
                    "email": email,
                    "action": "failed",
                    "error": "template_empty",
                })
                continue

            subject_kwargs = {}
            if drip_type == DRIP_WEEKLY_DIGEST:
                subject_kwargs["period"] = meta.get("period", "This Week")

            subject = _get_subject(drip_type, locale=locale, **subject_kwargs)
            success = _send_email(email, subject, html)

            if success:
                self._mark_sent(record["id"])
                result["sent"] += 1
                result["details"].append({
                    "id": record["id"],
                    "drip_type": drip_type,
                    "email": email,
                    "action": "sent",
                })
            else:
                self._mark_failed(record["id"])
                result["failed"] += 1
                result["details"].append({
                    "id": record["id"],
                    "drip_type": drip_type,
                    "email": email,
                    "action": "failed",
                    "error": "send_failed",
                })

        logger.info(
            "Drip processing complete: sent=%d, failed=%d, skipped=%d",
            result["sent"], result["failed"], result["skipped"],
        )
        return result

    # --- Query methods ---

    def get_provider_drips(self, provider_id: str) -> list[dict]:
        """Get all drip email records for a provider."""
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM drip_emails
                   WHERE provider_id = ?
                   ORDER BY scheduled_at ASC""",
                (provider_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_count(self) -> int:
        """Count pending drip emails due for sending."""
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) as cnt FROM drip_emails
                   WHERE status = 'pending' AND scheduled_at <= ?""",
                (now,),
            ).fetchone()
        return row["cnt"] if row else 0

    # --- Internal helpers ---

    def _schedule_drip(
        self,
        provider_id: str,
        email: str,
        drip_type: str,
        scheduled_at: datetime,
        locale: str = "en",
    ) -> Optional[dict]:
        """Insert a drip email record. Returns None if duplicate."""
        import json as _json
        record_id = str(uuid.uuid4())
        record = {
            "id": record_id,
            "provider_id": provider_id,
            "email": email,
            "drip_type": drip_type,
            "scheduled_at": scheduled_at.isoformat(),
            "sent_at": None,
            "status": "pending",
            "metadata": _json.dumps({"locale": locale}),
        }

        try:
            with self.db.connect() as conn:
                conn.execute(
                    """INSERT INTO drip_emails
                       (id, provider_id, email, drip_type, scheduled_at, sent_at, status, metadata)
                       VALUES (:id, :provider_id, :email, :drip_type, :scheduled_at, :sent_at, :status, :metadata)""",
                    record,
                )
            return record
        except Exception:
            # Unique constraint violation — drip already scheduled
            logger.debug("Drip already exists: %s / %s", provider_id, drip_type)
            return None

    def _has_drip(self, provider_id: str, drip_type: str) -> bool:
        """Check if a drip was already scheduled (sent or pending) for this provider."""
        with self.db.connect() as conn:
            row = conn.execute(
                """SELECT 1 FROM drip_emails
                   WHERE provider_id = ? AND drip_type = ?""",
                (provider_id, drip_type),
            ).fetchone()
        return row is not None

    def _get_pending(self, before: str) -> list[dict]:
        """Get pending drip emails scheduled before the given ISO timestamp."""
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM drip_emails
                   WHERE status = 'pending' AND scheduled_at <= ?
                   ORDER BY scheduled_at ASC""",
                (before,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _mark_sent(self, drip_id: str) -> None:
        """Mark a drip email as sent."""
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE drip_emails SET status = 'sent', sent_at = ? WHERE id = ?",
                (now, drip_id),
            )

    def _mark_failed(self, drip_id: str) -> None:
        """Mark a drip email as failed."""
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE drip_emails SET status = 'failed' WHERE id = ?",
                (drip_id,),
            )


# ---------------------------------------------------------------------------
# Email sender (Resend API)
# ---------------------------------------------------------------------------

def _send_email(to: str, subject: str, html: str) -> bool:
    """Send an email via Resend API. Returns True on success."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — cannot send email to %s", to)
        return False

    try:
        import httpx
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": f"{FROM_NAME} <{FROM_EMAIL}>",
                "to": [to],
                "subject": subject,
                "html": html,
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            logger.info("Email sent to %s: %s", to, subject[:60])
            return True
        logger.warning(
            "Resend error %d for %s: %s",
            resp.status_code, to, resp.text[:200],
        )
        return False
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to, e)
        return False
