"""
Email routes — subscriber management, drip scheduling,
and provider drip email processing.

Flow:
  1. User submits email → subscriber created
  2. Welcome email sent immediately via Resend
  3. Subscriber added to Brevo for drip sequence (if configured)
  4. Getting Started page link returned

Provider drip flow:
  1. Provider registers → welcome + onboarding drips scheduled
  2. First sale recorded → first_sale drip triggered
  3. Weekly cron → digest drip for active providers
  4. POST /api/v1/email/drip-process → processes all pending drips
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from marketplace.drip_email import DripEmailScheduler
from marketplace.i18n import detect_locale

logger = logging.getLogger("email")

router = APIRouter(prefix="/api/v1", tags=["email"])

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_LIST_ID = int(os.environ.get("BREVO_LIST_ID", "0"))
FROM_EMAIL = os.environ.get("FROM_EMAIL", "onboarding@agentictrade.io")
FROM_NAME = os.environ.get("FROM_NAME", "AgenticTrade")
FORWARD_TO_EMAIL = os.environ.get("FORWARD_TO_EMAIL", "")

# Simple email regex — loose enough for real-world use, strict enough to reject garbage
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# Drip schedule: (delay_days, stage_name, subject)
DRIP_SCHEDULE = [
    (2, "quickstart", "3 Quick-Start Tips for Your Agent Marketplace"),
    (5, "usecase", "How Other Agents Are Using AgenticTrade"),
    (10, "platform", "Your Agents Can Earn — Here's How"),
]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class DownloadGateRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254, description="Email address")
    source: str = Field(default="website", max_length=50)
    locale: str = Field(default="", max_length=10, description="Preferred locale (auto-detected if empty)")
    consent: bool = Field(
        default=False,
        description="Explicit consent to receive marketing emails. Must be True.",
    )


class DownloadGateResponse(BaseModel):
    download_url: str
    message: str
    already_subscribed: bool = False


class UnsubscribeResponse(BaseModel):
    message: str


class SubscriberStatsResponse(BaseModel):
    total_subscribers: int
    source: str = "all"


# ---------------------------------------------------------------------------
# Download gate
# ---------------------------------------------------------------------------

@router.post("/download-gate", response_model=DownloadGateResponse)
async def download_gate(
    req: DownloadGateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Email-gated download endpoint.
    Collects email, sends welcome email, returns download link.
    """
    email = req.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(422, "Invalid email address format")

    # Require explicit consent for marketing email collection
    if not req.consent:
        raise HTTPException(
            422,
            "Explicit consent is required to subscribe to marketing emails. "
            "Set consent=true to proceed.",
        )

    db = request.app.state.db
    now = datetime.now(timezone.utc)

    # Record consent metadata (IP + timestamp)
    client_ip = request.client.host if request.client else "unknown"

    # Detect subscriber locale: explicit > cookie > Accept-Language
    locale = detect_locale(
        query_lang=req.locale or None,
        cookie_lang=request.cookies.get("lang"),
        accept_language=request.headers.get("accept-language"),
    )

    # Build base URL — respect X-Forwarded-Proto behind reverse proxy
    _proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    base_url = f"{_proto}://{request.url.netloc}"

    # Check if already subscribed
    existing = db.get_subscriber(email)
    if existing and not existing.get("unsubscribed"):
        return DownloadGateResponse(
            download_url=f"{base_url}/portal/getting-started",
            message="Welcome back! Here's your download link.",
            already_subscribed=True,
        )

    # Create subscriber with detected locale and consent tracking
    first_drip_at = (now + timedelta(days=DRIP_SCHEDULE[0][0])).isoformat()
    import json as _json
    subscriber = {
        "id": str(uuid.uuid4()),
        "email": email,
        "source": req.source,
        "subscribed_at": now.isoformat(),
        "confirmed": 0,
        "drip_stage": 0,
        "drip_next_at": first_drip_at,
        "metadata": _json.dumps({
            "locale": locale,
            "consent_given_at": now.isoformat(),
            "consent_ip": client_ip,
        }),
    }
    is_new = db.insert_subscriber(subscriber)

    # Record immutable consent evidence in a dedicated audit table
    if is_new:
        db.insert_consent_record({
            "id": str(uuid.uuid4()),
            "email": email,
            "consent_type": "marketing",
            "consent_given_at": now.isoformat(),
            "consent_ip": client_ip,
            "source": req.source,
        })

    # Send welcome email in background (non-blocking)
    if is_new:
        download_url = f"{base_url}/portal/getting-started"
        background_tasks.add_task(_send_welcome_email, email, download_url, locale)
        background_tasks.add_task(_add_to_brevo, email, req.source)

    return DownloadGateResponse(
        download_url=f"{base_url}/portal/getting-started",
        message="Check your email for the welcome guide. Here's your download link!",
        already_subscribed=not is_new,
    )


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------

