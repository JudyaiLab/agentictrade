"""Health check, landing page, and SEO endpoints."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from marketplace.i18n import SUPPORTED_LOCALES

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _i18n_ctx(request: Request) -> dict:
    """Build i18n template context from request state (set by middleware)."""
    return {
        "t": getattr(request.state, "t", lambda k: k),
        "locale": getattr(request.state, "locale", "en"),
        "supported_locales": SUPPORTED_LOCALES,
    }

_BASE_URL = "https://agentictrade.io"


# ── SEO Infrastructure (Phase 0) ──────────────────────────────────


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    """Serve robots.txt — allow public pages, block dashboard and API.
    Explicitly welcome AI crawlers for AEO/GEO optimization."""
    return (
        "# AI Crawlers — welcome\n"
        "User-agent: GPTBot\n"
        "Allow: /\n"
        "Allow: /llms.txt\n"
        "Allow: /llms-full.txt\n"
        "Allow: /.well-known/\n"
        "\n"
        "User-agent: ChatGPT-User\n"
        "Allow: /\n"
        "\n"
        "User-agent: Google-Extended\n"
        "Allow: /\n"
        "\n"
        "User-agent: Anthropic-AI\n"
        "Allow: /\n"
        "\n"
        "User-agent: PerplexityBot\n"
        "Allow: /\n"
        "\n"
        "User-agent: ClaudeBot\n"
        "Allow: /\n"
        "\n"
        "# General crawlers\n"
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /marketplace\n"
        "Allow: /api-docs\n"
        "Allow: /docs\n"
        "Allow: /pricing\n"
        "Allow: /providers\n"
        "Allow: /about\n"
        "Allow: /terms\n"
        "Allow: /privacy\n"
        "Allow: /quality-policy\n"
        "Allow: /faq\n"
        "Allow: /use-cases/\n"
        "Allow: /status\n"
        "Allow: /llms.txt\n"
        "Allow: /llms-full.txt\n"
        "Allow: /.well-known/\n"
        "Disallow: /portal/\n"
        "Disallow: /dashboard/\n"
        "Disallow: /api/v1/\n"
        "Disallow: /checkout/\n"
        "\n"
        f"Sitemap: {_BASE_URL}/sitemap.xml\n"
    )


@router.get("/sitemap.xml", response_class=Response)
async def sitemap_xml():
    """Auto-generate sitemap with all public pages."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    pages = [
        {"loc": "/", "priority": "1.0", "changefreq": "weekly"},
        {"loc": "/marketplace", "priority": "0.9", "changefreq": "daily"},
        {"loc": "/faq", "priority": "0.8", "changefreq": "weekly"},
        {"loc": "/api-docs", "priority": "0.8", "changefreq": "weekly"},
        {"loc": "/pricing", "priority": "0.8", "changefreq": "monthly"},
        {"loc": "/providers", "priority": "0.8", "changefreq": "monthly"},
        {"loc": "/about", "priority": "0.6", "changefreq": "monthly"},
        {"loc": "/terms", "priority": "0.3", "changefreq": "monthly"},
        {"loc": "/privacy", "priority": "0.3", "changefreq": "monthly"},
        {"loc": "/quality-policy", "priority": "0.5", "changefreq": "monthly"},
        {"loc": "/status", "priority": "0.4", "changefreq": "daily"},
        {"loc": "/llms.txt", "priority": "0.5", "changefreq": "weekly"},
        {"loc": "/llms-full.txt", "priority": "0.6", "changefreq": "weekly"},
        {"loc": "/portal/register", "priority": "0.7", "changefreq": "monthly"},
        {"loc": "/portal/login", "priority": "0.5", "changefreq": "monthly"},
        # Use case pages
        {"loc": "/use-cases/trading-agents", "priority": "0.7", "changefreq": "monthly"},
        {"loc": "/use-cases/content-agents", "priority": "0.7", "changefreq": "monthly"},
        {"loc": "/use-cases/research-agents", "priority": "0.7", "changefreq": "monthly"},
        {"loc": "/use-cases/customer-support-agents", "priority": "0.7", "changefreq": "monthly"},
        {"loc": "/use-cases/data-pipeline-agents", "priority": "0.7", "changefreq": "monthly"},
        # Documentation sub-pages
        {"loc": "/docs/quickstart", "priority": "0.8", "changefreq": "monthly"},
        {"loc": "/docs/authentication", "priority": "0.7", "changefreq": "monthly"},
        {"loc": "/docs/discovery", "priority": "0.7", "changefreq": "monthly"},
        {"loc": "/docs/payments", "priority": "0.7", "changefreq": "monthly"},
        {"loc": "/docs/mcp", "priority": "0.7", "changefreq": "monthly"},
        {"loc": "/docs/sdk", "priority": "0.7", "changefreq": "monthly"},
        # Category pages
        {"loc": "/marketplace/category/ai", "priority": "0.7", "changefreq": "weekly"},
        {"loc": "/marketplace/category/crypto", "priority": "0.7", "changefreq": "weekly"},
        {"loc": "/marketplace/category/data", "priority": "0.7", "changefreq": "weekly"},
        {"loc": "/marketplace/category/code", "priority": "0.7", "changefreq": "weekly"},
        {"loc": "/marketplace/category/content", "priority": "0.7", "changefreq": "weekly"},
        {"loc": "/marketplace/category/finance", "priority": "0.7", "changefreq": "weekly"},
        {"loc": "/marketplace/category/security", "priority": "0.7", "changefreq": "weekly"},
        {"loc": "/marketplace/category/productivity", "priority": "0.7", "changefreq": "weekly"},
    ]

    # Dynamically add individual service detail pages
    try:
        from api.main import db as _db
        active_svcs = _db.list_services(status="active")
        for svc in active_svcs:
            pages.append({
                "loc": f"/marketplace/{svc['id']}",
                "priority": "0.6",
                "changefreq": "weekly",
            })
    except Exception:
        pass

    # Pages with multilingual support get hreflang alternates
    i18n_pages = {"/", "/marketplace", "/pricing", "/providers", "/about",
                  "/portal/register", "/portal/login"}
    hreflang_map = [
        ("en", "en"), ("zh-Hant-TW", "zh-TW"), ("ko", "ko"),
        ("ja", "ja"), ("fr", "fr"), ("de", "de"),
        ("ru", "ru"), ("es", "es"), ("pt", "pt"),
    ]

    urls = []
    for page in pages:
        loc = page["loc"]
        alt_links = ""
        if loc in i18n_pages:
            for hl, lang_param in hreflang_map:
                sep = "&" if "?" in loc else "?"
                alt_links += (
                    f'    <xhtml:link rel="alternate" hreflang="{hl}" '
                    f'href="{_BASE_URL}{loc}{sep}lang={lang_param}"/>\n'
                )
            alt_links += (
                f'    <xhtml:link rel="alternate" hreflang="x-default" '
                f'href="{_BASE_URL}{loc}"/>\n'
            )
        urls.append(
            f"  <url>\n"
            f"    <loc>{_BASE_URL}{loc}</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            f"    <changefreq>{page['changefreq']}</changefreq>\n"
            f"    <priority>{page['priority']}</priority>\n"
            f"{alt_links}"
            f"  </url>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
        '        xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )

    return Response(content=xml, media_type="application/xml")


@router.get("/llms.txt", response_class=PlainTextResponse)
@router.get("/.well-known/llms.txt", response_class=PlainTextResponse)
async def llms_txt(request: Request):
    """GEO: Tell LLMs what AgenticTrade is. Official llms.txt spec format.
    Includes live platform metrics for AI citation accuracy."""
    # Pull live metrics for citation
    db = getattr(request.app.state, "db", None)
    svc_count = agent_count = total_calls = 0
    if db is not None:
        try:
            with db.connect() as conn:
                svc_count = conn.execute(
                    "SELECT COUNT(*) as c FROM services WHERE status='active'"
                ).fetchone()["c"]
                agent_count = conn.execute(
                    "SELECT COUNT(*) as c FROM agent_identities"
                ).fetchone()["c"]
                total_calls = conn.execute(
                    "SELECT COUNT(*) as c FROM usage_records"
                ).fetchone()["c"]
        except Exception:
            pass

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return (
        "# AgenticTrade\n"
        "\n"
        "> AgenticTrade is an AI service marketplace where providers list services\n"
        "> and AI agents automatically discover, use, and pay for them.\n"
        "> Multi-rail payments (x402 USDC on Base, PayPal, 300+ cryptocurrencies),\n"
        "> 24/7 automated quality monitoring, MCP integration for native LLM access,\n"
        "> and a self-service provider portal. Open source (MIT), 9 languages.\n"
        "> Built by JudyAI Lab.\n"
        "\n"
        f"## Platform Statistics (as of {today})\n"
        f"- Active services: {svc_count}\n"
        f"- Registered AI agents: {agent_count}\n"
        f"- Total API calls processed: {total_calls:,}\n"
        f"- Supported languages: 9 (EN, ZH-TW, KO, JA, FR, DE, RU, ES, PT)\n"
        f"- Payment rails: 3 (x402/USDC, PayPal, NOWPayments/300+ crypto)\n"
        f"- Commission: 0% month 1, 5% months 2-3, 10% cap\n"
        f"- Quality monitoring: 24/7 automated health patrol\n"
        f"- License: MIT open source\n"
        "\n"
        "## Key Concepts\n"
        "- MCP Tool Descriptor: machine-readable API description for LLM tool use\n"
        "- Proxy Key: credential routing so provider API keys are never exposed\n"
        "- Provider Agent: lightweight HTTP server providers run locally\n"
        "- Prompt-as-API: turn a system prompt into a paid API without writing code\n"
        "- x402: HTTP-native payment protocol for per-call USDC micropayments on Base\n"
        "- Health Score: 0-100 quality rating based on uptime, response time, error rate\n"
        "\n"
        f"## For a detailed reference, see: [{_BASE_URL}/llms-full.txt]({_BASE_URL}/llms-full.txt)\n"
        "\n"
        f"## Getting Started\n"
        f"- [Home]({_BASE_URL}/): Platform overview\n"
        f"- [Getting Started]({_BASE_URL}/docs/getting-started): Quick start guide\n"
        f"- [API Documentation]({_BASE_URL}/api-docs): Full endpoint reference\n"
        f"- [Pricing]({_BASE_URL}/pricing): Fee structure and quality rewards\n"
        "\n"
        f"## For Service Providers — Make Money with AI\n"
        f"- [Sell Services]({_BASE_URL}/providers): List your service in minutes\n"
        f"- [Provider Portal]({_BASE_URL}/portal): Self-service dashboard\n"
        f"- [Payments & Settlement]({_BASE_URL}/docs/payments): USDC, PayPal, crypto payouts\n"
        f"- [FAQ]({_BASE_URL}/faq): Common questions about earning with AI agents\n"
        "\n"
        f"## Use Cases\n"
        f"- [AI Trading Agents]({_BASE_URL}/use-cases/trading-agents): Crypto analysis, backtesting, signals\n"
        f"- [AI Content Agents]({_BASE_URL}/use-cases/content-agents): Writing, translation, summarization\n"
        f"- [AI Research Agents]({_BASE_URL}/use-cases/research-agents): Web search, data mining, analysis\n"
        f"- [AI Support Agents]({_BASE_URL}/use-cases/customer-support-agents): Ticket routing, FAQ, sentiment\n"
        f"- [AI Data Pipeline Agents]({_BASE_URL}/use-cases/data-pipeline-agents): ETL, cleaning, validation\n"
        "\n"
        f"## For AI Agents & Developers\n"
        f"- [API Reference]({_BASE_URL}/docs/api-reference): Full endpoint documentation\n"
        f"- [Service Discovery]({_BASE_URL}/marketplace): Browse and search services\n"
        f"- [Agent Playbook]({_BASE_URL}/api/v1/agent-playbook): Machine-readable integration guide\n"
        f"- [MCP Install](https://clawhub.ai/s/agentictrade): One-click install for Claude Code\n"
        f"\n"
        f"## Agent Discovery Endpoints\n"
        f"- MCP: {_BASE_URL}/.well-known/mcp.json\n"
        f"- OpenAI Plugin: {_BASE_URL}/.well-known/ai-plugin.json\n"
        f"- ACDP: {_BASE_URL}/.well-known/agents.json\n"
        f"- OpenAPI: {_BASE_URL}/api/v1/openapi.json\n"
        f"- Self-Register: POST {_BASE_URL}/api/v1/agents/onboard (no auth required)\n"
        "\n"
        f"## Trust & Compliance\n"
        f"- [About]({_BASE_URL}/about): Mission and team\n"
        f"- [Terms]({_BASE_URL}/terms) | [Privacy]({_BASE_URL}/privacy) | [Quality Policy]({_BASE_URL}/quality-policy) | [Status]({_BASE_URL}/status)\n"
        f"- [GitHub](https://github.com/JudyaiLab/agentictrade): Full source code\n"
        "\n"
    )


@router.get("/llms-full.txt", response_class=PlainTextResponse)
@router.get("/.well-known/llms-full.txt", response_class=PlainTextResponse)
async def llms_full_txt(request: Request):
    """GEO: Detailed platform reference for LLMs. Complements llms.txt summary."""
    db = getattr(request.app.state, "db", None)
    svc_count = agent_count = total_calls = 0
    if db is not None:
        try:
            with db.connect() as conn:
                svc_count = conn.execute(
                    "SELECT COUNT(*) as c FROM services WHERE status='active'"
                ).fetchone()["c"]
                agent_count = conn.execute(
                    "SELECT COUNT(*) as c FROM agent_identities"
                ).fetchone()["c"]
                total_calls = conn.execute(
                    "SELECT COUNT(*) as c FROM usage_records"
                ).fetchone()["c"]
        except Exception:
            pass

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return (
        "# AgenticTrade — Full Reference\n"
        f"Last updated: {today}\n"
        "\n"
        "## What Is AgenticTrade?\n"
        "\n"
        "AgenticTrade is an AI service marketplace built by JudyAI Lab. People who\n"
        "build useful AI services list them here, and AI agents automatically discover,\n"
        "use, and pay for those services. Think of it as a marketplace where AI agents\n"
        "are the customers.\n"
        "\n"
        "Two audiences:\n"
        "1. **Service Providers** — People who have useful services (data analysis,\n"
        "   translation, image processing, etc.) and want AI agents to pay for them.\n"
        "2. **Agent Builders** — Developers building AI agents that need external\n"
        "   capabilities like market data, code execution, or content generation.\n"
        "\n"
        f"## Platform Statistics\n"
        f"- Active services: {svc_count}\n"
        f"- Registered AI agents: {agent_count}\n"
        f"- Total API calls: {total_calls:,}\n"
        f"- Languages: 9 (English, Traditional Chinese, Korean, Japanese, French,\n"
        f"  German, Russian, Spanish, Portuguese)\n"
        f"- License: MIT open source\n"
        f"- Source: https://github.com/JudyaiLab/agentictrade\n"
        "\n"
        "## How It Works\n"
        "\n"
        "### For Service Providers\n"
        "1. Register at https://agentictrade.io/portal/register\n"
        "2. List your service — either bring an existing HTTP API endpoint, or use\n"
        "   Prompt-as-API to turn a system prompt into a paid service (no code).\n"
        "3. Set your price per API call (in USDC).\n"
        "4. AI agents find your service, use it, and you get paid automatically.\n"
        "\n"
        "### For Agent Builders\n"
        "1. Search the marketplace: GET /api/v1/discover?q=your_need\n"
        "2. Find services that match, check pricing and quality scores.\n"
        "3. Call services through the proxy: POST /api/v1/proxy/{service_id}\n"
        "4. Payment happens automatically per call in USDC.\n"
        "\n"
        "### For AI Agents (Self-Service)\n"
        "1. Fetch the agent playbook: GET /api/v1/agent-playbook (no auth)\n"
        "2. Self-register: POST /api/v1/agents/onboard (no auth)\n"
        "3. Receive API credentials immediately.\n"
        "4. Discover and consume services autonomously.\n"
        "\n"
        "## Pricing\n"
        "\n"
        "- Free to join for both providers and agents.\n"
        "- Providers set their own per-call prices.\n"
        "- Platform commission: 0% month 1, 5% months 2-3, 10% from month 4 (capped).\n"
        "- Quality Rewards: providers with health score 80+ pay 8%, score 95+ pay 6%.\n"
        "- Referral Program: earn 20% of platform fees from referred users, permanently.\n"
        "\n"
        "## Payment Methods\n"
        "\n"
        "Three payment rails:\n"
        "1. **x402** — USDC on Base blockchain. Fully agent-native, per-call settlement.\n"
        "2. **PayPal** — Fiat credit cards, bank transfers, PayPal balance.\n"
        "3. **NOWPayments** — 300+ cryptocurrencies, auto-converted to USDC.\n"
        "\n"
        "Providers choose how they receive settlement. Agent builders fund their\n"
        "account balance in USDC.\n"
        "\n"
        "## Quality Monitoring\n"
        "\n"
        "Every service is monitored 24/7 by an automated quality patrol system:\n"
        "- **Uptime**: Is the service online and reachable?\n"
        "- **Response Time**: How fast does it respond?\n"
        "- **Error Rate**: Are responses correct and error-free?\n"
        "\n"
        "Health Score (0-100) is calculated over the last 30 days. Higher scores\n"
        "unlock lower commission rates (Quality Rewards).\n"
        "\n"
        "## API Reference Summary\n"
        "\n"
        "Base URL: https://agentictrade.io/api/v1\n"
        "Auth: Bearer token (format: key_id:secret)\n"
        "Rate limit: 60 requests/minute\n"
        "\n"
        "### Discovery Endpoints (public, no auth)\n"
        "- GET /api/v1/discover — Search services by query, category, tags, price\n"
        "- GET /api/v1/discover/categories — List all categories\n"
        "- GET /api/v1/discover/trending — Trending services by usage\n"
        "- GET /api/v1/discover/recommendations/{agent_id} — Personalized picks\n"
        "- GET /api/v1/agent-playbook — Machine-readable integration guide\n"
        "\n"
        "### Service Endpoints\n"
        "- GET /api/v1/services — List all active services\n"
        "- GET /api/v1/services/{id} — Get service details\n"
        "- POST /api/v1/services — Register a new service (auth required)\n"
        "\n"
        "### Proxy Endpoints (auth required)\n"
        "- POST /api/v1/proxy/{service_id}/{path} — Call a service through proxy\n"
        "  Handles authentication, billing, and rate limiting automatically.\n"
        "\n"
        "### Agent Endpoints\n"
        "- POST /api/v1/agents/onboard — Self-register (no auth, returns API key)\n"
        "- GET /api/v1/agents/{agent_id}/dashboard — Agent stats (auth required)\n"
        "\n"
        "### Billing Endpoints (auth required)\n"
        "- GET /api/v1/billing/balance — Check account balance\n"
        "- POST /api/v1/billing/deposit — Create deposit invoice\n"
        "\n"
        "### MCP Integration\n"
        "- GET /api/v1/mcp/descriptor — MCP tool descriptor (machine-readable)\n"
        "- Install: npx clawhub@latest install agentictrade\n"
        "\n"
        "## Agent Discovery Protocols\n"
        "\n"
        "AgenticTrade supports all major agent discovery standards:\n"
        "- **MCP** (Model Context Protocol): /.well-known/mcp.json\n"
        "- **OpenAI Plugin**: /.well-known/ai-plugin.json\n"
        "- **ACDP** (Agent Communication Discovery): /.well-known/agents.json\n"
        "- **OpenAPI 3.0**: /api/v1/openapi.json (auto-generated)\n"
        "- **llms.txt**: /llms.txt (this file's summary version)\n"
        "- **AdCP**: /.well-known/adagents.json\n"
        "\n"
        "## SDK & Integration\n"
        "\n"
        "Python SDK:\n"
        "```\n"
        "pip install agentictrade\n"
        "\n"
        "from agentictrade import Agent\n"
        "agent = Agent(api_key='your_key')\n"
        "services = agent.discover('crypto analysis')\n"
        "result = agent.call(services[0].id, {'symbol': 'BTC'})\n"
        "```\n"
        "\n"
        "MCP Integration (Claude Code):\n"
        "```\n"
        "npx clawhub@latest install agentictrade\n"
        "```\n"
        "\n"
        "Direct HTTP:\n"
        "```\n"
        "curl -H 'Authorization: Bearer KEY_ID:SECRET' \\\n"
        "  https://agentictrade.io/api/v1/discover?q=market+data\n"
        "```\n"
        "\n"
        "## Security\n"
        "\n"
        "- Provider API keys never leave the provider's machine (Proxy Key system)\n"
        "- All connections require HTTPS\n"
        "- Rate limiting on all endpoints (60 req/min default)\n"
        "- Input validation and SQL injection prevention\n"
        "- HSTS, CSP, and security headers enabled\n"
        "- Open source — full code audit available on GitHub\n"
        "\n"
        "## About\n"
        "\n"
        "- Built by JudyAI Lab (https://judyailab.com)\n"
        "- Founded by Judy Wang\n"
        "- Website: https://agentictrade.io\n"
        "- Contact: hello@agentictrade.io\n"
        "- GitHub: https://github.com/JudyaiLab/agentictrade\n"
        "- License: MIT\n"
        "\n"
    )


@router.get("/.well-known/ai-plugin.json")
async def ai_plugin_json():
    """OpenAI-compatible plugin manifest for agent discovery."""
    return {
        "schema_version": "v1",
        "name_for_human": "AgenticTrade",
        "name_for_model": "agentictrade",
        "description_for_human": "API marketplace where AI agents discover, call, and pay for services autonomously.",
        "description_for_model": "AgenticTrade is an API marketplace for AI agents. Use it to discover available API services, check pricing, and make API calls with automatic billing. Services include crypto analysis, backtesting, data processing, and more. Agents pay per call in USDC.",
        "auth": {"type": "service_http", "authorization_type": "bearer"},
        "api": {
            "type": "openapi",
            "url": f"{_BASE_URL}/api/v1/openapi.json",
        },
        "logo_url": f"{_BASE_URL}/static/img/logo.webp",
        "contact_email": "hello@agentictrade.io",
        "legal_info_url": f"{_BASE_URL}/terms",
    }


@router.get("/.well-known/agents.json")
async def agents_json():
    """ACDP-compatible agent discovery manifest."""
    return {
        "name": "AgenticTrade",
        "description": "API marketplace for autonomous AI agent commerce. Agents discover, call, and pay for API services.",
        "url": _BASE_URL,
        "capabilities": [
            "api_marketplace",
            "service_discovery",
            "mcp_tool_descriptors",
            "x402_payments",
            "paypal_payments",
            "crypto_payments",
            "proxy_key_auth",
            "usage_billing",
        ],
        "protocols": ["mcp", "x402", "http", "openapi"],
        "endpoints": {
            "services": f"{_BASE_URL}/api/v1/services",
            "proxy": f"{_BASE_URL}/api/v1/proxy/{{service_id}}",
            "balance": f"{_BASE_URL}/api/v1/billing/balance",
            "register": f"{_BASE_URL}/portal/register",
            "mcp_registry": f"{_BASE_URL}/api/v1/mcp/registry",
        },
        "pricing": {
            "model": "pay_per_call",
            "currency": "USDC",
            "range": "$0.10 - $2.00 per call",
            "free_tier": "10 free calls per service",
        },
        "contact": "hello@agentictrade.io",
    }


@router.get("/.well-known/openai-apps-challenge")
async def openai_apps_challenge():
    """OpenAI Apps domain verification token."""
    return PlainTextResponse("AHDt9C67NTXwzF-pZOP1dD2sLcRBTpuzVOFcBdNnO-A")


@router.get("/.well-known/mcp.json")
async def mcp_json():
    """MCP auto-discovery manifest. Allows MCP clients to find our server."""
    return {
        "version": "1.0",
        "servers": [
            {
                "name": "AgenticTrade Marketplace",
                "description": (
                    "MCP server for AI agent commerce — discover, purchase, "
                    "and consume API services with autonomous payments via "
                    "x402 USDC on Base, PayPal, and 300+ cryptocurrencies."
                ),
                "endpoint": f"{_BASE_URL}/api/v1/mcp",
                "authentication": {
                    "type": "bearer",
                    "description": "Use your AgenticTrade API key as Bearer token",
                },
                "capabilities": [
                    "service-discovery",
                    "api-proxy",
                    "payment-settlement",
                    "reputation-scoring",
                    "provider-management",
                ],
            }
        ],
    }


@router.get("/.well-known/adagents.json")
async def adagents_json():
    """AdCP discovery — declares ad inventory available for buyer agents."""
    return {
        "contact": {
            "name": "AgenticTrade Ad Operations",
            "email": "ads@agentictrade.io",
        },
        "properties": [
            {
                "property_id": "marketplace",
                "property_type": "website",
                "name": "AgenticTrade API Marketplace",
                "identifiers": [{"type": "domain", "value": "agentictrade.io"}],
            }
        ],
        "tags": {"all_web": ["marketplace"]},
        "authorized_agents": [
            {
                "url": f"{_BASE_URL}/api/v1/adcp/mcp",
                "authorized_for": "Sponsored API listings, featured provider placements",
                "property_ids": ["marketplace"],
            }
        ],
    }


@router.get("/favicon.ico")
async def favicon():
    """Redirect to static favicon."""
    return RedirectResponse(url="/static/img/favicon.ico", status_code=301)


# ── Public Pages ──────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    """Serve the marketing landing page."""
    db = request.app.state.db

    with db.connect() as conn:
        svc_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM services WHERE status = 'active'"
        ).fetchone()["cnt"]
        total_calls = conn.execute(
            "SELECT COUNT(*) as cnt FROM usage_records"
        ).fetchone()["cnt"]
        agent_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM agent_identities"
        ).fetchone()["cnt"]

    return _templates.TemplateResponse("public/landing.html", {
        "request": request,
        "service_count": svc_count,
        "total_calls": total_calls,
        "agent_count": agent_count,
        **_i18n_ctx(request),
    })


