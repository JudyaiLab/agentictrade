# AgenticTrade에 AI API를 등록하는 방법 — 5분 가이드

*게시일: 2026년 3월 | 카테고리: API 제공자용*

---

AI 에이전트가 실제로 돈을 쓰고 있습니다. 그리고 그 대부분은 RapidAPI나 Stripe를 통하거나, 맞춤 빌링 코드라는 블랙홀로 사라지고 있죠. AI, 데이터, 유틸리티 API를 보유하고 있다면, 이 새로운 구매자 계층에 더 빠르게 도달하는 방법이 있습니다: **AgenticTrade에 등록하세요**.

이 가이드를 따라하면 5분 안에 서비스를 마켓에 올릴 수 있습니다. 신용카드도, 빌링 인프라도, 6주짜리 연동 프로젝트도 필요 없습니다.

## AgenticTrade를 선택해야 하는 이유

키보드를 만지기 전에, 비즈니스 근거를 숫자 세 개로 정리합니다:

| Platform | Commission | First Month |
|----------|-----------|-------------|
| Apple App Store | 30% | 30% |
| RapidAPI | 25% | 25% |
| **AgenticTrade** | **10%** | **0%** |

**AgenticTrade는 RapidAPI보다 수수료가 60% 낮습니다** — 게다가 첫 달은 완전 무료입니다. 에이전트가 자동으로 서비스를 발견하고 결제하는 동안, 수익의 100%를 가져가세요.

플랫폼이 처리하는 것들:
- **서비스 검색** — MCP 네이티브 서비스 레지스트리, 에이전트와 사람 모두 검색 가능
- **결제** — 멀티레일 지원: USDC (x402), 300+ 암호화폐 (NOWPayments), 법정화폐 (PayPal)
- **미터링** — 호출 단위 추적과 실시간 사용량 분석
- **평판** — 자동 품질 점수 (지연시간, 가동시간, 안정성) 누적

API만 만드세요. 나머지는 저희가 처리합니다.

---

## 사전 준비

- 수익화할 API (AI 모델 endpoint, 데이터 피드, 도구 서비스 등)
- AgenticTrade 계정 ([agentictrade.io](https://agentictrade.io)에서 무료 가입)
- curl 또는 아무 HTTP 클라이언트

이게 전부입니다.

---

## Step 1: 가입하고 API Key 받기

[agentictrade.io](https://agentictrade.io)에서 가입한 뒤 **Dashboard → API Keys → New Key**로 이동하세요.

`acf_live_xxx` 형식의 키를 복사합니다:

```
acf_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

## Step 2: 서비스 등록하기

`POST` 한 번이면 API를 등록할 수 있습니다. 호출당 $0.01의 암호화폐 감성 분석 API를 판매한다고 가정한 실제 예시입니다:

```bash
curl -X POST https://agentictrade.io/api/v1/services \
  -H "Authorization: Bearer acf_live_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Crypto Sentiment API",
    "description": "Real-time social sentiment scores for 500+ crypto assets, powered by NLP analysis of Twitter, Reddit, and Telegram.",
    "base_url": "https://api.yourservice.com/v1",
    "price_per_call": 0.01,
    "currency": "USD",
    "payment_rail": "x402",
    "category": "data",
    "tags": ["crypto", "sentiment", "nlp", "trading"],
    "documentation_url": "https://docs.yourservice.com"
  }'
```

**응답:**

```json
{
  "id": "svc_4a8f2c1d9e3b",
  "name": "Crypto Sentiment API",
  "status": "active",
  "price_per_call": 0.01,
  "commission_rate": 0.0,
  "commission_rate_note": "Month 1: 0% commission — keep 100% of revenue",
  "marketplace_url": "https://agentictrade.io/marketplace/svc_4a8f2c1d9e3b",
  "created_at": "2026-03-20T13:00:00Z"
  }
```

서비스가 **즉시 활성화**됩니다. 플랫폼이 고유 ID(`svc_xxx`)를 부여하고 첫 달 수수료를 자동으로 0%로 설정합니다.

---

## Step 3: Proxy용 두 번째 API Key 생성

여기서부터 강력해집니다. 구매자에게 실제 API endpoint를 직접 노출하는 대신, AgenticTrade가 자체 인프라를 통해 요청을 프록시합니다. 이렇게 되면:

- 구매자가 실제 URL을 볼 수 없습니다 (보안 + 속도 제한)
- 결제가 endpoint 호출 전에 자동으로 처리됩니다
- 사용량이 투명하게 측정됩니다

Proxy 접근용 키를 생성하세요:

```bash
curl -X POST https://agentictrade.io/api/v1/api-keys \
  -H "Authorization: Bearer acf_live_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Proxy Key — Sentiment API",
    "scopes": ["proxy:svc_4a8f2c1d9e3b"],
    "rate_limit": 300
  }'
```

**응답:**

```json
{
  "id": "key_7f2d9a3c1e8b",
  "key": "acp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "name": "Proxy Key — Sentiment API",
  "scopes": ["proxy:svc_4a8f2c1d9e3b"],
  "rate_limit": 300,
  "created_at": "2026-03-20T13:02:00Z"
}
```

이 proxy key가 구매자에게 전달됩니다. 구매자는 이 키로 AgenticTrade의 proxy endpoint를 통해 서비스를 호출합니다.

---

## Step 4: (선택) MCP Tool Descriptor 생성

AI 에이전트가 MCP(Model Context Protocol)를 통해 네이티브로 서비스를 발견하게 하려면, tool descriptor를 등록하세요:

```bash
curl -X POST https://agentictrade.io/api/v1/mcp/tools \
  -H "Authorization: Bearer acf_live_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "service_id": "svc_4a8f2c1d9e3b",
    "name": "get_crypto_sentiment",
    "description": "Get real-time sentiment score for a cryptocurrency asset. Returns a -100 (extremely bearish) to +100 (extremely bullish) score.",
    "input_schema": {
      "type": "object",
      "properties": {
        "symbol": {
          "type": "string",
          "description": "Crypto asset symbol, e.g. BTC, ETH, SOL"
        },
        "timeframe": {
          "type": "string",
          "enum": ["1h", "24h", "7d"],
          "default": "24h"
        }
      },
      "required": ["symbol"]
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "symbol": {"type": "string"},
        "sentiment_score": {"type": "number"},
        "confidence": {"type": "number"},
        "sources_analyzed": {"type": "integer"}
      }
    }
  }'
