"""
Agent Commerce Framework — API Server
FastAPI application with x402 payment support, agent identity,
reputation engine, enhanced discovery, and team management.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from marketplace.db import Database
from marketplace.registry import ServiceRegistry
from marketplace.auth import APIKeyManager
from marketplace.proxy import PaymentProxy
from marketplace.commission import CommissionEngine
from marketplace.settlement import SettlementEngine
from marketplace.payment import PaymentConfig, setup_x402_middleware
from marketplace.wallet import WalletConfig, WalletManager
from marketplace.identity import IdentityManager
from marketplace.reputation import ReputationEngine
from marketplace.discovery import DiscoveryEngine
from marketplace.rate_limit import RateLimiter, create_rate_limiter
from marketplace.webhooks import WebhookManager
from marketplace.audit import AuditLogger
from marketplace.i18n import detect_locale, make_translator, SUPPORTED_LOCALES
from marketplace.provider_auth import ensure_provider_accounts_table, ensure_pat_table
from marketplace.compliance import log_compliance_results
from marketplace.agent_provider import AgentProviderManager
from marketplace.escrow import EscrowManager
from marketplace.report import ReportManager
from marketplace.service_review import ServiceReviewEngine
from payments.x402_provider import X402Provider
from payments.nowpayments_provider import NOWPaymentsProvider
from payments.paypal_provider import PayPalProvider
from payments.agentkit_provider import AgentKitProvider
from payments.router import PaymentRouter
from .routes import services, health, proxy
from .routes import auth as auth_routes
from .routes import settlement as settlement_routes
from .routes import identity as identity_routes
from .routes import reputation as reputation_routes
from .routes import discovery as discovery_routes
from .routes import teams as team_routes
from .routes import webhooks as webhook_routes
from .routes import admin as admin_routes
from .routes import audit as audit_routes
from .routes import dashboard as dashboard_routes
from .routes import billing as billing_routes
from .routes import provider as provider_routes
from .routes import email as email_routes
from .routes import referral as referral_routes
from .routes import portal as portal_routes
from .routes import agent_provider as agent_provider_routes
from .routes import escrow as escrow_routes
from .routes import sla as sla_routes
from .routes import service_report as service_report_routes
from .routes import batch as batch_routes
from .routes import financial_export as financial_export_routes
from .routes import legal as legal_routes
from .routes import adcp as adcp_routes
from .routes import agents as agents_routes
from .routes import mcp_endpoint as mcp_routes
from .routes import service_gateway as gateway_routes
from .routes import negotiation as negotiation_routes
from .routes import budget as budget_routes

logger = logging.getLogger("acf")

# --- App ---

app = FastAPI(
    title="Agent Commerce Framework",
    description=(
        "Agent-to-Agent Marketplace API — register AI agents as service providers, "
        "manage identities, reputation scoring, escrow payments (USDC), "
        "webhook notifications, and team collaboration.\n\n"
        "**Auth**: Bearer token (`key_id:secret`). "
        "Brute-force protected: 5 failures/min per IP → 429.\n\n"
        "**Payments**: Tiered escrow (<$1=1d, <$100=3d, $100+=7d hold). "
        "Structured disputes with evidence, tiered timeouts (<$1=24h, <$100=72h, $100+=7d), "
        "provider counter-response, and admin arbitration.\n\n"
        "**Commission**: 0% (month 1) → 5% (months 2-3) → 10% (month 4+). "
        "Quality rewards: Verified (≥80 health) = 8%, Premium (≥95) = 6%.\n\n"
        "**Webhooks**: HMAC-SHA256 signed, 8 event types, retry with exponential backoff."
    ),
    version="0.7.1",
    docs_url=None if os.environ.get("ACF_DISABLE_DOCS") else "/docs",
    redoc_url=None if os.environ.get("ACF_DISABLE_DOCS") else "/redoc",
)

_cors_env = os.environ.get("CORS_ORIGINS", "")
if not _cors_env or _cors_env == "*":
    logger.warning(
        "CORS_ORIGINS not set or wildcard — restricting to localhost only. "
        "Set CORS_ORIGINS explicitly for production."
    )
    _cors_origins = ["http://localhost:3000"]
    _cors_all = False
else:
    _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
    _cors_all = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID", "Accept"],
)

# GZip compression — ~75% size reduction on HTML/CSS/JSON responses
app.add_middleware(GZipMiddleware, minimum_size=500)

# ---------------------------------------------------------------------------
# Component factory — all shared state is created via _init_components() so
# that (a) initialization errors are caught in a single place, (b) tests can
# call the factory with overrides, and (c) module import doesn't trigger
# heavyweight side effects like DB connections or payment provider setup.
# ---------------------------------------------------------------------------


def _build_payment_providers(wallet_manager: WalletManager) -> dict[str, object]:
    """Discover and instantiate available payment providers."""
    providers: dict[str, object] = {}
    try:
        _x402 = X402Provider()
        if _x402.config.enabled:
            providers["x402"] = _x402
            logger.info("x402 payment provider registered")
    except Exception:
        logger.info("x402 provider not configured")

    if os.environ.get("NOWPAYMENTS_API_KEY"):
        try:
            providers["nowpayments"] = NOWPaymentsProvider()
            logger.info("NOWPayments provider registered")
        except Exception:
            logger.info("NOWPayments provider not configured")

    if os.environ.get("PAYPAL_CLIENT_ID"):
        try:
            mode = "live" if os.environ.get("PAYPAL_MODE") == "live" else "sandbox"
            providers["paypal"] = PayPalProvider(mode=mode)
            logger.info("PayPal provider registered (%s)", mode)
        except Exception:
            logger.info("PayPal provider not configured")

    if wallet_manager.is_ready:
        try:
            providers["agentkit"] = AgentKitProvider(wallet_manager=wallet_manager)
            logger.info("AgentKit payment provider registered")
        except Exception:
            logger.info("AgentKit provider not configured")

    # Circle Nanopayments (x402 on Arc — gas-free USDC micropayments)
    nano_url = os.environ.get("NANO_GATEWAY_URL", "")
    if nano_url:
        try:
            from payments.nanopayments_provider import NanopaymentsProvider
            _nano = NanopaymentsProvider(gateway_url=nano_url)
            if _nano.is_configured:
                providers["nanopayments"] = _nano
                logger.info("Circle Nanopayments provider registered (Arc)")
            else:
                logger.info("Nanopayments gateway not reachable at %s", nano_url)
        except Exception:
            logger.info("Nanopayments provider not configured")

    return providers


def _init_components(target_app: FastAPI | None = None) -> dict:
    """Create all shared components and return them as a dict.

    When *target_app* is provided, x402 middleware is attached (must happen
    before the first request, per Starlette).  Separating construction from
    the module top-level makes the app testable and avoids import-time
    side effects.
    """
    db = Database(os.environ.get("DATABASE_PATH"))
    registry = ServiceRegistry(db)
    key_manager = APIKeyManager(db)

    platform_fee = Decimal(os.environ.get("PLATFORM_FEE_PCT", "0.10"))

    wallet_config = WalletConfig.from_env()
    wallet_manager = WalletManager(wallet_config)

    _payment_providers = _build_payment_providers(wallet_manager)
    payment_router_obj = PaymentRouter(_payment_providers) if _payment_providers else None

    webhook_manager = WebhookManager(db)
    audit_logger = AuditLogger(db)
    commission_engine = CommissionEngine(db)

    payment_proxy = PaymentProxy(
        db,
        platform_fee_pct=platform_fee,
        payment_router=payment_router_obj,
        webhook_manager=webhook_manager,
        commission_engine=commission_engine,
    )

    settlement_engine = SettlementEngine(
        db, platform_fee_pct=platform_fee, wallet_manager=wallet_manager,
        commission_engine=commission_engine,
    )

    identity_manager = IdentityManager(db)
    reputation_engine = ReputationEngine(db)
    discovery_engine = DiscoveryEngine(db, registry)

    agent_provider_mgr = AgentProviderManager(db)
    escrow_mgr = EscrowManager(db)
    report_mgr = ReportManager(db)
    service_review_engine = ServiceReviewEngine(db)

    _rl_backend = os.environ.get("RATE_LIMIT_BACKEND", "database")
    rate_limiter = create_rate_limiter(
        backend=_rl_backend,
        db=db if _rl_backend == "database" else None,
        rate=60,
        per=60.0,
        burst=120,
    )

    # x402 middleware must be attached before the first request (Starlette).
    payment_config = PaymentConfig.from_env()
    if target_app is not None and payment_config.enabled:
        _active_services = db.list_services(status="active")
        _x402_ok = setup_x402_middleware(target_app, payment_config, _active_services)
        if _x402_ok:
            logger.info("x402 payment middleware active")
        else:
            logger.info("x402 middleware not attached (no eligible services)")
    elif not payment_config.enabled:
        logger.info("Running without x402 payments (WALLET_ADDRESS not set)")

    return {
        "db": db, "registry": registry, "key_manager": key_manager,
        "platform_fee": platform_fee, "wallet_manager": wallet_manager,
        "payment_router_obj": payment_router_obj, "webhook_manager": webhook_manager,
        "audit_logger": audit_logger, "commission_engine": commission_engine,
        "payment_proxy": payment_proxy, "settlement_engine": settlement_engine,
        "identity_manager": identity_manager, "reputation_engine": reputation_engine,
        "discovery_engine": discovery_engine, "agent_provider_mgr": agent_provider_mgr,
        "escrow_mgr": escrow_mgr, "report_mgr": report_mgr,
        "service_review_engine": service_review_engine, "rate_limiter": rate_limiter,
        "payment_config": payment_config,
    }


# Initialise components — wrapped in factory for testability.
_components = _init_components(target_app=app)

# Expose as module-level names for backward compatibility with routes/middleware.
db = _components["db"]
registry = _components["registry"]
key_manager = _components["key_manager"]
platform_fee = _components["platform_fee"]
wallet_manager = _components["wallet_manager"]
payment_router_obj = _components["payment_router_obj"]
webhook_manager = _components["webhook_manager"]
audit_logger = _components["audit_logger"]
commission_engine = _components["commission_engine"]
payment_proxy = _components["payment_proxy"]
settlement_engine = _components["settlement_engine"]
identity_manager = _components["identity_manager"]
reputation_engine = _components["reputation_engine"]
discovery_engine = _components["discovery_engine"]
agent_provider_mgr = _components["agent_provider_mgr"]
escrow_mgr = _components["escrow_mgr"]
report_mgr = _components["report_mgr"]
service_review_engine = _components["service_review_engine"]
rate_limiter = _components["rate_limiter"]
payment_config = _components["payment_config"]


@app.on_event("startup")
async def startup():
    """Initialize database, shared state, and x402 middleware."""
    app.state.db = db
    app.state.registry = registry
    app.state.auth = key_manager
    app.state.proxy = payment_proxy
    app.state.settlement = settlement_engine
    app.state.wallet = wallet_manager
    app.state.payment_config = payment_config
    app.state.identity = identity_manager
    app.state.reputation = reputation_engine
    app.state.discovery = discovery_engine

    # MCP bridge server for HTTP endpoint
    try:
        from mcp_bridge.server import create_mcp_server
        app.state.mcp_server = create_mcp_server(db, registry)
    except Exception:
        app.state.mcp_server = None
    app.state.rate_limiter = rate_limiter
    app.state.payment_router = payment_router_obj
    app.state.webhooks = webhook_manager
    app.state.commission_engine = commission_engine
    app.state.audit = audit_logger
    app.state.agent_provider_mgr = agent_provider_mgr
    app.state.escrow_mgr = escrow_mgr
    app.state.report_mgr = report_mgr
    app.state.service_review_engine = service_review_engine

    # Ensure provider portal tables exist
    ensure_provider_accounts_table(db)
    ensure_pat_table(db)

    # Ensure promo code tables exist and seed default promos
    from marketplace.promo import ensure_promo_tables, seed_default_promos
    ensure_promo_tables(db)
    seed_default_promos(db)

    # Run compliance checks (non-blocking — logs warnings only)
    log_compliance_results()

    # Automated GDPR data minimization: anonymize IP addresses in audit
    # entries older than configured retention period (default 365 days).
    _retention_days = int(os.environ.get("ACF_AUDIT_RETENTION_DAYS", "365"))
    try:
        anonymized = audit_logger.anonymize_old_entries(_retention_days)
        if anonymized > 0:
            logger.info("Audit IP anonymization: %d entries processed", anonymized)
    except Exception as exc:
        logger.warning("Audit IP anonymization failed: %s", exc)

    # Recover stuck settlements (processing > 24h without completion)
    try:
        recovered = settlement_engine.recover_stuck_settlements(timeout_hours=24)
        if recovered:
            logger.info("Recovered %d stuck settlements", len(recovered))
    except Exception as exc:
        logger.warning("Settlement recovery check failed: %s", exc)

    # Retry failed settlements (up to 3 attempts per settlement)
    try:
        retried = settlement_engine.retry_failed_settlements(max_attempts=3)
        if retried:
            logger.info("Re-queued %d failed settlements for retry", len(retried))
    except Exception as exc:
        logger.warning("Settlement retry check failed: %s", exc)

    # x402 middleware is set up at module level (before app starts)
    # to comply with Starlette's restriction on adding middleware after startup

    # Log wallet status
    if wallet_manager.is_ready:
        logger.info("CDP wallet ready for settlements")
    else:
        logger.info("CDP wallet not configured — settlements logged only")


# --- Rate limiting middleware ---

@app.middleware("http")
async def i18n_middleware(request: Request, call_next):
    """Detect locale and attach translator to request state."""
    query_lang = request.query_params.get("lang")
    cookie_lang = request.cookies.get("lang")
    accept_lang = request.headers.get("accept-language")
    locale = detect_locale(query_lang, cookie_lang, accept_lang)
    request.state.locale = locale
    request.state.t = make_translator(locale)
    request.state.supported_locales = SUPPORTED_LOCALES
    response = await call_next(request)
    if query_lang and query_lang.lower() in SUPPORTED_LOCALES:
        response.set_cookie("lang", locale, max_age=365 * 24 * 3600, samesite="lax")
    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    # Cache static assets for 7 days
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=604800, immutable"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://rsms.me; "
        "font-src 'self' https://rsms.me; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Apply rate limiting based on client IP with proper headers.

    Uses ``db.arun()`` for DB-backed rate limiters to avoid blocking
    the asyncio event loop on every request.
    """
    client_ip = request.client.host if request.client else "unknown"
    from marketplace.rate_limit import DatabaseRateLimiter
    if isinstance(rate_limiter, DatabaseRateLimiter):
        allowed = await db.arun(rate_limiter.allow, client_ip)
    else:
        allowed = rate_limiter.allow(client_ip)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Try again later."},
            headers={"Retry-After": "60"},
        )
    return await call_next(request)