def _unsub_token(email: str) -> str:
    """Generate a non-deterministic unsubscribe token.

    Format: ``nonce:hmac_hex`` where *nonce* is random hex and the HMAC
    covers ``email|nonce`` so that different calls for the same email
    produce different tokens.  An attacker who knows the email cannot
    predict the token without knowing both the secret and the nonce.
    """
    import hashlib, hmac, secrets as _secrets
    secret = os.environ.get("ACF_ADMIN_SECRET", "")
    nonce = _secrets.token_hex(16)
    sig = hmac.new(
        secret.encode(), f"{email.lower()}|{nonce}".encode(), hashlib.sha256,
    ).hexdigest()[:32]
    return f"{nonce}:{sig}"


def _verify_unsub_token(email: str, token: str) -> bool:
    """Verify an unsubscribe token (``nonce:hmac_hex`` format)."""
    import hashlib, hmac as _hmac
    if not token or ":" not in token:
        return False
    nonce, sig = token.split(":", 1)
    secret = os.environ.get("ACF_ADMIN_SECRET", "")
    expected = _hmac.new(
        secret.encode(), f"{email.lower()}|{nonce}".encode(), hashlib.sha256,
    ).hexdigest()[:32]
    return _hmac.compare_digest(sig, expected)


@router.get("/unsubscribe", response_model=UnsubscribeResponse)
def unsubscribe(email: str, token: str = "", request: Request = None):
    """One-click unsubscribe (requires valid token from email link)."""
    if not _verify_unsub_token(email.strip().lower(), token):
        raise HTTPException(403, "Invalid or missing unsubscribe token")
    db = request.app.state.db
    found = db.unsubscribe(email.strip().lower())
    if found:
        return UnsubscribeResponse(message="You've been unsubscribed. Sorry to see you go!")
    return UnsubscribeResponse(message="Email not found in our subscriber list.")


# ---------------------------------------------------------------------------
# Admin: subscriber stats
# ---------------------------------------------------------------------------

@router.get("/admin/subscribers")
def subscriber_stats(request: Request):
    """Get subscriber count (admin only)."""
    import hmac as _hmac
    expected = os.environ.get("ACF_ADMIN_SECRET", "")
    if not expected:
        raise HTTPException(503, "Admin credentials not configured")
    auth_header = request.headers.get("x-admin-key", "")
    if not auth_header:
        raise HTTPException(401, "Admin key required")
    if not _hmac.compare_digest(auth_header, expected):
        raise HTTPException(401, "Invalid admin key")

    db = request.app.state.db
    count = db.count_subscribers()
    return SubscriberStatsResponse(total_subscribers=count)


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