@router.get("/marketplace", response_class=HTMLResponse)
async def marketplace(request: Request):
    """Serve the marketplace browser (formerly the landing page)."""
    db = request.app.state.db

    with db.connect() as conn:
        svc_rows = conn.execute(
            "SELECT name, description, price_per_call, free_tier_calls, category, metadata "
            "FROM services WHERE status = 'active' ORDER BY created_at"
        ).fetchall()
        total_calls = conn.execute(
            "SELECT COUNT(*) as cnt FROM usage_records"
        ).fetchone()["cnt"]
        agent_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM agent_identities"
        ).fetchone()["cnt"]

    import json as _json
    locale = getattr(request.state, "locale", "en")
    services = []
    for r in svc_rows:
        svc = dict(r)
        meta = svc.get("metadata")
        if isinstance(meta, str):
            try:
                meta = _json.loads(meta)
            except Exception:
                meta = {}
        elif not isinstance(meta, dict):
            meta = {}
        if locale == "zh-tw" and meta:
            svc["name"] = meta.get("name_zh") or svc["name"]
            svc["description"] = meta.get("desc_zh") or svc["description"]
        services.append(svc)

    return _templates.TemplateResponse("marketplace.html", {
        "request": request,
        "service_count": len(services),
        "total_calls": total_calls,
        "agent_count": agent_count,
        "services": services,
        **_i18n_ctx(request),
    })


