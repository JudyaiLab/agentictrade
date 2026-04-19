"""
Quality Notification System — Email providers when service quality degrades.

Sends localized emails (9 languages) to service providers when:
1. Quality WARNING: score drops below 75 (advisory)
2. Quality PAUSED: service auto-paused due to sustained low quality (<60 for 3 rounds)
3. Quality DECLINING: 7-day decline >15 points (early warning)

Uses Resend API for delivery. Deduplicates notifications per service per day.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from .db import Database

logger = logging.getLogger("quality_notifier")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@agentictrade.io")
FROM_NAME = os.environ.get("FROM_NAME", "AgenticTrade")
DEFAULT_LOCALE = "en"

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates",
    "email",
)

# ---------------------------------------------------------------------------
# Provider contact registry — lightweight table linking provider_id to email
# ---------------------------------------------------------------------------

_CONTACT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS provider_contacts (
    provider_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    preferred_locale TEXT DEFAULT 'en',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_NOTIF_LOG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS quality_notifications (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    notification_type TEXT NOT NULL,
    quality_score REAL,
    sent_at TEXT NOT NULL,
    locale TEXT DEFAULT 'en'
);
CREATE INDEX IF NOT EXISTS idx_qn_provider_date
    ON quality_notifications(provider_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_qn_service_date
    ON quality_notifications(service_id, sent_at);
"""


def _ensure_tables(db: Database) -> None:
    with db.connect() as conn:
        conn.executescript(_CONTACT_TABLE_SQL)
        conn.executescript(_NOTIF_LOG_TABLE_SQL)


# ---------------------------------------------------------------------------
# Provider contact CRUD
# ---------------------------------------------------------------------------