async def _send_welcome_email(email: str, download_url: str, locale: str = "en") -> None:
    """Send welcome email via Resend API in the subscriber's detected language."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping welcome email to %s", email)
        return

    subject = _welcome_subject(locale)
    html = _welcome_email_html(download_url, locale)

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": f"{FROM_NAME} <{FROM_EMAIL}>",
                    "to": [email],
                    "subject": subject,
                    "html": html,
                },
            )
            if resp.status_code in (200, 201):
                logger.info("Welcome email sent to %s (locale=%s)", email, locale)
            else:
                logger.warning(
                    "Resend API error %d: %s", resp.status_code, resp.text[:200],
                )
    except Exception as e:
        logger.error("Failed to send welcome email: %s", e)


async def _add_to_brevo(email: str, source: str) -> None:
    """Add subscriber to Brevo list for drip automation."""
    if not BREVO_API_KEY or not BREVO_LIST_ID:
        logger.info("Brevo not configured — skipping drip subscription for %s", email)
        return

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.brevo.com/v3/contacts",
                headers={
                    "api-key": BREVO_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "email": email,
                    "listIds": [BREVO_LIST_ID],
                    "attributes": {"SOURCE": source},
                    "updateEnabled": True,
                },
            )
            if resp.status_code in (200, 201, 204):
                logger.info("Added %s to Brevo list %d", email, BREVO_LIST_ID)
            else:
                logger.warning(
                    "Brevo API error %d: %s", resp.status_code, resp.text[:200],
                )
    except Exception as e:
        logger.error("Failed to add to Brevo: %s", e)


# ---------------------------------------------------------------------------
# Provider drip email processing
# ---------------------------------------------------------------------------

class DripProcessResponse(BaseModel):
    sent: int
    failed: int
    skipped: int
    details: list = []


class DripTriggerRequest(BaseModel):
    provider_id: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=5, max_length=254)
    trigger: str = Field(..., description="Trigger type: first_sale")


class DripTriggerResponse(BaseModel):
    triggered: bool
    drip_type: str
    message: str


@router.post("/email/drip-process", response_model=DripProcessResponse)
def drip_process(request: Request, dry_run: bool = False):
    """
    Cron-friendly endpoint: process all pending provider drip emails.

    Secured via admin key in x-admin-key header.
    Pass ?dry_run=true to preview without actually sending.
    """
    import hmac as _hmac

    expected = os.environ.get("ACF_ADMIN_SECRET", "")
    if not expected:
        raise HTTPException(503, "Admin credentials not configured")
    auth_header = request.headers.get("x-admin-key", "")
    if not auth_header:
        raise HTTPException(401, "Admin key required")
    if not _hmac.compare_digest(auth_header, expected):
        raise HTTPException(401, "Invalid admin key")

    db = request.app.state.db
    scheduler = DripEmailScheduler(db)
    result = scheduler.process_pending(dry_run=dry_run)
    return DripProcessResponse(**result)


@router.post("/email/drip-trigger", response_model=DripTriggerResponse)
def drip_trigger(req: DripTriggerRequest, request: Request):
    """
    Trigger a milestone-based drip email (e.g., first_sale).

    Secured via admin key in x-admin-key header.
    Idempotent — won't send duplicate if already triggered.
    """
    import hmac as _hmac

    expected = os.environ.get("ACF_ADMIN_SECRET", "")
    if not expected:
        raise HTTPException(503, "Admin credentials not configured")
    auth_header = request.headers.get("x-admin-key", "")
    if not auth_header:
        raise HTTPException(401, "Admin key required")
    if not _hmac.compare_digest(auth_header, expected):
        raise HTTPException(401, "Invalid admin key")

    if req.trigger != "first_sale":
        raise HTTPException(422, f"Unknown trigger type: {req.trigger}. Supported: first_sale")

    if not _EMAIL_RE.match(req.email.strip().lower()):
        raise HTTPException(422, "Invalid email address format")

    db = request.app.state.db
    scheduler = DripEmailScheduler(db)
    record = scheduler.trigger_first_sale(
        provider_id=req.provider_id,
        email=req.email.strip().lower(),
    )

    if record is not None:
        return DripTriggerResponse(
            triggered=True,
            drip_type="first_sale",
            message="First sale celebration email scheduled.",
        )
    return DripTriggerResponse(
        triggered=False,
        drip_type="first_sale",
        message="First sale email was already sent or scheduled.",
    )


@router.get("/email/drip-status")
def drip_status(request: Request, provider_id: str = ""):
    """
    Get drip email status for a provider (or global pending count).

    Secured via admin key in x-admin-key header.
    """
    import hmac as _hmac

    expected = os.environ.get("ACF_ADMIN_SECRET", "")
    if not expected:
        raise HTTPException(503, "Admin credentials not configured")
    auth_header = request.headers.get("x-admin-key", "")
    if not auth_header:
        raise HTTPException(401, "Admin key required")
    if not _hmac.compare_digest(auth_header, expected):
        raise HTTPException(401, "Invalid admin key")

    db = request.app.state.db
    scheduler = DripEmailScheduler(db)

    if provider_id:
        drips = scheduler.get_provider_drips(provider_id)
        return {"provider_id": provider_id, "drips": drips}

    pending = scheduler.get_pending_count()
    return {"pending_count": pending}


# ---------------------------------------------------------------------------
# Resend Inbound Webhook — forward received emails to team Gmail
# ---------------------------------------------------------------------------

RESEND_WEBHOOK_SECRET = os.environ.get("RESEND_WEBHOOK_SECRET", "")


@router.post("/email/inbound")
async def resend_inbound_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Resend inbound webhook endpoint.

    When someone sends email to *@agentictrade.io, Resend fires a POST here.
    We fetch the full email via Resend API and forward it to FORWARD_TO_EMAIL.
    """
    # Verify webhook signature if secret is configured
    if RESEND_WEBHOOK_SECRET:
        import hmac as _hmac
        import hashlib as _hashlib
        body = await request.body()
        signature = request.headers.get("resend-signature", "")
        if not signature:
            raise HTTPException(400, "Missing webhook signature")
        expected = _hmac.new(
            RESEND_WEBHOOK_SECRET.encode(), body, _hashlib.sha256,
        ).hexdigest()
        if not _hmac.compare_digest(signature, expected):
            logger.warning("Inbound email webhook: signature mismatch")
            raise HTTPException(400, "Invalid signature")
        try:
            payload = __import__("json").loads(body)
        except Exception:
            raise HTTPException(400, "Invalid JSON payload")
    elif not RESEND_API_KEY:
        # No secret and no API key = not configured, reject
        raise HTTPException(503, "Email webhook not configured")
    else:
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON payload")

    event_type = payload.get("type", "")
    if event_type != "email.received":
        return {"status": "ignored", "event": event_type}

    data = payload.get("data", {})
    email_id = data.get("email_id", "")

    if not email_id:
        logger.warning("Inbound webhook missing email_id")
        return {"status": "skipped", "reason": "no email_id"}

    background_tasks.add_task(_forward_inbound_email, email_id)
    return {"status": "accepted", "email_id": email_id}