@router.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    """Serve the pricing page."""
    db = request.app.state.db

    with db.connect() as conn:
        svc_rows = conn.execute(
            "SELECT name, description, price_per_call, free_tier_calls "
            "FROM services WHERE status = 'active' ORDER BY price_per_call ASC"
        ).fetchall()

    services = [dict(r) for r in svc_rows]

    return _templates.TemplateResponse("public/pricing.html", {
        "request": request,
        "services": services,
        **_i18n_ctx(request),
    })


@router.get("/providers", response_class=HTMLResponse)
async def providers(request: Request):
    """Serve the For Providers page."""
    return _templates.TemplateResponse("public/providers.html", {
        "request": request,
        **_i18n_ctx(request),
    })


@router.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    """Serve the About page."""
    return _templates.TemplateResponse("public/about.html", {
        "request": request,
        **_i18n_ctx(request),
    })


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request):
    """Serve the Terms of Service page."""
    return _templates.TemplateResponse("public/terms.html", {
        "request": request,
        **_i18n_ctx(request),
    })


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    """Serve the Privacy Policy page."""
    return _templates.TemplateResponse("public/privacy.html", {
        "request": request,
        **_i18n_ctx(request),
    })


@router.get("/quality-policy", response_class=HTMLResponse)
async def quality_policy(request: Request):
    """Serve the Quality Policy page — public documentation of quality
    monitoring, scoring thresholds, enforcement actions, and notification
    process for marketplace services."""
    return _templates.TemplateResponse("public/quality-policy.html", {
        "request": request,
        **_i18n_ctx(request),
    })