# --- Global exception handler ---

_error_templates_dir = Path(__file__).resolve().parent.parent / "templates"
_error_templates = None


def _get_error_templates():
    global _error_templates
    if _error_templates is None:
        from fastapi.templating import Jinja2Templates
        _error_templates = Jinja2Templates(directory=str(_error_templates_dir))
    return _error_templates


def _build_error_ctx(request: Request) -> dict:
    """Build minimal template context for error pages."""
    from marketplace.i18n import SUPPORTED_LOCALES
    return {
        "request": request,
        "t": getattr(request.state, "t", lambda k: k),
        "locale": getattr(request.state, "locale", "en"),
        "supported_locales": SUPPORTED_LOCALES,
    }


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Custom branded error pages for 404 and other HTTP errors."""
    if exc.status_code == 404:
        # Return HTML 404 for browser requests, JSON for API
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            tpl = _get_error_templates()
            ctx = _build_error_ctx(request)
            return tpl.TemplateResponse("public/404.html", ctx, status_code=404)
        return JSONResponse(status_code=404, content={"error": "Not found"})
    # Other HTTP errors
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail or "Error"},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions — return branded 500 for browsers, JSON for API."""
    import traceback
    logger.error("Unhandled exception: %s\n%s", exc, traceback.format_exc())
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        try:
            tpl = _get_error_templates()
            ctx = _build_error_ctx(request)
            return tpl.TemplateResponse("public/500.html", ctx, status_code=500)
        except Exception:
            pass
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


