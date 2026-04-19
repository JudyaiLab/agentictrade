# CoinSifter API Documentation

**Version:** 1.0  
**Base URL:** `https://agentictrade.io`  
**Authentication:** Bearer Token (key_id:secret)

---

## Overview

CoinSifter provides cryptocurrency market scanning APIs via the AgenticTrade marketplace. Scan 600+ USDT pairs across exchanges with configurable technical indicators.

## Services

| Service | ID | Price | Free Calls |
|---------|-----|-------|------------|
| CoinSifter Demo | `758c1057-191e-405e-a352-7f52bcd97a82` | $0.00 | Unlimited |
| CoinSifter Pro API | `7ed931d1-57fb-4d97-8a27-8efd3dad04a4` | $0.10/call | 10 |
| CoinSifter Scanner | `6a9939cf-583b-4e6d-897f-360dcf200f59` | $0.50/call | 5 |

---

## Quick Start

### Step 1: Create API Key

```bash
curl -X POST https://agentictrade.io/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{"name": "my-app", "type": "buyer"}'
```

Response:
```json
{
  "key_id": "key_abc123",
  "secret": "sk_xyz789...",
  "name": "my-app"
}
```

### Step 2: Call Demo (Free)

```bash
curl https://agentictrade.io/api/v1/proxy/758c1057-191e-405e-a352-7f52bcd97a82/api/demo \
  -H "Authorization: Bearer key_abc123:sk_xyz789..."
```

### Step 3: Upgrade to Pro

```bash
curl https://agentictrade.io/api/v1/proxy/7ed931d1-57fb-4d97-8a27-8efd3dad04a4/api/scan \
  -H "Authorization: Bearer key_abc123:sk_xyz789..."
```

---

## Authentication

All requests require a Bearer token in the format: `key_id:secret`

```bash
curl https://agentictrade.io/api/v1/... \
  -H "Authorization: Bearer YOUR_KEY_ID:YOUR_SECRET"
```

### Creating Keys

```bash
# Create buyer key (for calling services)
POST /api/v1/keys
{"name": "app-name", "type": "buyer"}

# Create provider key (for selling services)
POST /api/v1/keys
{"name": "app-name", "type": "provider"}
```

---

## Endpoints

### GET /api/demo

Free demo endpoint returning sample scan results.

**Service ID:** `758c1057-191e-405e-a352-7f52bcd97a82`

```bash
curl https://agentictrade.io/api/v1/proxy/758c1057-191e-405e-a352-7f52bcd97a82/api/demo \
  -H "Authorization: Bearer YOUR_KEY:SECRET"
```

**Response:**
```json
{
  "coins": [
    {"symbol": "BTCUSDT", "price": 67432.50, "change_24h": 2.34},
    {"symbol": "ETHUSDT", "price": 3521.80, "change_24h": 1.87},
    {"symbol": "BNBUSDT", "price": 598.20, "change_24h": -0.45},
    {"symbol": "SOLUSDT", "price": 142.30, "change_24h": 5.21},
    {"symbol": "XRPUSDT", "price": 0.5234, "change_24h": 0.89}
  ],
  "timestamp": "2026-03-21T12:00:00Z"
}
```

---

### GET /api/scan

Real-time crypto scanner — scans 600+ USDT pairs.

**Service ID:** `7ed931d1-57fb-4d97-8a27-8efd3dad04a4`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| exchange | string | binance | Exchange to scan |
| min_volume | number | 1000000 | Minimum 24h volume (USDT) |
| indicators | string | RSI,EMA | Comma-separated indicators |
| timeframe | string | 1h | candlestick timeframe |

```bash
curl "https://agentictrade.io/api/v1/proxy/7ed931d1-57fb-4d97-8a27-8efd3dad04a4/api/scan?min_volume=5000000&timeframe=4h" \
  -H "Authorization: Bearer YOUR_KEY:SECRET"
```

**Response:**
```json
{
  "scan_id": "scan_abc123",
  "pairs_scanned": 623,
  "results": [
    {
      "symbol": "BTCUSDT",
      "price": 67432.50,
      "volume_24h": 1250000000,
      "rsi": 68.5,
      "ema_20": 67000,
      "macd": {"value": 125.3, "signal": 100.2},
      "signals": ["RSI_OVERBOUGHT", "EMA_BULLISH"],
      "score": 85.5
    }
  ],
  "timestamp": "2026-03-21T12:00:00Z"
}
```