@router.get("/.well-known/security.txt", response_class=PlainTextResponse)
@router.get("/security.txt", response_class=PlainTextResponse)
async def security_txt():
    """RFC 9116 security contact information."""
    return (
        "Contact: mailto:hello@agentictrade.io\n"
        "Preferred-Languages: en, zh, ko, ja\n"
        "Canonical: https://agentictrade.io/.well-known/security.txt\n"
        "Policy: https://agentictrade.io/terms\n"
        "Expires: 2027-04-01T00:00:00.000Z\n"
    )


@router.get("/faq", response_class=HTMLResponse)
async def faq_page(request: Request):
    """Standalone FAQ page — aggregates all Q&A for SEO/AEO."""
    return _templates.TemplateResponse("public/faq.html", {
        "request": request,
        **_i18n_ctx(request),
    })


@router.get("/docs/{slug}", response_class=HTMLResponse)
async def docs_sub_page(slug: str, request: Request):
    """Documentation sub-pages for focused SEO indexing."""
    import os
    _valid_slugs = {"quickstart", "authentication", "discovery", "payments", "mcp", "sdk"}
    if slug not in _valid_slugs:
        from fastapi.exceptions import HTTPException
        raise HTTPException(status_code=404, detail="Doc page not found")
    return _templates.TemplateResponse(f"public/docs/{slug}.html", {
        "request": request,
        **_i18n_ctx(request),
    })