# --- Mount routes ---

app.include_router(health.router)
app.include_router(services.router, prefix="/api/v1")
app.include_router(proxy.router, prefix="/api/v1")
app.include_router(auth_routes.router, prefix="/api/v1")
app.include_router(settlement_routes.router, prefix="/api/v1")
app.include_router(identity_routes.router, prefix="/api/v1")
app.include_router(reputation_routes.router, prefix="/api/v1")
app.include_router(discovery_routes.router, prefix="/api/v1")
app.include_router(team_routes.router, prefix="/api/v1")
app.include_router(webhook_routes.router, prefix="/api/v1")
app.include_router(admin_routes.router, prefix="/api/v1")
app.include_router(audit_routes.router, prefix="/api/v1")
app.include_router(provider_routes.router, prefix="/api/v1")
app.include_router(dashboard_routes.router)
app.include_router(billing_routes.router)
app.include_router(email_routes.router)
app.include_router(referral_routes.router, prefix="/api/v1")
app.include_router(portal_routes.router)
app.include_router(agent_provider_routes.router, prefix="/api/v1")
app.include_router(escrow_routes.router, prefix="/api/v1")
app.include_router(sla_routes.router, prefix="/api/v1")
app.include_router(service_report_routes.router, prefix="/api/v1")
app.include_router(batch_routes.router, prefix="/api/v1")
app.include_router(financial_export_routes.router, prefix="/api/v1")
app.include_router(legal_routes.router, prefix="/api/v1")
app.include_router(adcp_routes.router)
app.include_router(agents_routes.router, prefix="/api/v1")
app.include_router(mcp_routes.router, prefix="/api/v1")
app.include_router(gateway_routes.router)
app.include_router(negotiation_routes.router, prefix="/api/v1")
app.include_router(budget_routes.router, prefix="/api/v1")


@app.get("/api/health")
@app.get("/api/v1/health")
async def api_health(detail: bool = False):
    """API health check endpoint for monitoring and load balancers.

    Pass ?detail=true to include platform-wide service quality summary.
    """
    from marketplace.health_monitor import HealthMonitor

    result: dict = {"status": "ok", "service": "agentictrade", "version": "0.7.1"}

    if detail:
        try:
            monitor = HealthMonitor(db)
            result["platform"] = monitor.get_platform_health_summary()
        except Exception as exc:
            logger.warning("Platform health summary failed: %s", exc)
            result["platform"] = {"error": "unavailable"}

    return result


# --- Static files ---
_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