def upsert_provider_contact(
    db: Database,
    provider_id: str,
    email: str,
    preferred_locale: str = DEFAULT_LOCALE,
) -> None:
    """Create or update a provider's contact info."""
    _ensure_tables(db)
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO provider_contacts (provider_id, email, preferred_locale, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(provider_id) DO UPDATE SET
                   email = excluded.email,
                   preferred_locale = excluded.preferred_locale,
                   updated_at = excluded.updated_at""",
            (provider_id, email, preferred_locale, now, now),
        )


def get_provider_contact(db: Database, provider_id: str) -> Optional[dict]:
    """Look up provider contact. Falls back to agent_providers.owner_email."""
    _ensure_tables(db)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM provider_contacts WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()
        if row:
            return dict(row)

        # Fallback: check agent_providers table
        ap_row = conn.execute(
            "SELECT owner_email FROM agent_providers WHERE id = ? OR agent_id = ?",
            (provider_id, provider_id),
        ).fetchone()
        if ap_row:
            return {
                "provider_id": provider_id,
                "email": ap_row["owner_email"],
                "preferred_locale": DEFAULT_LOCALE,
            }
    return None


# ---------------------------------------------------------------------------
# Localized email subjects
# ---------------------------------------------------------------------------

_QUALITY_SUBJECTS: dict[str, dict[str, str]] = {
    "quality_warning": {
        "en": "⚠️ Service Quality Alert — {service_name} (Score: {score})",
        "zh-tw": "⚠️ 服務品質警告 — {service_name}（分數：{score}）",
        "ko": "⚠️ 서비스 품질 경고 — {service_name} (점수: {score})",
        "ja": "⚠️ サービス品質アラート — {service_name}（スコア: {score}）",
        "fr": "⚠️ Alerte qualité de service — {service_name} (Score : {score})",
        "de": "⚠️ Service-Qualitätswarnung — {service_name} (Bewertung: {score})",
        "ru": "⚠️ Предупреждение о качестве сервиса — {service_name} (Оценка: {score})",
        "es": "⚠️ Alerta de calidad del servicio — {service_name} (Puntuación: {score})",
        "pt": "⚠️ Alerta de qualidade do serviço — {service_name} (Pontuação: {score})",
    },
    "quality_paused": {
        "en": "⛔ Service Paused — {service_name} (Quality Below Threshold)",
        "zh-tw": "⛔ 服務已暫停 — {service_name}（品質低於門檻）",
        "ko": "⛔ 서비스 일시 중지 — {service_name} (품질 기준 미달)",
        "ja": "⛔ サービス一時停止 — {service_name}（品質基準未達）",
        "fr": "⛔ Service suspendu — {service_name} (Qualité en dessous du seuil)",
        "de": "⛔ Service pausiert — {service_name} (Qualität unter Schwellenwert)",
        "ru": "⛔ Сервис приостановлен — {service_name} (Качество ниже порога)",
        "es": "⛔ Servicio pausado — {service_name} (Calidad por debajo del umbral)",
        "pt": "⛔ Serviço pausado — {service_name} (Qualidade abaixo do limite)",
    },
    "quality_declining": {
        "en": "📉 Quality Declining — {service_name} (Down {decline} pts in 7 days)",
        "zh-tw": "📉 品質下降中 — {service_name}（7 天內下降 {decline} 分）",
        "ko": "📉 품질 하락 중 — {service_name} (7일간 {decline}점 하락)",
        "ja": "📉 品質低下中 — {service_name}（7日間で {decline} ポイント低下）",
        "fr": "📉 Qualité en baisse — {service_name} (−{decline} pts en 7 jours)",
        "de": "📉 Qualität sinkt — {service_name} (−{decline} Pkte in 7 Tagen)",
        "ru": "📉 Качество снижается — {service_name} (−{decline} баллов за 7 дней)",
        "es": "📉 Calidad en descenso — {service_name} (−{decline} pts en 7 días)",
        "pt": "📉 Qualidade em declínio — {service_name} (−{decline} pts em 7 dias)",
    },
}


def _get_subject(
    notif_type: str, locale: str, **kwargs: str,
) -> str:
    type_subjects = _QUALITY_SUBJECTS.get(notif_type, {})
    subject = type_subjects.get(locale, type_subjects.get("en", "AgenticTrade Quality Alert"))
    for key, value in kwargs.items():
        subject = subject.replace(f"{{{key}}}", str(value))
    return subject


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------


def _load_quality_template(notif_type: str, locale: str, **kwargs: str) -> str:
    """Load localized HTML template. Falls back to English if locale missing."""
    # Try locale-specific first, then fallback to base template
    for candidate in [f"{notif_type}_{locale}.html", f"{notif_type}.html"]:
        path = os.path.join(_TEMPLATES_DIR, candidate)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                html = f.read()
            for key, value in kwargs.items():
                html = html.replace(f"{{{{{key}}}}}", str(value))
            return html

    logger.error("Quality email template not found: %s", notif_type)
    return ""


# ---------------------------------------------------------------------------
# Deduplication check
# ---------------------------------------------------------------------------


def _already_notified_today(
    db: Database, service_id: str, notif_type: str,
) -> bool:
    """Check if we already sent this notification type for this service today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db.connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) as c FROM quality_notifications
               WHERE service_id = ? AND notification_type = ?
               AND sent_at >= ?""",
            (service_id, notif_type, today),
        ).fetchone()
    return row["c"] > 0 if row else False


# ---------------------------------------------------------------------------
# Send email via Resend
# ---------------------------------------------------------------------------


def _send_email(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — cannot send quality email to %s", to)
        return False

    if not html:
        logger.error("Empty email body — skipping send to %s", to)
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
            logger.info("Quality email sent to %s: %s", to, subject[:60])
            return True
        logger.warning("Resend error %d for %s: %s", resp.status_code, to, resp.text[:200])
        return False
    except Exception as e:
        logger.error("Failed to send quality email to %s: %s", to, e)
        return False


def _log_notification(
    db: Database,
    provider_id: str,
    service_id: str,
    notif_type: str,
    quality_score: float,
    locale: str,
) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO quality_notifications
               (id, provider_id, service_id, notification_type, quality_score, sent_at, locale)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                provider_id,
                service_id,
                notif_type,
                quality_score,
                datetime.now(timezone.utc).isoformat(),
                locale,
            ),
        )


# ---------------------------------------------------------------------------
# Public API — called from health_monitor.enforce_quality_gate()
# ---------------------------------------------------------------------------


def notify_quality_warning(
    db: Database,
    service_id: str,
    service_name: str,
    provider_id: str,
    quality_score: float,
) -> bool:
    """Send a quality warning email to the provider. Returns True if sent."""
    return _notify(
        db, "quality_warning", service_id, service_name,
        provider_id, quality_score,
    )


def notify_quality_paused(
    db: Database,
    service_id: str,
    service_name: str,
    provider_id: str,
    quality_score: float,
) -> bool:
    """Send a service-paused email to the provider. Returns True if sent."""
    return _notify(
        db, "quality_paused", service_id, service_name,
        provider_id, quality_score,
    )


def notify_quality_declining(
    db: Database,
    service_id: str,
    service_name: str,
    provider_id: str,
    quality_score: float,
    decline: float = 0.0,
) -> bool:
    """Send a quality-declining email to the provider. Returns True if sent."""
    return _notify(
        db, "quality_declining", service_id, service_name,
        provider_id, quality_score, decline=decline,
    )


def _notify(
    db: Database,
    notif_type: str,
    service_id: str,
    service_name: str,
    provider_id: str,
    quality_score: float,
    decline: float = 0.0,
) -> bool:
    """Core notification logic with dedup, locale lookup, and send."""
    _ensure_tables(db)

    # Deduplicate: max 1 per notification type per service per day
    if _already_notified_today(db, service_id, notif_type):
        logger.debug("Already notified %s for %s today — skipping", notif_type, service_id[:12])
        return False

    # Look up provider contact
    contact = get_provider_contact(db, provider_id)
    if not contact:
        logger.warning("No contact for provider %s — cannot send %s", provider_id, notif_type)
        return False

    locale = contact.get("preferred_locale", DEFAULT_LOCALE)
    email = contact["email"]

    # Build email
    subject = _get_subject(
        notif_type, locale,
        service_name=service_name,
        score=str(round(quality_score, 1)),
        decline=str(round(decline, 1)),
    )
    html = _load_quality_template(
        notif_type, locale,
        service_name=service_name,
        quality_score=str(round(quality_score, 1)),
        decline=str(round(decline, 1)),
        provider_id=provider_id,
        service_id=service_id,
        threshold_warn="75",
        threshold_pause="60",
        policy_url="https://agentictrade.io/quality-policy",
    )

    sent = _send_email(email, subject, html)

    if sent:
        _log_notification(db, provider_id, service_id, notif_type, quality_score, locale)

    return sent