async def _forward_inbound_email(email_id: str) -> None:
    """Fetch inbound email from Resend API and forward to team Gmail."""
    if not RESEND_API_KEY:
        logger.error("RESEND_API_KEY not set — cannot forward inbound email")
        return

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            # Fetch the received email content
            resp = await client.get(
                f"https://api.resend.com/emails/{email_id}",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            )
            if resp.status_code != 200:
                logger.error("Failed to fetch inbound email %s: %d", email_id, resp.status_code)
                return

            email_data = resp.json()
            from_addr = email_data.get("from", "unknown@unknown.com")
            to_addrs = email_data.get("to", [])
            subject = email_data.get("subject", "(no subject)")
            html_body = email_data.get("html", "")
            text_body = email_data.get("text", "")

            # Build forwarded email
            fwd_subject = f"[AgenticTrade] Fwd: {subject}"
            fwd_html = f"""<div style="font-family: sans-serif; padding: 12px; background: #f5f5f5; border-radius: 8px; margin-bottom: 16px;">
<strong>Forwarded email received at AgenticTrade</strong><br>
<b>From:</b> {from_addr}<br>
<b>To:</b> {', '.join(to_addrs) if isinstance(to_addrs, list) else to_addrs}<br>
<b>Subject:</b> {subject}
</div>
<hr>
{html_body or f'<pre>{text_body}</pre>'}"""

            # Send forwarded email via Resend
            send_resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": f"AgenticTrade Inbox <{FROM_EMAIL}>",
                    "to": [FORWARD_TO_EMAIL],
                    "subject": fwd_subject,
                    "html": fwd_html,
                    "reply_to": from_addr,
                },
            )
            if send_resp.status_code in (200, 201):
                logger.info("Forwarded inbound email %s from %s to %s", email_id, from_addr, FORWARD_TO_EMAIL)
            else:
                logger.error("Failed to forward email: %d %s", send_resp.status_code, send_resp.text[:200])
    except Exception as e:
        logger.error("Error forwarding inbound email %s: %s", email_id, e)