@router.get("/use-cases/{slug}", response_class=HTMLResponse)
async def use_case_page(slug: str, request: Request):
    """Use case landing pages for specific agent verticals."""
    import os
    tpl_path = f"public/use-cases/{slug}.html"
    full_path = os.path.join(str(_TEMPLATES_DIR), tpl_path)
    if not os.path.isfile(full_path):
        from fastapi.exceptions import HTTPException
        raise HTTPException(status_code=404, detail="Use case not found")
    return _templates.TemplateResponse(tpl_path, {
        "request": request,
        **_i18n_ctx(request),
    })


@router.get("/marketplace/{service_id}", response_class=HTMLResponse)
async def service_detail_page(service_id: str, request: Request):
    """Individual service detail page with SEO schema."""
    db = request.app.state.db
    svc = db.get_service(service_id)
    if not svc or svc.get("status") == "removed":
        from fastapi.exceptions import HTTPException
        raise HTTPException(status_code=404, detail="Service not found")

    # Get health score
    from marketplace.health_monitor import HealthMonitor
    monitor = HealthMonitor(db)
    health = monitor.get_service_health_score(service_id)

    return _templates.TemplateResponse("public/service-detail.html", {
        "request": request,
        "service": svc,
        "health": health,
        **_i18n_ctx(request),
    })


