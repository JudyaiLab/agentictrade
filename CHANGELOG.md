# Changelog

## [0.8.0] - 2026-03-28

### Added
- **OpenClaw Skill on ClawHub**: Published `agentictrade@1.0.0` — one-command install for any OpenClaw agent (`openclaw install agentictrade@1.0.0`)
- **4 Integration Methods**: OpenClaw Skill (easiest), MCP Server v0.2.0 (10 tools), Python SDK, REST API — comparison guide on Getting Started page
- **Email verification for agent-to-human binding**: Every agent provider must verify ownership via signed email token before listing services
- **Agent Playbook API**: `GET /api/v1/agent-playbook` — machine-readable JSON guiding AI agents through onboarding, human binding, service publishing, and consumption
- **MCP Server Provider tools** (v0.2.0): 5 new provider tools — `register_service`, `update_service`, `my_services`, `my_earnings`, `register_as_provider` (total: 10 tools)
- **Getting Started page**: `/portal/getting-started` — visual onboarding guide with Provider/Buyer dual paths, 3-step flow with code snippets
- **Portal navigation**: Getting Started link added to portal nav bar
- **Registration redirect**: New registrations now redirect to Getting Started (was Dashboard)

### Changed
- **Landing page redesigned**: OpenClaw Skill focus replacing previous Starter Kit messaging
- **SKILL.md updated**: Added funding guidance for OpenClaw Skill
- **MCP Server upgraded to v0.2.0**: From 5 marketplace-only tools to 10 tools (5 marketplace + 5 provider)

### Removed
- **Starter Kit (deleted)**: Product page, download endpoint, ZIP archive, and templates all removed — replaced by OpenClaw Skill and 4 integration methods

### Documentation
- Updated IR_DECK.md and IR_DECK_zh-TW.md: Removed starter kit references, added OpenClaw/ClawHub distribution, updated product overview
- Updated PRODUCT_SPEC.md: Added OpenClaw Skill, Agent Playbook API, MCP Server v0.2.0, email verification as product components
- Marketplace DB cleaned: Removed 5 test data entries, retained 7 real services

## [0.6.1] - 2026-03-21

### Added
- **Founding Seller badge system**: First 50 providers get permanent badge + 8% commission cap (vs 10% standard)
- **API Health Monitor**: Automated service health checks with quality scoring (uptime 50% + latency 30% + error rate 20%)
- Provider reputation endpoint: `GET /provider/reputation`
- Provider health endpoint: `GET /provider/health`
- Admin health check endpoints: `POST /admin/health-check/run`, `GET /admin/health-check/scores`
- x402 payment middleware ↔ proxy integration: skip balance deduction when x402 crypto payment verified
- Test suite `conftest.py` with rate limiter reset fixture for test isolation
- 4 x402 bypass proxy tests, 10 founding seller tests, 9 health monitor tests

### Fixed
- x402 middleware initialization moved to module level (fix Starlette "Cannot add middleware after started" crash)
- Rate limiter test isolation: all 739 tests now pass consistently in full suite (was 9 failures)

## [0.6.0] - 2026-03-20

### Changed
- **Strategic repositioning**: Platform-as-a-service model (10% commission), not framework sales
- Starter Kit repositioned as free client-side SDK (connects to agentictrade.io)
- All localhost:8092 references replaced with agentictrade.io in Starter Kit
- Provider Growth Program added to revenue model (0% → 5% → 10% tiered commission)
- Updated IR Deck, Product Spec, Implementation Plan, Market Research

## [0.5.0] - 2026-03-19

### Added
- **Starter Kit**: Free SDK + 13-chapter guide + CLI tools + 3 service templates
- **Dashboard UI**: 5 pages (overview, services, analytics, settings, API docs) + 58 tests
- **Security hardening**: Fixed 2 CRITICAL + 6 HIGH vulnerabilities from OWASP audit
- Webhook header standardized to `X-ACF-Signature` / `X-ACF-Event`

### Fixed
- Webhook signature header mismatch (was X-Webhook-Signature in docs, X-ACF-Signature in code)

## [0.4.0] - 2026-03-19

### Added
- **Multi-rail payments**: PaymentRouter with x402, PayPal, and NOWPayments providers
- **Webhook system**: HMAC-SHA256 signed event notifications with retry
- **MCP Bridge**: 5 MCP tools for AI agent discovery and interaction
- **Admin dashboard**: Platform stats, usage analytics, provider rankings, service health
- **Python SDK**: Full client library for all marketplace operations
- **Rate limiting**: Token bucket middleware (60 req/min per IP)
- **Shared auth deps**: Consolidated authentication in `api/deps.py`
- **Integration tests**: End-to-end marketplace flow tests
- **Example scripts**: Quickstart, team setup, webhook listener

### Security
- Cross-team deletion prevention (team_id scoping on delete operations)
- Input validation: allowlists for roles, gate types, sort fields
- Decimal parse protection on price filters
- Period format validation (all-time or YYYY-MM)
- owner_id hidden from public agent responses
- Limit clamping (max 100) on all paginated endpoints

### Changed
- PaymentProxy now auto-selects payment provider via PaymentRouter
- PaymentProxy dispatches webhook events on successful calls
- Version bumped to 0.4.0

## [0.3.0] - 2026-03-18

### Added
- **Agent Identity**: Register, verify, search agents with capabilities
- **Reputation Engine**: Auto-computed scores from usage data (latency, reliability, quality)
- **Enhanced Discovery**: Full-text search, categories, trending, recommendations
- **Team Management**: Teams, members, routing rules, quality gates
- **Templates**: 3 team templates (solo/small_team/enterprise) + 3 service templates
- **Docker Compose**: PostgreSQL 16 production deployment
- 160+ new tests

## [0.2.0] - 2026-03-17

### Added
- x402 USDC payment middleware
- CDP wallet integration for automated payouts
- Settlement engine with platform fee splitting
- Service registry with SSRF protection

## [0.1.0] - 2026-03-16

### Added
- Initial marketplace: service registration, API key auth, payment proxy
- Usage metering and billing
- SQLite database layer
- FastAPI server with health checks