# ---------------------------------------------------------------------------
# Multi-language email content
# ---------------------------------------------------------------------------

_WELCOME_CONTENT: dict[str, dict[str, str]] = {
    "en": {
        "subject": "Your AgenticTrade Starter Kit is Ready",
        "title": "Welcome to AgenticTrade",
        "ready": "Your Starter Kit is ready to download:",
        "btn": "Download Starter Kit",
        "inside": "What's Inside",
        "item1": "<strong>4 Agent Templates</strong> — ready-to-deploy marketplace agents",
        "item2": "<strong>13-Chapter Guide</strong> — from setup to revenue",
        "item3": "<strong>Deploy Configs</strong> — Docker, CI/CD, monitoring",
        "item4": "<strong>CLI Tools</strong> — register, test, and manage agents",
        "quickstart": "Quick Start",
        "step1": "Unzip the kit",
        "step2": "Run <code>pip install -r requirements.txt</code>",
        "step3": "Follow Chapter 1 of the guide",
        "step4": "Your first agent can be live in 15 minutes",
        "questions": "Questions? Reply to this email — we read every message.",
        "footer": "You're receiving this because you downloaded the AgenticTrade Starter Kit.",
    },
    "zh-tw": {
        "subject": "你的 AgenticTrade 入門套件已準備好",
        "title": "歡迎來到 AgenticTrade",
        "ready": "你的入門套件已準備好下載：",
        "btn": "下載入門套件",
        "inside": "套件內容",
        "item1": "<strong>4 個 Agent 範本</strong> — 可直接部署的市集 Agent",
        "item2": "<strong>13 章指南</strong> — 從設定到產生收入",
        "item3": "<strong>部署設定檔</strong> — Docker、CI/CD、監控",
        "item4": "<strong>CLI 工具</strong> — 註冊、測試、管理 Agent",
        "quickstart": "快速開始",
        "step1": "解壓縮套件",
        "step2": "執行 <code>pip install -r requirements.txt</code>",
        "step3": "跟著指南第 1 章操作",
        "step4": "15 分鐘內你的第一個 Agent 就能上線",
        "questions": "有問題嗎？直接回覆這封信 — 我們會閱讀每一封訊息。",
        "footer": "你收到此郵件是因為你下載了 AgenticTrade 入門套件。",
    },
    "ko": {
        "subject": "AgenticTrade 스타터 킷이 준비되었습니다",
        "title": "AgenticTrade에 오신 것을 환영합니다",
        "ready": "스타터 킷을 다운로드할 준비가 되었습니다:",
        "btn": "스타터 킷 다운로드",
        "inside": "포함 내용",
        "item1": "<strong>4개 에이전트 템플릿</strong> — 배포 준비 완료된 마켓플레이스 에이전트",
        "item2": "<strong>13장 가이드</strong> — 설정부터 수익 창출까지",
        "item3": "<strong>배포 설정</strong> — Docker, CI/CD, 모니터링",
        "item4": "<strong>CLI 도구</strong> — 등록, 테스트, 에이전트 관리",
        "quickstart": "빠른 시작",
        "step1": "킷 압축 해제",
        "step2": "<code>pip install -r requirements.txt</code> 실행",
        "step3": "가이드 1장 따라하기",
        "step4": "15분 만에 첫 에이전트 가동 가능",
        "questions": "질문이 있으신가요? 이 이메일에 답장하세요 — 모든 메시지를 읽습니다.",
        "footer": "AgenticTrade 스타터 킷을 다운로드하셔서 이 이메일을 받으셨습니다.",
    },
    "ja": {
        "subject": "AgenticTrade スターターキットの準備ができました",
        "title": "AgenticTrade へようこそ",
        "ready": "スターターキットのダウンロード準備ができました：",
        "btn": "スターターキットをダウンロード",
        "inside": "キットの内容",
        "item1": "<strong>4つのエージェントテンプレート</strong> — デプロイ可能なマーケットプレイスエージェント",
        "item2": "<strong>13章ガイド</strong> — セットアップから収益化まで",
        "item3": "<strong>デプロイ設定</strong> — Docker、CI/CD、モニタリング",
        "item4": "<strong>CLIツール</strong> — 登録、テスト、エージェント管理",
        "quickstart": "クイックスタート",
        "step1": "キットを解凍",
        "step2": "<code>pip install -r requirements.txt</code> を実行",
        "step3": "ガイドの第1章に従う",
        "step4": "15分で最初のエージェントを稼働可能",
        "questions": "ご質問はありますか？このメールに返信してください — すべてのメッセージを読んでいます。",
        "footer": "AgenticTrade スターターキットをダウンロードされたため、このメールをお送りしています。",
    },
    "fr": {
        "subject": "Votre Starter Kit AgenticTrade est prêt",
        "title": "Bienvenue sur AgenticTrade",
        "ready": "Votre Starter Kit est prêt à télécharger :",
        "btn": "Télécharger le Starter Kit",
        "inside": "Contenu du kit",
        "item1": "<strong>4 templates d'agents</strong> — agents marketplace prêts à déployer",
        "item2": "<strong>Guide en 13 chapitres</strong> — de la configuration aux revenus",
        "item3": "<strong>Configs de déploiement</strong> — Docker, CI/CD, monitoring",
        "item4": "<strong>Outils CLI</strong> — enregistrer, tester et gérer les agents",
        "quickstart": "Démarrage rapide",
        "step1": "Décompressez le kit",
        "step2": "Exécutez <code>pip install -r requirements.txt</code>",
        "step3": "Suivez le chapitre 1 du guide",
        "step4": "Votre premier agent peut être en ligne en 15 minutes",
        "questions": "Des questions ? Répondez à cet email — nous lisons chaque message.",
        "footer": "Vous recevez cet email car vous avez téléchargé le Starter Kit AgenticTrade.",
    },
    "de": {
        "subject": "Ihr AgenticTrade Starter Kit ist bereit",
        "title": "Willkommen bei AgenticTrade",
        "ready": "Ihr Starter Kit steht zum Download bereit:",
        "btn": "Starter Kit herunterladen",
        "inside": "Inhalt des Kits",
        "item1": "<strong>4 Agent-Templates</strong> — einsatzbereite Marketplace-Agenten",
        "item2": "<strong>13-Kapitel-Leitfaden</strong> — vom Setup bis zum Umsatz",
        "item3": "<strong>Deploy-Konfigurationen</strong> — Docker, CI/CD, Monitoring",
        "item4": "<strong>CLI-Tools</strong> — Registrierung, Test und Agent-Verwaltung",
        "quickstart": "Schnellstart",
        "step1": "Kit entpacken",
        "step2": "<code>pip install -r requirements.txt</code> ausführen",
        "step3": "Kapitel 1 des Leitfadens folgen",
        "step4": "Ihr erster Agent kann in 15 Minuten live sein",
        "questions": "Fragen? Antworten Sie auf diese E-Mail — wir lesen jede Nachricht.",
        "footer": "Sie erhalten diese E-Mail, weil Sie das AgenticTrade Starter Kit heruntergeladen haben.",
    },
    "ru": {
        "subject": "Ваш стартовый набор AgenticTrade готов",
        "title": "Добро пожаловать в AgenticTrade",
        "ready": "Ваш стартовый набор готов к загрузке:",
        "btn": "Скачать стартовый набор",
        "inside": "Что внутри",
        "item1": "<strong>4 шаблона агентов</strong> — готовые к развёртыванию агенты маркетплейса",
        "item2": "<strong>Руководство из 13 глав</strong> — от настройки до получения дохода",
        "item3": "<strong>Конфигурации деплоя</strong> — Docker, CI/CD, мониторинг",
        "item4": "<strong>CLI-инструменты</strong> — регистрация, тестирование, управление агентами",
        "quickstart": "Быстрый старт",
        "step1": "Распакуйте архив",
        "step2": "Запустите <code>pip install -r requirements.txt</code>",
        "step3": "Следуйте главе 1 руководства",
        "step4": "Ваш первый агент может заработать через 15 минут",
        "questions": "Есть вопросы? Ответьте на это письмо — мы читаем каждое сообщение.",
        "footer": "Вы получили это письмо, потому что скачали стартовый набор AgenticTrade.",
    },
    "es": {
        "subject": "Tu Kit de Inicio AgenticTrade está listo",
        "title": "Bienvenido a AgenticTrade",
        "ready": "Tu Kit de Inicio está listo para descargar:",
        "btn": "Descargar Kit de Inicio",
        "inside": "Qué incluye",
        "item1": "<strong>4 plantillas de agentes</strong> — agentes de marketplace listos para desplegar",
        "item2": "<strong>Guía de 13 capítulos</strong> — desde la configuración hasta los ingresos",
        "item3": "<strong>Configs de despliegue</strong> — Docker, CI/CD, monitoreo",
        "item4": "<strong>Herramientas CLI</strong> — registrar, probar y gestionar agentes",
        "quickstart": "Inicio rápido",
        "step1": "Descomprime el kit",
        "step2": "Ejecuta <code>pip install -r requirements.txt</code>",
        "step3": "Sigue el capítulo 1 de la guía",
        "step4": "Tu primer agente puede estar en línea en 15 minutos",
        "questions": "¿Preguntas? Responde a este correo — leemos cada mensaje.",
        "footer": "Recibes este correo porque descargaste el Kit de Inicio AgenticTrade.",
    },
    "pt": {
        "subject": "Seu Kit Inicial AgenticTrade está pronto",
        "title": "Bem-vindo ao AgenticTrade",
        "ready": "Seu Kit Inicial está pronto para download:",
        "btn": "Baixar Kit Inicial",
        "inside": "O que está incluído",
        "item1": "<strong>4 templates de agentes</strong> — agentes de marketplace prontos para deploy",
        "item2": "<strong>Guia de 13 capítulos</strong> — da configuração à receita",
        "item3": "<strong>Configs de deploy</strong> — Docker, CI/CD, monitoramento",
        "item4": "<strong>Ferramentas CLI</strong> — registrar, testar e gerenciar agentes",
        "quickstart": "Início rápido",
        "step1": "Descompacte o kit",
        "step2": "Execute <code>pip install -r requirements.txt</code>",
        "step3": "Siga o capítulo 1 do guia",
        "step4": "Seu primeiro agente pode estar online em 15 minutos",
        "questions": "Dúvidas? Responda este email — lemos cada mensagem.",
        "footer": "Você recebeu este email porque baixou o Kit Inicial AgenticTrade.",
    },
}