```

이제 Claude, GPT, 또는 MCP 호환 프레임워크로 만든 어떤 AI 에이전트든 여러분의 도구를 네이티브로 검색하고 호출할 수 있습니다.

---

## 구매자가 서비스를 호출하는 방법

구매자 입장에서 AgenticTrade를 통한 서비스 호출은 이렇게 보입니다:

```bash
# 구매자가 AgenticTrade proxy를 통해 서비스 호출
curl -X POST https://agentictrade.io/api/v1/proxy/svc_4a8f2c1d9e3b/sentiment \
  -H "Authorization: Bearer acp_buyer_proxy_key" \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTC", "timeframe": "24h"}'

# API 응답이 그대로 반환됩니다:
# {
#   "symbol": "BTC",
#   "sentiment_score": 42,
#   "confidence": 0.87,
#   "sources_analyzed": 18420
# }
```

플랫폼이 결제 헤더(`X-ACF-Amount`, `X-ACF-Free-Tier` 등)를 자동으로 처리합니다. Endpoint는 데이터만 반환하면 됩니다.

---

## 수익 대시보드

실시간으로 수익을 확인하세요:

```bash
curl https://agentictrade.io/api/v1/settlements \
  -H "Authorization: Bearer acf_live_your_key_here"
```

```json
{
  "total_revenue": 847.32,
  "total_calls": 84732,
  "pending_settlement": 12.50,
  "last_payout": {
    "amount": 234.82,
    "currency": "USDC",
    "timestamp": "2026-03-15T00:00:00Z"
  },
  "commission_history": [
    {"month": "2026-01", "rate": "0%", "revenue": 0.00},
    {"month": "2026-02", "rate": "5%", "revenue": 38.47},
    {"month": "2026-03", "rate": "10%", "revenue": 808.85}
  ]
}
```

정산은 Base 네트워크의 USDC로 자동 처리되며, 수동 인보이스가 필요 없습니다.

---

## 수수료 체계: 실제 비용

**Provider Growth Program**은 초기에 비용 부담을 줄이도록 설계되었습니다:

| Period | Commission | You Keep |
|--------|-----------|----------|
| Month 1 | **0%** | 100% |
| Months 2–3 | **5%** | 95% |
| Month 4 onwards | **10%** | 90% |

RapidAPI와 비교하면: 첫 날부터 25%를 내야 하고, 단계적 할인도 없습니다.

월 $1,000 API 매출 기준:
- **RapidAPI**: $750 수취
- **AgenticTrade (4개월 이후)**: $900 수취
- **절감액**: 월 $150 = 연간 $1,800

---

## 플랫폼에 포함된 기능

10% (또는 5%, 또는 0%)로 얻는 것들:

| Feature | Included |
|---------|----------|
| Service listing + discovery | ✅ |
| MCP tool registry | ✅ |
| Multi-rail payment (crypto + fiat) | ✅ |
| Per-call metering + analytics | ✅ |
| Reputation engine (latency, uptime, reliability scores) | ✅ |
| Automatic USDC settlements | ✅ |
| Rate limiting + SSRF protection | ✅ |
| Webhook notifications (payment.completed, service.called) | ✅ |
| Team management + routing rules | ✅ |
| 24/7 proxy infrastructure | ✅ |

이 중 어느 것도 직접 구축할 필요가 없습니다. 플랫폼에 기본 탑재되어 있습니다.

---

## 자주 묻는 질문

**Q: x402 프로토콜을 직접 연동해야 하나요?**
아닙니다. AgenticTrade가 서버 측에서 결제 프로토콜 협상을 처리합니다. API 응답만 반환하세요. 플랫폼이 빌링 헤더를 주입합니다.

**Q: API 운영 비용이 비싼 경우에는?**
가격을 직접 설정할 수 있습니다. `price_per_call`을 인프라 비용에 마진을 더한 금액으로 설정하세요. 플랫폼은 비율을 가져갈 뿐, 수익에 상한을 두지 않습니다.

**Q: 언제든 서비스를 내릴 수 있나요?**
네. API 호출 한 번이면 비활성화됩니다. 약정도 위약금도 없습니다.

**Q: 정산 주기는 어떻게 되나요?**
매주 USDC로 자동 정산됩니다. 엔터프라이즈 제공자는 맞춤 정산 주기를 요청할 수 있습니다.

---

## 다음 단계

1. [agentictrade.io](https://agentictrade.io)에서 **무료 가입**
2. **첫 서비스 등록** — 2분이면 됩니다
3. AI 에이전트 개발자에게 **마켓플레이스 URL 공유**
4. 대시보드에서 **수익이 쌓이는 걸 확인**

첫 달은 무료입니다. 등록하지 않을 이유가 없습니다.

---

*AgenticTrade는 JudyAI Lab이 만들었습니다. 플랫폼은 [agentictrade.io](https://agentictrade.io)에서 운영 중입니다.*
