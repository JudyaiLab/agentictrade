# API Reference

Base URL: `http://localhost:8000`

All authenticated endpoints require the header:

```
Authorization: Bearer {key_id}:{secret}
```

---

## Table of Contents

- [Authentication](#authentication)
- [Service Registry](#service-registry)
- [Discovery](#discovery)
- [Payment Proxy](#payment-proxy)
- [Agent Identity](#agent-identity)
- [Reputation](#reputation)
- [Settlements](#settlements)
- [Team Management](#team-management)
- [Webhooks](#webhooks)
- [Templates](#templates)
- [Provider Portal](#provider-portal)
- [Admin](#admin)
- [Dashboard](#dashboard)
- [Health](#health)
- [Error Responses](#error-responses)
- [Rate Limiting](#rate-limiting)

---

## Authentication

### Create API Key

```
POST /api/v1/keys
```

Create a new API key. **Buyer keys require no authentication.** Provider and admin keys require an existing authenticated Bearer token.

**Request Body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `owner_id` | string | yes | -- | Unique identifier for the key owner |
| `role` | string | no | `"buyer"` | One of: `buyer`, `provider`, `admin` |
| `rate_limit` | integer | no | `60` | Max requests per minute |
| `wallet_address` | string | no | `null` | USDC wallet address |

**Example:**

```bash
# Create a buyer key (no auth required)
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{"owner_id": "my-agent", "role": "buyer"}'

# Create a provider key (requires auth)
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {existing_key_id}:{existing_secret}" \
  -d '{"owner_id": "my-agent", "role": "provider"}'
```

**Response (201):**

```json
{
  "key_id": "acf_a1b2c3d4e5f6g7h8",
  "secret": "sec_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "role": "provider",
  "rate_limit": 60,
  "message": "Save the secret — it cannot be retrieved again."
}
```

> **Important:** The `secret` is returned only once. Store it securely.

---

### Validate API Key

```
POST /api/v1/keys/validate
```

Validate an API key pair. Returns owner and role information if valid.

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `key_id` | string | yes | The key ID |
| `secret` | string | yes | The key secret |

**Response (200):**

```json
{
  "valid": true,
  "owner_id": "my-agent",
  "role": "provider",
  "rate_limit": 60
}
```

**Error (401):** Invalid credentials.

---

## Service Registry

### Register a Service

```
POST /api/v1/services
```

Register a new service on the marketplace. **Requires provider or admin API key.**

**Request Body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | yes | -- | Service name |
| `description` | string | no | `""` | Service description |
| `endpoint` | string | yes | -- | Provider's API URL (must start with `https://` or `http://`) |
| `price_per_call` | string | yes | -- | Price per call (e.g. `"0.05"`) |
| `category` | string | no | `""` | Category (e.g. `ai`, `data`, `content`) |
| `tags` | string[] | no | `[]` | Searchable tags |
| `payment_method` | string | no | `"x402"` | `x402`, `paypal`, or `nowpayments` |
| `free_tier_calls` | integer | no | `0` | Number of free calls per buyer |

**Example:**

```bash
curl -X POST http://localhost:8000/api/v1/services \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {key_id}:{secret}" \
  -d '{
    "name": "Sentiment Analysis API",
    "description": "Analyze text sentiment with confidence scores",
    "endpoint": "https://my-api.example.com/v1",
    "price_per_call": "0.05",
    "category": "ai",
    "tags": ["nlp", "sentiment"],
    "payment_method": "x402",
    "free_tier_calls": 50
  }'
```

**Response (201):**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "provider_id": "my-agent",
  "name": "Sentiment Analysis API",
  "description": "Analyze text sentiment with confidence scores",
  "pricing": {
    "price_per_call": "0.05",
    "currency": "USDC",
    "payment_method": "x402",
    "free_tier_calls": 50
  },
  "status": "active",
  "category": "ai",
  "tags": ["nlp", "sentiment"],
  "created_at": "2026-03-19T10:00:00+00:00",
  "updated_at": "2026-03-19T10:00:00+00:00"
}
```

---

### List Services

```
GET /api/v1/services
```

List and search services. **No authentication required.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | -- | Text search in name and description |
| `category` | string | -- | Filter by category |
| `status` | string | `"active"` | Filter by status (`active`, `paused`, `removed`) |
| `limit` | integer | `50` | Max results (1--100) |
| `offset` | integer | `0` | Pagination offset |

**Response (200):**

```json
{
  "services": [{ "id": "...", "name": "...", "pricing": {...}, ... }],
  "count": 10,
  "offset": 0,
  "limit": 50
}
```

---

### Get Service

```
GET /api/v1/services/{service_id}
```

Get details for a single service. **No authentication required.**

**Response (200):** Service object (same shape as register response).

**Error (404):** Service not found.

---

### Update Service

```
PATCH /api/v1/services/{service_id}
```

Update a service. **Owner only; requires provider API key.**

**Request Body (all optional):**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | New name |
| `description` | string | New description |
| `endpoint` | string | New endpoint URL |
| `price_per_call` | string | New price |
| `status` | string | `active`, `paused`, or `removed` |
| `category` | string | New category |
| `tags` | string[] | New tags |

**Response (200):** Updated service object.

---

### Delete Service

```
DELETE /api/v1/services/{service_id}
```

Soft-delete a service (sets status to `removed`). **Owner only; requires provider API key.**

**Response (200):**

```json
{"status": "removed", "id": "550e8400-..."}
```

---

## Discovery

### Search Services (Advanced)

```
GET /api/v1/discover
```

Advanced service discovery with full-text search, filters, and sorting. **No authentication required.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `q` | string | -- | Full-text search query |
| `category` | string | -- | Filter by category |
| `tags` | string | -- | Comma-separated tag filter (e.g. `nlp,sentiment`) |
| `min_price` | string | -- | Minimum price per call (e.g. `"0.01"`) |
| `max_price` | string | -- | Maximum price per call (e.g. `"1.00"`) |
| `payment_method` | string | -- | `x402`, `paypal`, or `nowpayments` |
| `has_free_tier` | boolean | -- | `true` to filter for services with free calls |
| `sort_by` | string | `"created_at"` | Sort: `created_at`, `price`, or `name` |
| `limit` | integer | `50` | Max results (1--100) |
| `offset` | integer | `0` | Pagination offset |

**Example:**

```bash
curl "http://localhost:8000/api/v1/discover?q=nlp&category=ai&has_free_tier=true&sort_by=price&limit=10"
```

**Response (200):**

```json
{
  "services": [{ "id": "...", "name": "...", "pricing": {...}, ... }],
  "total": 25,
  "offset": 0,
  "limit": 10
}
```

---

### List Categories

```
GET /api/v1/discover/categories
```

Get all service categories with active service counts. **No authentication required.**

**Response (200):**

```json
{
  "categories": [
    {"category": "ai", "count": 12},
    {"category": "data", "count": 5},
    {"category": "content", "count": 3}
  ]
}
```

---

### Trending Services

```
GET /api/v1/discover/trending
```

Get trending services ranked by usage volume. **No authentication required.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | integer | `10` | Max results |

**Response (200):**

```json
{
  "trending": [
    {
      "service": { "id": "...", "name": "...", "pricing": {...}, ... },
      "call_count": 1523,
      "avg_latency_ms": 187.3
    }
  ],
  "count": 10
}
```

---

### Personalized Recommendations

```
GET /api/v1/discover/recommendations/{agent_id}
```

Get service recommendations based on an agent's usage history. Returns services in categories the agent uses most, excluding already-used services. **No authentication required.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | integer | `5` | Max recommendations |

**Response (200):**

```json
{
  "recommendations": [{ "id": "...", "name": "...", ... }],
  "count": 5
}
```

---

## Payment Proxy

### Proxy Request

```
ANY /api/v1/proxy/{service_id}/{path}
```

Forward a request to a service provider with automatic payment handling. Supports `GET`, `POST`, `PUT`, `PATCH`, `DELETE`. **Requires buyer or provider API key.**

The marketplace handles the entire payment flow:

1. Validates your API key and checks rate limits
2. Looks up the service and pricing
3. Checks free tier quota (per-buyer, per-service)
4. Creates a payment via the configured payment provider (x402/PayPal/NOWPayments)
5. Forwards your request to the provider's endpoint
6. Records usage and billing
7. Dispatches webhook events (`service.called`)
8. Returns the provider's response with billing headers

**Path Parameters:**

| Param | Description |
|-------|-------------|
| `service_id` | The service to call |
| `path` | Path appended to the provider's endpoint |

**Example:**

```bash
curl -X POST http://localhost:8000/api/v1/proxy/{service_id}/analyze \
  -H "Authorization: Bearer {key_id}:{secret}" \
  -H "Content-Type: application/json" \
  -d '{"text": "Analyze this document"}'
```

**Response:** The provider's original response, plus these billing headers:

| Header | Description |
|--------|-------------|
| `X-ACF-Usage-Id` | Unique usage record ID |
| `X-ACF-Amount` | Amount charged (e.g. `"0.05"`) |
| `X-ACF-Free-Tier` | `"true"` if within free tier |
| `X-ACF-Latency-Ms` | Round-trip latency in milliseconds |

**Error Codes:**

| Code | Meaning |
|------|---------|
| 401 | Missing or invalid API key |
| 404 | Service not found |
| 429 | Rate limit exceeded |
| 502 | Provider unreachable |
| 504 | Provider timeout |

---

### Get My Usage

```
GET /api/v1/usage/me
```

Get usage statistics for the authenticated buyer. **Requires API key.**

**Response (200):**

```json
{
  "buyer_id": "my-agent",
  "total_calls": 42,
  "total_spent_usd": "2.10",
  "avg_latency_ms": 205.3
}
```

---

## Agent Identity

### Register Agent

```
POST /api/v1/agents
```

Register a new agent identity. **Requires API key.**

**Request Body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `display_name` | string | yes | -- | Agent's display name |
| `identity_type` | string | no | `"api_key_only"` | `api_key_only`, `kya_jwt`, or `did_vc` |
| `capabilities` | string[] | no | `[]` | Declared capabilities |
| `wallet_address` | string | no | `null` | USDC wallet address |
| `metadata` | object | no | `{}` | Custom metadata |

**Example:**

```bash
curl -X POST http://localhost:8000/api/v1/agents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {key_id}:{secret}" \
  -d '{
    "display_name": "My AI Agent",
    "capabilities": ["nlp", "inference"],
    "identity_type": "api_key_only",
    "wallet_address": "0x1234567890abcdef1234567890abcdef12345678"
  }'
```

**Response (201):**

```json
{
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "display_name": "My AI Agent",
  "identity_type": "api_key_only",
  "capabilities": ["nlp", "inference"],
  "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
  "verified": false,
  "reputation_score": 0.0,
  "status": "active",
  "owner_id": "my-agent",
  "created_at": "2026-03-19T10:00:00+00:00",
  "updated_at": "2026-03-19T10:00:00+00:00"
}
```

---

### List Agents

```
GET /api/v1/agents
```

List agents. **No authentication required.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `status` | string | `"active"` | Filter by status |
| `limit` | integer | `50` | Max results (1--100) |
| `offset` | integer | `0` | Pagination offset |

**Response (200):**

```json
{
  "agents": [{ "agent_id": "...", "display_name": "...", ... }],
  "count": 10
}
```

---

### Search Agents

```
GET /api/v1/agents/search
```

Search agents by name or ID. **No authentication required.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `q` | string | `""` | Search query |
| `limit` | integer | `20` | Max results (1--100) |

**Response (200):**

```json
{
  "agents": [{ "agent_id": "...", "display_name": "...", ... }],
  "count": 3
}
```

---

### Get Agent

```
GET /api/v1/agents/{agent_id}
```

Get agent details. **No authentication required.**

**Response (200):** Agent object (same shape as register response, without `owner_id`).

**Error (404):** Agent not found.

---

### Update Agent

```
PATCH /api/v1/agents/{agent_id}
```

Update an agent. **Owner only; requires API key.**

**Request Body (all optional):**

| Field | Type | Description |
|-------|------|-------------|
| `display_name` | string | New display name |
| `capabilities` | string[] | Updated capabilities |
| `wallet_address` | string | New wallet address |
| `status` | string | `active`, `suspended`, or `deactivated` |
| `metadata` | object | Updated metadata |

**Response (200):** Updated agent object.

---

### Deactivate Agent

```
DELETE /api/v1/agents/{agent_id}
```

Soft-deactivate an agent. **Owner only; requires API key.**

**Response (200):**

```json
{"status": "deactivated", "agent_id": "550e8400-..."}
```

---

### Verify Agent (Admin)

```
POST /api/v1/agents/{agent_id}/verify
```

Mark an agent as verified. **Requires admin API key.**

**Response (200):** Agent object with `"verified": true`.

---

## Reputation

Reputation scores are computed automatically from real usage data. No user ratings or manual input.

**Scoring formula:**

| Component | Weight | Calculation |
|-----------|--------|-------------|
| Latency score | 30% | `10.0 - (avg_latency_ms / 1000)`, clamped [0, 10] |
| Reliability score | 40% | `success_rate / 10`, clamped [0, 10] |
| Response quality | 30% | `(1 - error_rate/100) * 10`, clamped [0, 10] |
| **Overall** | -- | Weighted average of the three components |

### Get Agent Reputation

```
GET /api/v1/agents/{agent_id}/reputation
```

Get reputation scores for an agent. **No authentication required.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `period` | string | `"all-time"` | `"all-time"` or `"YYYY-MM"` format |
| `compute` | boolean | `false` | If `true`, recompute from live usage data |

**Response (200):**

```json
{
  "agent_id": "550e8400-...",
  "service_id": "",
  "overall_score": 8.72,
  "latency_score": 9.15,
  "reliability_score": 8.50,
  "response_quality": 8.40,
  "call_count": 150,
  "period": "all-time"
}
```

---

### Get Service Reputation

```
GET /api/v1/services/{service_id}/reputation
```

Get reputation records for a specific service. **No authentication required.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `period` | string | `"all-time"` | `"all-time"` or `"YYYY-MM"` format |

**Response (200):**

```json
{
  "service_id": "550e8400-...",
  "period": "all-time",
  "records": [
    {
      "agent_id": "...",
      "overall_score": 8.7,
      "latency_score": 9.1,
      "reliability_score": 8.5,
      "response_quality": 8.4,
      "call_count": 150
    }
  ]
}
```

---

### Reputation Leaderboard

```
GET /api/v1/reputation/leaderboard
```

Get top agents ranked by reputation score. **No authentication required.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | integer | `20` | Max results (1--100) |

**Response (200):**

```json
{
  "leaderboard": [
    {
      "agent_id": "550e8400-...",
      "display_name": "Top Agent",
      "reputation_score": 9.2,
      "verified": true
    }
  ],
  "count": 20
}
```

---

## Settlements

### Create Settlement (Admin)

```
POST /api/v1/settlements
```

Create a settlement for a provider's earnings in a given period. Aggregates usage records, calculates platform fee, and creates a payout record. **Requires admin API key.**

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider_id` | string | yes | Provider to settle |
| `period_start` | string | yes | ISO 8601 start date (e.g. `"2026-03-01T00:00:00Z"`) |
| `period_end` | string | yes | ISO 8601 end date |

**Response (201):**

```json
{
  "id": "550e8400-...",
  "provider_id": "provider-1",
  "total_amount": "10.50",
  "platform_fee": "1.05",
  "net_amount": "9.45",
  "call_count": 210,
  "status": "pending"
}
```

---

### List Settlements

```
GET /api/v1/settlements
```

List settlements. Providers see only their own; admins see all. **Requires API key.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `provider_id` | string | -- | Filter by provider (admin only) |
| `status` | string | -- | Filter: `pending`, `processing`, `completed`, `failed` |
| `limit` | integer | `50` | Max results (1--100) |

**Response (200):**

```json
{
  "settlements": [
    {
      "id": "550e8400-...",
      "provider_id": "provider-1",
      "period_start": "2026-03-01T00:00:00Z",
      "period_end": "2026-04-01T00:00:00Z",
      "total_amount": "10.50",
      "platform_fee": "1.05",
      "net_amount": "9.45",
      "status": "pending",
      "payment_tx": null
    }
  ],
  "count": 1
}
```

---

### Mark Settlement Paid (Admin)

```
PATCH /api/v1/settlements/{settlement_id}/pay
```

Mark a settlement as paid with a transaction reference. **Requires admin API key.**

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `payment_tx` | string | yes | Transaction hash or reference |

**Response (200):**

```json
{"status": "completed", "payment_tx": "0xabc123..."}
```

**Error (404):** Settlement not found or already paid.

---

## Team Management

### Create Team

```
POST /api/v1/teams
```

Create a new team. **Requires API key.**

**Request Body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | yes | -- | Team name (max 200 chars) |
| `description` | string | no | `""` | Team description |
| `config` | object | no | `{}` | Custom configuration |

**Response (201):**

```json
{"id": "team-uuid", "name": "My AI Team", "owner_id": "my-agent"}
```

---

### List Teams

```
GET /api/v1/teams
```

List teams owned by the authenticated user. **Requires API key.** Returns empty list if unauthenticated.

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | integer | `50` | Max results (1--100) |

---

### Get Team

```
GET /api/v1/teams/{team_id}
```

Get team details including members, routing rules, and quality gates. **No authentication required.**

**Response (200):**

```json
{
  "id": "team-uuid",
  "name": "My AI Team",
  "owner_id": "my-agent",
  "description": "NLP processing team",
  "config": {},
  "status": "active",
  "created_at": "2026-03-19T10:00:00",
  "updated_at": "2026-03-19T10:00:00",
  "members": [
    {"id": "...", "agent_id": "...", "role": "worker", "skills": ["nlp"]}
  ],
  "routing_rules": [
    {"id": "...", "name": "NLP tasks", "keywords": ["nlp", "text"], "target_agent_id": "...", "priority": 10}
  ],
  "quality_gates": [
    {"id": "...", "gate_type": "quality_score", "threshold": 8.5, "gate_order": 0}
  ]
}
```

---

### Update Team

```
PATCH /api/v1/teams/{team_id}
```

Update team details. **Owner only; requires API key.**

**Request Body (all optional):**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | New name |
| `description` | string | New description |
| `config` | object | Updated configuration |

---

### Delete Team

```
DELETE /api/v1/teams/{team_id}
```

Archive a team (soft delete). **Owner only; requires API key.**

**Response (200):**

```json
{"status": "archived", "id": "team-uuid"}
```

---

### Add Team Member

```
POST /api/v1/teams/{team_id}/members
```

Add a member to a team. **Owner only; requires API key.**

**Request Body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `agent_id` | string | yes | -- | Agent to add |
| `role` | string | no | `"worker"` | `leader`, `worker`, `reviewer`, or `router` |
| `skills` | string[] | no | `[]` | Agent's skills for routing |

**Response (201):**

```json
{"id": "member-uuid", "team_id": "team-uuid", "agent_id": "agent-uuid"}
```

---

### List Team Members

```
GET /api/v1/teams/{team_id}/members
```

**No authentication required.**

**Response (200):**

```json
{
  "members": [
    {"id": "...", "team_id": "...", "agent_id": "...", "role": "worker", "skills": ["nlp"], "joined_at": "..."}
  ],
  "count": 3
}
```

---

### Remove Team Member

```
DELETE /api/v1/teams/{team_id}/members/{agent_id}
```

**Owner only; requires API key.**

**Response (200):**

```json
{"status": "removed"}
```

---

### Add Routing Rule

```
POST /api/v1/teams/{team_id}/rules
```

Add a keyword-based routing rule. When incoming tasks match the keywords, they are routed to the target agent. **Owner only; requires API key.**

**Request Body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | yes | -- | Rule name |
| `keywords` | string[] | yes | -- | Keywords that trigger this rule |
| `target_agent_id` | string | yes | -- | Agent to route to |
| `priority` | integer | no | `0` | Higher priority = evaluated first |

**Response (201):**

```json
{"id": "rule-uuid", "name": "NLP tasks"}
```

---

### List Routing Rules

```
GET /api/v1/teams/{team_id}/rules
```

**No authentication required.** Returns rules sorted by priority (descending).

**Response (200):**

```json
{
  "rules": [
    {"id": "...", "name": "NLP tasks", "keywords": ["nlp", "text"], "target_agent_id": "...", "priority": 10, "enabled": true}
  ],
  "count": 2
}
```

---

### Delete Routing Rule

```
DELETE /api/v1/teams/{team_id}/rules/{rule_id}
```

**Owner only; requires API key.**

---

### Add Quality Gate

```
POST /api/v1/teams/{team_id}/gates
```

Add a quality gate to enforce output standards. **Owner only; requires API key.**

**Request Body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `gate_type` | string | yes | -- | `quality_score`, `latency`, `error_rate`, `coverage`, or `custom` |
| `threshold` | float | yes | -- | Threshold value (0.0--10.0) |
| `gate_order` | integer | no | `0` | Execution order (lower = first) |
| `config` | object | no | `{}` | Gate-specific configuration |

**Response (201):**

```json
{"id": "gate-uuid", "gate_type": "quality_score"}
```

---

### List Quality Gates

```
GET /api/v1/teams/{team_id}/gates
```

**No authentication required.** Returns gates sorted by `gate_order` (ascending).

**Response (200):**

```json
{
  "gates": [
    {"id": "...", "gate_type": "quality_score", "threshold": 8.5, "gate_order": 0, "config": {}, "enabled": true}
  ],
  "count": 2
}
```

---

### Delete Quality Gate

```
DELETE /api/v1/teams/{team_id}/gates/{gate_id}
```

**Owner only; requires API key.**

---

## Webhooks

### Subscribe

```
POST /api/v1/webhooks
```

Create a webhook subscription. **Requires API key.**

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | yes | Webhook endpoint URL (**must use HTTPS**) |
| `events` | string[] | yes | Events to subscribe to (see below) |
| `secret` | string | yes | Secret for HMAC-SHA256 payload signing |

**Available Events:**

| Event | Trigger |
|-------|---------|
| `service.called` | A service was called through the proxy |
| `payment.completed` | A payment was successfully processed |
| `reputation.updated` | An agent's reputation score changed |
| `settlement.completed` | A settlement was paid out |

**Example:**

```bash
curl -X POST http://localhost:8000/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {key_id}:{secret}" \
  -d '{
    "url": "https://my-agent.example.com/webhook",
    "events": ["service.called", "payment.completed"],
    "secret": "whsec_my_webhook_secret"
  }'
```

**Response (201):**

```json
{
  "id": "wh-uuid",
  "owner_id": "my-agent",
  "url": "https://my-agent.example.com/webhook",
  "events": ["payment.completed", "service.called"],
  "active": true,
  "created_at": "2026-03-19T10:00:00+00:00"
}
```

**Webhook Payload Format:**

```json
{
  "event": "service.called",
  "payload": {
    "usage_id": "...",
    "service_id": "...",
    "buyer_id": "...",
    "provider_id": "...",
    "amount_usd": 0.05,
    "payment_method": "x402",
    "status_code": 200,
    "latency_ms": 150
  },
  "timestamp": "2026-03-19T10:00:01+00:00",
  "webhook_id": "wh-uuid"
}
```

**Webhook Headers:**

| Header | Description |
|--------|-------------|
| `X-ACF-Signature` | HMAC-SHA256 hex digest of the payload body |
| `X-ACF-Event` | Event name (e.g. `service.called`) |
| `Content-Type` | `application/json` |

**Signature Verification (Python):**

```python
import hmac
import hashlib

def verify_signature(body: bytes, secret: str, signature: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

**Retry Policy:** Failed deliveries are retried up to 3 times with exponential backoff (1s, 2s, 4s).

**Limits:** Maximum 20 webhooks per owner.

---

### List Webhooks

```
GET /api/v1/webhooks
```

List your webhook subscriptions. **Requires API key.**

**Response (200):**

```json
{
  "webhooks": [{ "id": "...", "url": "...", "events": [...], ... }],
  "count": 3
}
```

---

### Unsubscribe

```
DELETE /api/v1/webhooks/{webhook_id}
```

Delete a webhook subscription. **Owner only; requires API key.**

**Response (200):**

```json
{"status": "deleted", "webhook_id": "wh-uuid"}
```

---

## Templates

### List Team Templates

```
GET /api/v1/templates/teams
```

Get pre-built team configurations. **No authentication required.**

**Response (200):**

```json
{
  "templates": [
    {
      "name": "solo",
      "agents": 1,
      "quality_gates": [{"type": "quality_score", "threshold": 7.0}],
      "description": "Single agent for individual developers"
    },
    {
      "name": "small_team",
      "agents": 4,
      "quality_gates": [
        {"type": "quality_score", "threshold": 8.0},
        {"type": "quality_score", "threshold": 8.5}
      ],
      "description": "Collaborative team with keyword routing"
    },
    {
      "name": "enterprise",
      "agents": 6,
      "quality_gates": [
        {"type": "quality_score", "threshold": 8.5},
        {"type": "quality_score", "threshold": 9.0},
        {"type": "quality_score", "threshold": 9.0}
      ],
      "description": "Production-grade with skill-based routing"
    }
  ]
}
```

---

### List Service Templates

```
GET /api/v1/templates/services
```

Get pre-built service configurations. **No authentication required.**

**Response (200):**

```json
{
  "templates": [
    {"name": "ai_api", "category": "ai", "price_per_call": "0.05", "free_tier_calls": 100},
    {"name": "data_pipeline", "category": "data", "price_per_call": "0.10", "free_tier_calls": 50},
    {"name": "content_api", "category": "content", "price_per_call": "0.02", "free_tier_calls": 200}
  ]
}
```

---

## Provider Portal

All provider endpoints require a provider API key.

### Provider Dashboard

```
GET /api/v1/provider/dashboard
```

Provider overview: service count, total calls, revenue, settlements. **Requires provider API key.**

**Response (200):**

```json
{
  "provider_id": "my-agent",
  "total_services": 3,
  "total_calls": 1500,
  "total_revenue": 750.00,
  "total_settled": 600.00,
  "pending_settlement": 150.00
}
```

---

### My Services

```
GET /api/v1/provider/services
```

List provider's own services with usage statistics. **Requires provider API key.**

**Response (200):**

```json
{
  "services": [
    {
      "id": "550e8400-...",
      "name": "CoinSifter API",
      "description": "Crypto scanner",
      "endpoint": "https://api.example.com/v1",
      "price_per_call": "0.50",
      "status": "active",
      "category": "crypto",
      "total_calls": 500,
      "total_revenue": 250.00,
      "avg_latency_ms": 120.5,
      "created_at": "2026-03-01T00:00:00Z"
    }
  ]
}
```

---

### Service Analytics

```
GET /api/v1/provider/services/{service_id}/analytics
```

Detailed analytics for a specific service. **Owner only; requires provider API key.**

**Response (200):**

```json
{
  "service_id": "550e8400-...",
  "service_name": "CoinSifter API",
  "total_calls": 500,
  "total_revenue": 250.00,
  "avg_latency_ms": 120.5,
  "success_rate": 99.2,
  "unique_buyers": 15,
  "first_call": "2026-03-01T10:00:00Z",
  "last_call": "2026-03-19T15:30:00Z",
  "daily": [
    {"date": "2026-03-19", "calls": 30, "revenue": 15.00}
  ]
}
```

**Error (403):** `"Not your service"` — cannot view analytics for other providers' services.

---

### Earnings

```
GET /api/v1/provider/earnings
```

Earnings summary with settlement history. **Requires provider API key.**

**Response (200):**

```json
{
  "total_earned": 1000.00,
  "total_settled": 800.00,
  "pending_settlement": 200.00,
  "settlements": [
    {
      "id": "stl-001",
      "total_amount": 500.00,
      "platform_fee": 50.00,
      "net_amount": 450.00,
      "status": "completed",
      "period_start": "2026-02-01T00:00:00Z",
      "period_end": "2026-02-28T23:59:59Z"
    }
  ]
}
```

---

### My API Keys

```
GET /api/v1/provider/keys
```

List provider's own API keys (secrets are never returned). **Requires provider API key.**

**Response (200):**

```json
{
  "keys": [
    {
      "key_id": "acf_abc123",
      "role": "provider",
      "rate_limit": 60,
      "wallet_address": null,
      "created_at": "2026-03-01T00:00:00Z",
      "expires_at": null
    }
  ]
}
```

---

### Revoke API Key

```
DELETE /api/v1/provider/keys/{key_id}
```

Revoke one of provider's own API keys. **Owner only; requires provider API key.**

**Response (200):**

```json
{"status": "revoked", "key_id": "acf_abc123"}
```

**Error (403):** `"Not your key"` — cannot revoke other providers' keys.

---

### Test Service Endpoint

```
POST /api/v1/provider/services/{service_id}/test
```

Test a service endpoint's connectivity. **Owner only; requires provider API key.** Validates the URL is safe (no SSRF to private/loopback addresses).

**Response (200):**

```json
{
  "service_id": "550e8400-...",
  "endpoint": "https://api.example.com/v1",
  "reachable": true,
  "latency_ms": 120,
  "status_code": 200,
  "error": ""
}
```

Unreachable example:

```json
{
  "service_id": "550e8400-...",
  "endpoint": "https://dead.example.com",
  "reachable": false,
  "latency_ms": 10000,
  "status_code": 0,
  "error": "Connection timed out"
}
```

---

### Onboarding Progress

```
GET /api/v1/provider/onboarding
```

Track provider onboarding progress through 5 steps. **Requires provider API key.**

**Response (200):**

```json
{
  "provider_id": "my-agent",
  "steps": {
    "create_api_key": {"completed": true, "label": "Create API key"},
    "register_service": {"completed": true, "label": "Register your first service"},
    "activate_service": {"completed": true, "label": "Activate a service"},
    "first_traffic": {"completed": false, "label": "Receive first API call"},
    "first_settlement": {"completed": false, "label": "Complete first settlement"}
  },
  "completed_steps": 3,
  "total_steps": 5,
  "completion_pct": 60.0
}
```

---

## Admin

All admin endpoints require an admin API key.

### Platform Stats

```
GET /api/v1/admin/stats
```

Platform overview statistics. **Requires admin API key.**

**Response (200):**

```json
{
  "total_services": 25,
  "total_agents": 50,
  "total_teams": 8,
  "total_usage_records": 15000,
  "total_revenue_usd": 750.50,
  "total_settlements": 12,
  "active_webhooks": 15
}
```

---

### Daily Usage

```
GET /api/v1/admin/usage/daily
```

Daily usage aggregation. **Requires admin API key.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `days` | integer | `30` | Number of days to look back (1--90) |

**Response (200):**

```json
{
  "days": 30,
  "data": [
    {
      "date": "2026-03-19",
      "call_count": 523,
      "revenue_usd": 26.15,
      "unique_buyers": 12,
      "unique_services": 8
    }
  ]
}
```

---

### Provider Ranking

```
GET /api/v1/admin/providers/ranking
```

Rank providers by usage volume and revenue. **Requires admin API key.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | integer | `20` | Max results (1--100) |
| `period` | string | `"all-time"` | `"all-time"` or number of days (e.g. `"30"`) |

**Response (200):**

```json
{
  "period": "all-time",
  "providers": [
    {
      "provider_id": "provider-1",
      "display_name": "Top NLP Agent",
      "total_calls": 5000,
      "total_revenue": 250.0,
      "avg_latency_ms": 120.5,
      "success_rate": 99.8
    }
  ]
}
```

---

### Services Health

```
GET /api/v1/admin/services/health
```

Health overview of all active services. **Requires admin API key.**

**Response (200):**

```json
{
  "services": [
    {
      "service_id": "550e8400-...",
      "name": "Sentiment Analysis",
      "provider_id": "provider-1",
      "status": "active",
      "avg_latency_ms": 120.5,
      "error_rate": 0.5,
      "last_called": "2026-03-19T09:45:00"
    }
  ]
}
```

---

### Payment Summary

```
GET /api/v1/admin/payments/summary
```

Breakdown of usage by payment method. **Requires admin API key.**

**Response (200):**

```json
{
  "methods": {
    "x402": {"count": 8000, "total_usd": 400.0},
    "paypal": {"count": 3000, "total_usd": 250.0},
    "nowpayments": {"count": 500, "total_usd": 100.5}
  }
}
```

---

### Analytics Trends

```
GET /api/v1/admin/analytics/trends
```

Revenue and call trends by day/week/month. **Requires admin API key.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `granularity` | string | `"weekly"` | `"daily"`, `"weekly"`, or `"monthly"` |
| `periods` | integer | `12` | Number of periods to return (1--52) |

**Response (200):**

```json
{
  "granularity": "weekly",
  "data": [
    {
      "period": "2026-W12",
      "calls": 523,
      "revenue": 26.15,
      "unique_buyers": 12,
      "active_services": 8,
      "avg_latency_ms": 95.3,
      "success_rate": 99.5
    }
  ]
}
```

---

### Top Services

```
GET /api/v1/admin/analytics/top-services
```

Top services ranked by revenue, calls, or latency. **Requires admin API key.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | integer | `10` | Max results (1--50) |
| `sort_by` | string | `"revenue"` | `"revenue"`, `"calls"`, or `"latency"` |
| `days` | integer | `30` | Lookback period in days (1--365) |

**Response (200):**

```json
{
  "sort_by": "revenue",
  "days": 30,
  "services": [
    {
      "service_id": "550e8400-...",
      "service_name": "CoinSifter API",
      "provider_id": "judyailab",
      "category": "crypto",
      "total_calls": 500,
      "total_revenue": 250.00,
      "avg_latency_ms": 120.5,
      "unique_buyers": 15,
      "success_rate": 99.2
    }
  ]
}
```

---

### Buyer Metrics

```
GET /api/v1/admin/analytics/buyers
```

Buyer engagement metrics: new, active, repeat, top spenders. **Requires admin API key.**

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `days` | integer | `30` | Lookback period in days (1--365) |

**Response (200):**

```json
{
  "days": 30,
  "total_buyers_all_time": 100,
  "active_buyers": 35,
  "repeat_buyers": 12,
  "repeat_rate": 34.3,
  "avg_calls_per_buyer": 4.2,
  "top_spenders": [
    {
      "buyer_id": "buyer-001",
      "calls": 150,
      "total_spent": 75.00,
      "services_used": 5
    }
  ]
}
```

---

### Provider Commission Info

```
GET /api/v1/admin/providers/{provider_id}/commission
```

Get commission info for a specific provider (Growth Program status). **Requires admin API key.**

**Response (200):**

```json
{
  "provider_id": "provider-001",
  "registered": true,
  "current_rate": "0.00",
  "current_tier": "Month 1 (Free)",
  "registration_date": "2026-03-01T00:00:00Z",
  "month_number": 1,
  "next_tier_date": "2026-04-01T00:00:00Z",
  "next_tier_rate": "0.05"
}
```

---

## Dashboard

### Admin HTML Dashboard

```
GET /admin/dashboard?key={key_id}:{secret}
```

Renders a browser-friendly HTML dashboard with platform metrics, charts, provider rankings, and service health. **Requires admin API key via query parameter** (browser-friendly authentication).

**Query Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `key` | string | yes | Admin API key in `key_id:secret` format |

**Response:** HTML page with:
- Platform stats (services, agents, teams, revenue, settlements)
- Daily usage chart (last 7 days)
- Payment method breakdown
- Top 10 provider rankings
- Service health table

---

## Health

### Health Check

```
GET /health
```

**Response (200):**

```json
{
  "status": "ok",
  "timestamp": "2026-03-19T10:00:00+00:00"
}
```

### Service Info

```
GET /
```

**Response (200):**

```json
{
  "service": "Agent Commerce Framework",
  "version": "0.6.0",
  "docs": "/docs"
}
```

---

## Error Responses

All errors follow a consistent format:

```json
{
  "detail": "Human-readable error message"
}
```

| Status Code | Meaning |
|-------------|---------|
| 400 | Bad request (validation error, invalid input) |
| 401 | Missing or invalid API key |
| 403 | Insufficient permissions (wrong role) |
| 404 | Resource not found |
| 429 | Rate limit exceeded |
| 500 | Internal server error |
| 502 | Provider unreachable (proxy) |
| 504 | Provider timeout (proxy) |

---

## Rate Limiting

All endpoints are rate-limited at **60 requests per minute per IP** with a burst allowance of 120 requests. Per-API-key rate limits are also enforced based on the key's `rate_limit` setting.

When rate-limited, you receive HTTP 429:

```json
{"detail": "Rate limit exceeded. Try again later."}
```

The rate limit resets after the 60-second sliding window.