def _welcome_subject(locale: str = "en") -> str:
    """Return localized welcome email subject."""
    content = _WELCOME_CONTENT.get(locale, _WELCOME_CONTENT["en"])
    return content["subject"]


def _welcome_email_html(download_url: str, locale: str = "en") -> str:
    """Generate welcome email HTML in the subscriber's language."""
    c = _WELCOME_CONTENT.get(locale, _WELCOME_CONTENT["en"])
    return f"""<!DOCTYPE html>
<html lang="{locale}">
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">

<h1 style="color: #1a1a2e; font-size: 24px;">{c['title']}</h1>

<p>{c['ready']}</p>

<p style="margin: 24px 0;">
  <a href="{download_url}"
     style="background: #6C63FF; color: white; padding: 12px 24px;
            border-radius: 6px; text-decoration: none; font-weight: 600;">
    {c['btn']}
  </a>
</p>

<h2 style="font-size: 18px; margin-top: 32px;">{c['inside']}</h2>
<ul style="line-height: 1.8;">
  <li>{c['item1']}</li>
  <li>{c['item2']}</li>
  <li>{c['item3']}</li>
  <li>{c['item4']}</li>
</ul>

<h2 style="font-size: 18px; margin-top: 32px;">{c['quickstart']}</h2>
<ol style="line-height: 1.8;">
  <li>{c['step1']}</li>
  <li>{c['step2']}</li>
  <li>{c['step3']}</li>
  <li>{c['step4']}</li>
</ol>

<p style="margin-top: 32px; color: #666; font-size: 14px;">
  {c['questions']}<br>
  <a href="https://agentictrade.io" style="color: #6C63FF;">agentictrade.io</a>
</p>

<hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">
<p style="color: #999; font-size: 12px;">
  {c['footer']}
</p>

</body>
</html>"""