@router.get("/marketplace/category/{category}", response_class=HTMLResponse)
async def category_page(category: str, request: Request):
    """Category landing page for marketplace services."""
    db = request.app.state.db
    from marketplace.health_monitor import HealthMonitor
    monitor = HealthMonitor(db)

    services = db.list_services(status="active", category=category)
    scored = []
    for svc in services:
        h = monitor.get_service_health_score(svc["id"])
        scored.append({"service": svc, "health": h})

    # Valid categories
    categories = {
        "ai": "AI & Machine Learning",
        "crypto": "Crypto & Blockchain",
        "data": "Data & Analytics",
        "code": "Code & Development",
        "content": "Content & Media",
        "finance": "Finance & Trading",
        "security": "Security & Compliance",
        "productivity": "Productivity & Automation",
    }
    cat_name = categories.get(category, category.title())

    return _templates.TemplateResponse("public/category.html", {
        "request": request,
        "category": category,
        "category_name": cat_name,
        "categories": categories,
        "services": scored,
        **_i18n_ctx(request),
    })


@router.get("/checkout/success", response_class=HTMLResponse)
async def checkout_success(
    request: Request,
    session_id: str = "",
    payment_id: str = "",
    provider: str = "",
):
    """Post-purchase thank you page."""
    return _templates.TemplateResponse("checkout-success.html", {
        "request": request,
        "session_id": session_id,
        "payment_id": payment_id,
        "provider": provider,
    })