---

### GET /api/full-scan

Full CoinSifter scanner with all indicators and filters.

**Service ID:** `6a9939cf-583b-4e6d-897f-360dcf200f59`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| exchange | string | binance | Exchange |
| pairs | number | 100 | Max pairs to scan |
| indicators | string | all | RSI,EMA,MACD,BB,KD,VOL,ATR |
| timeframe | string | 1h | 15m, 1h, 4h, 1d |
| min_score | number | 50 | Minimum filter score |

```bash
curl "https://agentictrade.io/api/v1/proxy/6a9939cf-583b-4e6d-897f-360dcf200f59/api/full-scan?indicators=RSI,MACD,BB&timeframe=4h&min_score=70" \
  -H "Authorization: Bearer YOUR_KEY:SECRET"
```

**Response:**
```json
{
  "scan_id": "full_xyz789",
  "pairs_scanned": 623,
  "filters_applied": ["RSI", "MACD", "BB"],
  "results": [
    {
      "symbol": "ETHUSDT",
      "price": 3521.80,
      "indicators": {
        "rsi": 72.3,
        "macd": {"value": 25.4, "signal": 15.2, "histogram": 10.2},
        "bollinger_bands": {"upper": 3600, "middle": 3500, "lower": 3400},
        "volume": {"volume_24h": 850000000, "volume_ratio": 1.5}
      },
      "signals": ["RSI_OVERBOUGHT", "BB_UPPER_BREAK", "VOLUME_SPIKE"],
      "score": 82.3,
      "recommendation": "HOLD"
    }
  ],
  "timestamp": "2026-03-21T12:00:00Z"
}
```

---

## Error Codes

| Code | Message | Description |
|------|---------|-------------|
| 401 | Unauthorized | Invalid or missing API key |
| 403 | Forbidden | Insufficient permissions |
| 404 | Not Found | Service or endpoint not found |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Server error |
| 503 | Service Unavailable | Service temporarily down |

**Error Response Format:**
```json
{
  "error": {
    "code": 429,
    "message": "Rate limit exceeded. Upgrade plan for higher limits."
  }
}
```

---

## Rate Limits

| Plan | Requests/minute | Daily Limit |
|------|-----------------|-------------|
| Free | 10 | 100 |
| Pro | 60 | 1,000 |
| Enterprise | 300 | 10,000 |

---

## Pricing

| Service | Price | Free Tier |
|---------|-------|-----------|
| Demo | $0.00 | Unlimited |
| Pro API | $0.10/call | 10 calls |
| Full Scanner | $0.50/call | 5 calls |

Payments processed via:
- **x402** — Crypto payment (USDC, ETH)
- **NOWPayments** — Alternative crypto rails

---

## Code Examples

### Python

```python
import urllib.request
import json

BASE_URL = "https://agentictrade.io"
KEY_ID = "your_key_id"
SECRET = "your_secret"

def call_api(service_id, path):
    url = f"{BASE_URL}/api/v1/proxy/{service_id}/{path}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {KEY_ID}:{SECRET}")
    
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

# Call demo
result = call_api("758c1057-191e-405e-a352-7f52bcd97a82", "api/demo")
print(result["coins"])
```

### Node.js

```javascript
const fetch = require('fetch');

const BASE_URL = 'https://agentictrade.io';
const KEY = 'key_id:secret';

async function callApi(serviceId, path) {
  const res = await fetch(`${BASE_URL}/api/v1/proxy/${serviceId}/${path}`, {
    headers: { 'Authorization': `Bearer ${KEY}` }
  });
  return res.json();
}

// Call demo
const result = await callApi('758c1057-191e-405e-a352-7f52bcd97a82', 'api/demo');
console.log(result.coins);
```

---

## Support

- **Email:** hello@agentictrade.io
- **Discord:** https://discord.gg/judyailab
- **GitHub Issues:** https://github.com/judyailab/coinsifter
