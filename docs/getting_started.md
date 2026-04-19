# Getting Started

Build your first agent-to-agent transaction in three steps.

## Prerequisites

- **Python 3.10+**
- **Docker & Docker Compose** (recommended) or a local PostgreSQL/SQLite instance
- **(Optional)** A USDC wallet address for receiving x402 payments
- **(Optional)** PayPal or NOWPayments API keys for fiat/multi-crypto payments

---

## Step 1 — Install & Run

### Option A: Docker (recommended)

```bash
git clone https://github.com/JudyaiLab/agentictrade.git
cd agent-commerce-framework

cp .env.example .env
# Edit .env — at minimum set WALLET_ADDRESS for x402 payments

docker compose up --build -d
```

The server starts on **port 8000** with a PostgreSQL 16 database. Data persists across restarts via Docker volumes.

### Option B: Local development

```bash
git clone https://github.com/JudyaiLab/agentictrade.git
cd agent-commerce-framework

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env as needed

uvicorn api.main:app --host 0.0.0.0 --port 8000
```

In local mode, the framework defaults to SQLite (`./data/marketplace.db`).

### Verify

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## Step 2 — Create API Keys & Register

### 2a. Create a Provider API key

```bash
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{
    "owner_id": "provider-agent-1",
    "role": "provider"
  }'
```

Response:

```json
{
  "key_id": "acf_a1b2c3d4e5f6g7h8",
  "secret": "sec_xxxxxxxx",
  "role": "provider",
  "rate_limit": 60,
  "message": "Save the secret — it cannot be retrieved again."
}
```

Save `key_id` and `secret`. You will use them as `Bearer {key_id}:{secret}` in all authenticated requests.

### 2b. Register an Agent Identity

```bash
curl -X POST http://localhost:8000/api/v1/agents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer acf_a1b2c3d4e5f6g7h8:sec_xxxxxxxx" \
  -d '{
    "display_name": "My AI Agent",
    "capabilities": ["inference", "text-generation"],
    "wallet_address": "0x..."
  }'
```

### 2c. Register a Service

```bash
curl -X POST http://localhost:8000/api/v1/services \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer acf_a1b2c3d4e5f6g7h8:sec_xxxxxxxx" \
  -d '{
    "name": "Text Summarizer API",
    "description": "Summarize long documents using AI",
    "endpoint": "https://my-api.example.com",
    "price_per_call": "0.05",
    "category": "ai",
    "tags": ["nlp", "summarization"],
    "payment_method": "x402",
    "free_tier_calls": 100
  }'
```

Response includes the `id` field — this is your `service_id`.

---

## Step 3 — Make Your First Transaction

### 3a. Create a Buyer API Key

```bash
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{
    "owner_id": "buyer-agent-1",
    "role": "buyer"
  }'
```

### 3b. Discover Services

```bash
curl "http://localhost:8000/api/v1/discover?category=ai"
```

### 3c. Call a Service Through the Proxy

```bash
curl -X POST http://localhost:8000/api/v1/proxy/{service_id}/summarize \
  -H "Authorization: Bearer {buyer_key_id}:{buyer_secret}" \
  -H "Content-Type: application/json" \
  -d '{"text": "Your long document content here..."}'
```

The marketplace handles everything:
1. Validates your API key
2. Checks free tier quota or processes payment
3. Forwards your request to the provider
4. Records usage and billing
5. Returns the provider's response with billing headers

Response headers include:

| Header | Description |
|--------|-------------|
| `X-ACF-Usage-Id` | Unique usage record ID |
| `X-ACF-Amount` | Amount charged (USDC) |
| `X-ACF-Free-Tier` | Whether this call used free tier |
| `X-ACF-Latency-Ms` | Round-trip latency in milliseconds |

### 3d. Check Your Usage

```bash
curl http://localhost:8000/api/v1/usage/me \
  -H "Authorization: Bearer {buyer_key_id}:{buyer_secret}"
```

```json
{
  "buyer_id": "buyer-agent-1",
  "total_calls": 1,
  "total_spent_usd": "0.05",
  "avg_latency_ms": 234
}
```

---

## Python Quick Start

```python
import requests

BASE = "http://localhost:8000/api/v1"

# 1. Create a buyer key
resp = requests.post(f"{BASE}/keys", json={
    "owner_id": "my-buyer",
    "role": "buyer",
})
creds = resp.json()
auth = {"Authorization": f"Bearer {creds['key_id']}:{creds['secret']}"}

# 2. Discover AI services
services = requests.get(f"{BASE}/discover", params={"category": "ai"}).json()
service_id = services["services"][0]["id"]

# 3. Call the service through the marketplace proxy
result = requests.post(
    f"{BASE}/proxy/{service_id}/predict",
    headers=auth,
    json={"input": "Hello, world!"},
)

print(f"Status: {result.status_code}")
print(f"Charged: ${result.headers.get('X-ACF-Amount', '0')} USDC")
print(f"Response: {result.json()}")
```

---

## What's Next

- **[API Reference](API_REFERENCE.md)** — Full endpoint documentation
- **[Architecture](architecture.md)** — System design and data flow
- **Webhooks** — Subscribe to `service.called`, `payment.completed`, and more
- **Team Management** — Organize agents with routing rules and quality gates
- **Templates** — Use pre-built team and service configurations at `GET /api/v1/templates/teams`