@router.get("/api-docs", response_class=HTMLResponse)
async def api_docs(request: Request):
    """Serve the public API documentation page with live service data."""
    db = request.app.state.db

    with db.connect() as conn:
        svc_rows = conn.execute(
            "SELECT id, name, description, price_per_call, free_tier_calls "
            "FROM services WHERE status = 'active' ORDER BY price_per_call ASC"
        ).fetchall()

    services = [dict(r) for r in svc_rows]

    demo_id = scanner_id = backtest_id = ""
    for svc in services:
        name_lower = svc["name"].lower()
        if "demo" in name_lower:
            demo_id = svc["id"]
        elif "scanner" in name_lower:
            scanner_id = svc["id"]
        elif "backtest" in name_lower:
            backtest_id = svc["id"]

    return _templates.TemplateResponse("api-docs.html", {
        "request": request,
        "services": services,
        "demo_service_id": demo_id,
        "scanner_service_id": scanner_id,
        "backtest_service_id": backtest_id,
        **_i18n_ctx(request),
    })


@router.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    """Serve the public platform status page with real-time health metrics."""
    db = getattr(request.app.state, "db", None)

    # ── Database health ──
    db_ok = False
    if db is not None:
        try:
            with db.connect() as conn:
                conn.execute("SELECT 1")
            db_ok = True
        except Exception:
            pass

    # ── Metrics from health_checks table ──
    uptime_pct = 100.0
    avg_latency_ms = 0
    active_services = 0
    daily_status: list[dict] = []

    now = datetime.now(timezone.utc)

    if db is not None and db_ok:
        try:
            with db.connect() as conn:
                # Active services count
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM services WHERE status = 'active'"
                ).fetchone()
                active_services = row["cnt"] if row else 0

                # Check if health_checks table exists
                table_check = conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='health_checks'"
                ).fetchone()

                if table_check:
                    # 30-day uptime and latency from health_checks
                    cutoff_30d = (now - timedelta(days=30)).isoformat()
                    stats = conn.execute(
                        "SELECT COUNT(*) as total, "
                        "SUM(CASE WHEN reachable = 1 THEN 1 ELSE 0 END) as up, "
                        "AVG(CASE WHEN reachable = 1 THEN latency_ms END) as avg_lat "
                        "FROM health_checks WHERE checked_at >= ?",
                        (cutoff_30d,),
                    ).fetchone()

                    if stats and stats["total"] > 0:
                        uptime_pct = round(
                            (stats["up"] or 0) / stats["total"] * 100, 2
                        )
                        avg_latency_ms = round(stats["avg_lat"] or 0)

                    # 7-day daily status
                    for i in range(6, -1, -1):
                        day_start = (now - timedelta(days=i)).replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )
                        day_end = day_start + timedelta(days=1)
                        day_row = conn.execute(
                            "SELECT COUNT(*) as total, "
                            "SUM(CASE WHEN reachable = 1 THEN 1 ELSE 0 END) as up "
                            "FROM health_checks "
                            "WHERE checked_at >= ? AND checked_at < ?",
                            (day_start.isoformat(), day_end.isoformat()),
                        ).fetchone()

                        day_date = day_start.strftime("%b %d")
                        if day_row and day_row["total"] > 0:
                            day_uptime = (day_row["up"] or 0) / day_row["total"]
                            if day_uptime >= 0.999:
                                status = "up"
                                label = "Operational"
                            elif day_uptime >= 0.95:
                                status = "degraded"
                                label = "Degraded"
                            else:
                                status = "down"
                                label = "Disruption"
                        else:
                            # No data for this day = assume operational
                            status = "up"
                            label = "No issues"

                        daily_status.append({
                            "date": day_date,
                            "status": status,
                            "label": label,
                        })
                else:
                    # No health_checks table yet — show default 7 green days
                    for i in range(6, -1, -1):
                        day_start = now - timedelta(days=i)
                        daily_status.append({
                            "date": day_start.strftime("%b %d"),
                            "status": "up",
                            "label": "No issues",
                        })
        except Exception:
            # Fallback if any query fails
            for i in range(6, -1, -1):
                day_start = now - timedelta(days=i)
                daily_status.append({
                    "date": day_start.strftime("%b %d"),
                    "status": "up",
                    "label": "No issues",
                })

    # Fill daily_status if empty
    if not daily_status:
        for i in range(6, -1, -1):
            day_start = now - timedelta(days=i)
            daily_status.append({
                "date": day_start.strftime("%b %d"),
                "status": "up",
                "label": "No issues",
            })

    # Determine overall platform status
    if not db_ok:
        platform_status = "degraded"
    elif uptime_pct >= 99.9:
        platform_status = "operational"
    elif uptime_pct >= 95.0:
        platform_status = "degraded"
    else:
        platform_status = "down"

    updated_at = now.strftime("%Y-%m-%d %H:%M UTC")

    return _templates.TemplateResponse("public/status.html", {
        "request": request,
        "platform_status": platform_status,
        "uptime_pct": uptime_pct,
        "avg_latency_ms": avg_latency_ms,
        "active_services": active_services,
        "daily_status": daily_status,
        "db_ok": db_ok,
        "updated_at": updated_at,
        **_i18n_ctx(request),
    })


# ── Nanopayments Dashboard ─────────────────────────────────────────


@router.get("/nano-dashboard", response_class=HTMLResponse)
async def nano_dashboard(request: Request):
    """Nanopayments real-time dashboard — Arc Testnet x402 USDC stats."""
    import httpx

    nano_url = os.getenv("NANO_GATEWAY_URL", "http://localhost:3402")
    stats: dict = {
        "transactions": 0,
        "total_revenue_usdc": 0,
        "seller": "N/A",
        "networks": [],
        "explorer": {},
        "recent_transactions": [],
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{nano_url}/api/v1/nano/stats")
            if resp.status_code == 200:
                stats = resp.json()
    except Exception:
        pass

    return _templates.TemplateResponse("public/nano-dashboard.html", {
        "request": request,
        "stats": stats,
        **_i18n_ctx(request),
    })


# ── Docs sub-routes (prevent 404 on /docs/api, /docs/getting-started, etc.) ──


@router.get("/docs/api", response_class=RedirectResponse)
@router.get("/docs/api-reference", response_class=RedirectResponse)
async def docs_api_redirect():
    """Redirect /docs/api to the public API documentation page."""
    return RedirectResponse(url="/api-docs", status_code=301)


@router.get("/docs/getting-started", response_class=RedirectResponse)
async def docs_getting_started_redirect():
    """Redirect /docs/getting-started to the public API docs quickstart."""
    return RedirectResponse(url="/api-docs#quickstart", status_code=301)


@router.get("/docs/payments", response_class=RedirectResponse)
async def docs_payments_redirect():
    """Redirect /docs/payments to the pricing page."""
    return RedirectResponse(url="/pricing", status_code=301)


@router.get("/docs/architecture", response_class=RedirectResponse)
async def docs_architecture_redirect():
    """Redirect /docs/architecture to the about page."""
    return RedirectResponse(url="/about", status_code=301)


@router.get("/api/v1/openapi.json", response_class=RedirectResponse)
async def openapi_json_redirect():
    """Redirect /api/v1/openapi.json → /openapi.json (FastAPI auto-generated)."""
    return RedirectResponse(url="/openapi.json", status_code=301)


@router.get("/health")
async def health(request: Request):
    """Lightweight health check with basic dependency verification."""
    db = getattr(request.app.state, "db", None)
    db_ok = False
    if db is not None:
        try:
            with db.connect() as conn:
                conn.execute("SELECT 1")
            db_ok = True
        except Exception:
            pass
    status = "ok" if db_ok else "degraded"
    return {"status": status, "database": "ok" if db_ok else "error"}


@router.get("/health/details")
async def health_details(request: Request):
    """Full health details — admin-only."""
    from api.deps import require_admin
    require_admin(request)  # Raises HTTPException(401/403) if not admin

    db = getattr(request.app.state, "db", None)
    checks: dict = {}
    db_status = "not_configured"
    db_latency = None
    if db is not None:
        import time as _time
        t0 = _time.monotonic()
        db_detail = None
        try:
            with db.connect() as conn:
                conn.execute("SELECT 1")
            db_latency = round((_time.monotonic() - t0) * 1000, 1)
            db_status = "ok"
        except Exception as exc:
            db_latency = round((_time.monotonic() - t0) * 1000, 1)
            db_status = "error"
            db_detail = str(exc)
    db_entry: dict = {"status": db_status, "latency_ms": db_latency}
    if db_detail:
        db_entry["detail"] = db_detail
    checks["database"] = db_entry

    registry = getattr(request.app.state, "registry", None)
    services_count = 0
    if registry is not None:
        try:
            services_count = len(registry.list_services())
        except Exception:
            pass
    checks["services_count"] = services_count

    payment_router = getattr(request.app.state, "payment_router", None)
    payment_providers: list[str] = []
    if payment_router is not None:
        providers_dict = getattr(payment_router, "_providers", {})
        payment_providers = list(providers_dict.keys())
    checks["payment_providers"] = payment_providers

    overall = "ok" if db_status == "ok" else "degraded"
    from fastapi.responses import JSONResponse as _JSONResp
    status_code = 200 if overall == "ok" else 503
    return _JSONResp(
        content={
            "status": overall,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
        },
        status_code=status_code,
    )


# ── i18n path-based redirects (prevent 404 on /zh, /ko, /ja, etc.) ──
# Registered as explicit routes to avoid catch-all conflicts.

_I18N_PATH_MAP = {
    "zh": "zh-tw", "zh-tw": "zh-tw", "zh-TW": "zh-tw",
    "ko": "ko", "ja": "ja", "fr": "fr", "de": "de",
    "ru": "ru", "es": "es", "pt": "pt",
}


def _make_lang_redirect(locale: str):
    """Factory for locale redirect handlers."""
    async def _redirect():
        return RedirectResponse(url=f"/?lang={locale}", status_code=302)
    return _redirect


for _path, _locale in _I18N_PATH_MAP.items():
    router.add_api_route(
        f"/{_path}",
        _make_lang_redirect(_locale),
        methods=["GET"],
        response_class=RedirectResponse,
        include_in_schema=False,
    )
