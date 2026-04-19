# API 레퍼런스

Base URL: `http://localhost:8000`

모든 인증이 필요한 endpoint는 다음 header를 포함해야 합니다:

```
Authorization: Bearer {key_id}:{secret}
```

---

## 목차

- [인증](#인증)
- [서비스 레지스트리](#서비스-레지스트리)
- [검색](#검색)
- [결제 프록시](#결제-프록시)
- [Agent 신원](#agent-신원)
- [평판](#평판)
- [정산](#정산)
- [팀 관리](#팀-관리)
- [Webhook](#webhook)
- [템플릿](#템플릿)
- [제공자 포털](#제공자-포털)
- [관리자](#관리자)
- [대시보드](#대시보드)
- [헬스 체크](#헬스-체크)
- [에러 응답](#에러-응답)
- [속도 제한](#속도-제한)

---

## 인증

### API Key 생성

```
POST /api/v1/keys
```

새 API key를 생성합니다. **Buyer key는 인증 없이 생성 가능합니다.** Provider 및 admin key는 기존 인증된 Bearer token이 필요합니다.

**요청 본문:**

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `owner_id` | string | 예 | -- | key 소유자의 고유 식별자 |
| `role` | string | 아니오 | `"buyer"` | `buyer`, `provider`, `admin` 중 하나 |
| `rate_limit` | integer | 아니오 | `60` | 분당 최대 요청 수 |
| `wallet_address` | string | 아니오 | `null` | USDC 지갑 주소 |

**예시:**

```bash
# Buyer key 생성 (인증 불필요)
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{"owner_id": "my-agent", "role": "buyer"}'

# Provider key 생성 (인증 필요)
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {existing_key_id}:{existing_secret}" \
  -d '{"owner_id": "my-agent", "role": "provider"}'
```

**응답 (201):**

```json
{
  "key_id": "acf_a1b2c3d4e5f6g7h8",
  "secret": "sec_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "role": "provider",
  "rate_limit": 60,
  "message": "Save the secret — it cannot be retrieved again."
}
```

> **중요:** `secret`은 한 번만 반환됩니다. 안전하게 보관하십시오.

---

### API Key 검증

```
POST /api/v1/keys/validate
```

API key 쌍을 검증합니다. 유효한 경우 소유자 및 역할 정보를 반환합니다.

**요청 본문:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `key_id` | string | 예 | key ID |
| `secret` | string | 예 | key secret |

**응답 (200):**

```json
{
  "valid": true,
  "owner_id": "my-agent",
  "role": "provider",
  "rate_limit": 60
}
```

**에러 (401):** 유효하지 않은 자격 증명.

---

## 서비스 레지스트리

### 서비스 등록

```
POST /api/v1/services
```

마켓플레이스에 새 서비스를 등록합니다. **Provider 또는 admin API key가 필요합니다.**

**요청 본문:**

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `name` | string | 예 | -- | 서비스 이름 |
| `description` | string | 아니오 | `""` | 서비스 설명 |
| `endpoint` | string | 예 | -- | 제공자의 API URL (`https://` 또는 `http://`로 시작해야 함) |
| `price_per_call` | string | 예 | -- | 호출당 가격 (예: `"0.05"`) |
| `category` | string | 아니오 | `""` | 카테고리 (예: `ai`, `data`, `content`) |
| `tags` | string[] | 아니오 | `[]` | 검색 가능한 태그 |
| `payment_method` | string | 아니오 | `"x402"` | `x402`, `paypal`, 또는 `nowpayments` |
| `free_tier_calls` | integer | 아니오 | `0` | buyer당 무료 호출 횟수 |

**예시:**

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

**응답 (201):**

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

### 서비스 목록 조회

```
GET /api/v1/services
```

서비스를 조회하고 검색합니다. **인증 불필요.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `query` | string | -- | 이름 및 설명 내 텍스트 검색 |
| `category` | string | -- | 카테고리별 필터링 |
| `status` | string | `"active"` | 상태별 필터링 (`active`, `paused`, `removed`) |
| `limit` | integer | `50` | 최대 결과 수 (1--100) |
| `offset` | integer | `0` | 페이지네이션 오프셋 |

**응답 (200):**

```json
{
  "services": [{ "id": "...", "name": "...", "pricing": {...}, ... }],
  "count": 10,
  "offset": 0,
  "limit": 50
}
```

---

### 서비스 상세 조회

```
GET /api/v1/services/{service_id}
```

단일 서비스의 상세 정보를 조회합니다. **인증 불필요.**

**응답 (200):** 서비스 객체 (등록 응답과 동일한 구조).

**에러 (404):** 서비스를 찾을 수 없음.

---

### 서비스 수정

```
PATCH /api/v1/services/{service_id}
```

서비스를 수정합니다. **소유자 전용; provider API key가 필요합니다.**

**요청 본문 (모두 선택 사항):**

| 필드 | 타입 | 설명 |
|------|------|------|
| `name` | string | 새 이름 |
| `description` | string | 새 설명 |
| `endpoint` | string | 새 endpoint URL |
| `price_per_call` | string | 새 가격 |
| `status` | string | `active`, `paused`, 또는 `removed` |
| `category` | string | 새 카테고리 |
| `tags` | string[] | 새 태그 |

**응답 (200):** 수정된 서비스 객체.

---

### 서비스 삭제

```
DELETE /api/v1/services/{service_id}
```

서비스를 소프트 삭제합니다 (상태를 `removed`로 설정). **소유자 전용; provider API key가 필요합니다.**

**응답 (200):**

```json
{"status": "removed", "id": "550e8400-..."}
```

---

## 검색

### 서비스 검색 (고급)

```
GET /api/v1/discover
```

전문 검색, 필터, 정렬을 활용한 고급 서비스 검색. **인증 불필요.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `q` | string | -- | 전문 검색 쿼리 |
| `category` | string | -- | 카테고리별 필터링 |
| `tags` | string | -- | 쉼표로 구분된 태그 필터 (예: `nlp,sentiment`) |
| `min_price` | string | -- | 호출당 최소 가격 (예: `"0.01"`) |
| `max_price` | string | -- | 호출당 최대 가격 (예: `"1.00"`) |
| `payment_method` | string | -- | `x402`, `paypal`, 또는 `nowpayments` |
| `has_free_tier` | boolean | -- | `true`로 설정하면 무료 호출이 있는 서비스만 필터링 |
| `sort_by` | string | `"created_at"` | 정렬 기준: `created_at`, `price`, 또는 `name` |
| `limit` | integer | `50` | 최대 결과 수 (1--100) |
| `offset` | integer | `0` | 페이지네이션 오프셋 |

**예시:**

```bash
curl "http://localhost:8000/api/v1/discover?q=nlp&category=ai&has_free_tier=true&sort_by=price&limit=10"
```

**응답 (200):**

```json
{
  "services": [{ "id": "...", "name": "...", "pricing": {...}, ... }],
  "total": 25,
  "offset": 0,
  "limit": 10
}
```

---

### 카테고리 목록

```
GET /api/v1/discover/categories
```

활성 서비스 수와 함께 모든 서비스 카테고리를 조회합니다. **인증 불필요.**

**응답 (200):**

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

### 인기 서비스

```
GET /api/v1/discover/trending
```

사용량 기준으로 인기 서비스를 조회합니다. **인증 불필요.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `limit` | integer | `10` | 최대 결과 수 |

**응답 (200):**

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

### 맞춤 추천

```
GET /api/v1/discover/recommendations/{agent_id}
```

Agent의 사용 이력에 기반한 서비스 추천을 제공합니다. Agent가 가장 많이 사용하는 카테고리의 서비스 중 아직 사용하지 않은 서비스를 반환합니다. **인증 불필요.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `limit` | integer | `5` | 최대 추천 수 |

**응답 (200):**

```json
{
  "recommendations": [{ "id": "...", "name": "...", ... }],
  "count": 5
}
```

---

## 결제 프록시

### 프록시 요청

```
ANY /api/v1/proxy/{service_id}/{path}
```

자동 결제 처리와 함께 서비스 제공자에게 요청을 전달합니다. `GET`, `POST`, `PUT`, `PATCH`, `DELETE`를 지원합니다. **Buyer 또는 provider API key가 필요합니다.**

마켓플레이스가 전체 결제 흐름을 처리합니다:

1. API key를 검증하고 속도 제한을 확인합니다
2. 서비스 및 가격 정보를 조회합니다
3. 무료 티어 할당량을 확인합니다 (buyer별, 서비스별)
4. 설정된 결제 제공자(x402/PayPal/NOWPayments)를 통해 결제를 생성합니다
5. 요청을 제공자의 endpoint로 전달합니다
6. 사용량 및 청구 정보를 기록합니다
7. Webhook 이벤트를 발송합니다 (`service.called`)
8. 청구 header가 포함된 제공자의 응답을 반환합니다

**경로 매개변수:**

| 매개변수 | 설명 |
|----------|------|
| `service_id` | 호출할 서비스 |
| `path` | 제공자의 endpoint에 추가될 경로 |

**예시:**

```bash
curl -X POST http://localhost:8000/api/v1/proxy/{service_id}/analyze \
  -H "Authorization: Bearer {key_id}:{secret}" \
  -H "Content-Type: application/json" \
  -d '{"text": "Analyze this document"}'
```

**응답:** 제공자의 원본 응답에 다음 청구 header가 추가됩니다:

| Header | 설명 |
|--------|------|
| `X-ACF-Usage-Id` | 고유 사용 기록 ID |
| `X-ACF-Amount` | 청구 금액 (예: `"0.05"`) |
| `X-ACF-Free-Tier` | 무료 티어 범위 내인 경우 `"true"` |
| `X-ACF-Latency-Ms` | 왕복 지연 시간 (밀리초) |

**에러 코드:**

| 코드 | 의미 |
|------|------|
| 401 | API key 누락 또는 유효하지 않음 |
| 404 | 서비스를 찾을 수 없음 |
| 429 | 속도 제한 초과 |
| 502 | 제공자에 연결할 수 없음 |
| 504 | 제공자 응답 시간 초과 |

---

### 내 사용량 조회

```
GET /api/v1/usage/me
```

인증된 buyer의 사용 통계를 조회합니다. **API key가 필요합니다.**

**응답 (200):**

```json
{
  "buyer_id": "my-agent",
  "total_calls": 42,
  "total_spent_usd": "2.10",
  "avg_latency_ms": 205.3
}
```

---

## Agent 신원

### Agent 등록

```
POST /api/v1/agents
```

새 Agent 신원을 등록합니다. **API key가 필요합니다.**

**요청 본문:**

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `display_name` | string | 예 | -- | Agent 표시 이름 |
| `identity_type` | string | 아니오 | `"api_key_only"` | `api_key_only`, `kya_jwt`, 또는 `did_vc` |
| `capabilities` | string[] | 아니오 | `[]` | 선언된 기능 |
| `wallet_address` | string | 아니오 | `null` | USDC 지갑 주소 |
| `metadata` | object | 아니오 | `{}` | 사용자 정의 메타데이터 |

**예시:**

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

**응답 (201):**

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

### Agent 목록 조회

```
GET /api/v1/agents
```

Agent 목록을 조회합니다. **인증 불필요.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `status` | string | `"active"` | 상태별 필터링 |
| `limit` | integer | `50` | 최대 결과 수 (1--100) |
| `offset` | integer | `0` | 페이지네이션 오프셋 |

**응답 (200):**

```json
{
  "agents": [{ "agent_id": "...", "display_name": "...", ... }],
  "count": 10
}
```

---

### Agent 검색

```
GET /api/v1/agents/search
```

이름 또는 ID로 Agent를 검색합니다. **인증 불필요.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `q` | string | `""` | 검색 쿼리 |
| `limit` | integer | `20` | 최대 결과 수 (1--100) |

**응답 (200):**

```json
{
  "agents": [{ "agent_id": "...", "display_name": "...", ... }],
  "count": 3
}
```

---

### Agent 상세 조회

```
GET /api/v1/agents/{agent_id}
```

Agent 상세 정보를 조회합니다. **인증 불필요.**

**응답 (200):** Agent 객체 (등록 응답과 동일한 구조, `owner_id` 제외).

**에러 (404):** Agent를 찾을 수 없음.

---

### Agent 수정

```
PATCH /api/v1/agents/{agent_id}
```

Agent를 수정합니다. **소유자 전용; API key가 필요합니다.**

**요청 본문 (모두 선택 사항):**

| 필드 | 타입 | 설명 |
|------|------|------|
| `display_name` | string | 새 표시 이름 |
| `capabilities` | string[] | 수정된 기능 |
| `wallet_address` | string | 새 지갑 주소 |
| `status` | string | `active`, `suspended`, 또는 `deactivated` |
| `metadata` | object | 수정된 메타데이터 |

**응답 (200):** 수정된 Agent 객체.

---

### Agent 비활성화

```
DELETE /api/v1/agents/{agent_id}
```

Agent를 소프트 비활성화합니다. **소유자 전용; API key가 필요합니다.**

**응답 (200):**

```json
{"status": "deactivated", "agent_id": "550e8400-..."}
```

---

### Agent 인증 (관리자)

```
POST /api/v1/agents/{agent_id}/verify
```

Agent를 인증 완료로 표시합니다. **Admin API key가 필요합니다.**

**응답 (200):** `"verified": true`가 포함된 Agent 객체.

---

## 평판

평판 점수는 실제 사용 데이터를 기반으로 자동 산출됩니다. 사용자 평가나 수동 입력은 없습니다.

**점수 산출 공식:**

| 구성 요소 | 가중치 | 계산 방법 |
|-----------|--------|-----------|
| 지연 시간 점수 | 30% | `10.0 - (avg_latency_ms / 1000)`, [0, 10] 범위로 제한 |
| 신뢰성 점수 | 40% | `success_rate / 10`, [0, 10] 범위로 제한 |
| 응답 품질 | 30% | `(1 - error_rate/100) * 10`, [0, 10] 범위로 제한 |
| **종합** | -- | 세 구성 요소의 가중 평균 |

### Agent 평판 조회

```
GET /api/v1/agents/{agent_id}/reputation
```

Agent의 평판 점수를 조회합니다. **인증 불필요.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `period` | string | `"all-time"` | `"all-time"` 또는 `"YYYY-MM"` 형식 |
| `compute` | boolean | `false` | `true`인 경우 실시간 사용 데이터로 재계산 |

**응답 (200):**

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

### 서비스 평판 조회

```
GET /api/v1/services/{service_id}/reputation
```

특정 서비스의 평판 기록을 조회합니다. **인증 불필요.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `period` | string | `"all-time"` | `"all-time"` 또는 `"YYYY-MM"` 형식 |

**응답 (200):**

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

### 평판 리더보드

```
GET /api/v1/reputation/leaderboard
```

평판 점수 기준 상위 Agent를 조회합니다. **인증 불필요.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `limit` | integer | `20` | 최대 결과 수 (1--100) |

**응답 (200):**

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

## 정산

### 정산 생성 (관리자)

```
POST /api/v1/settlements
```

특정 기간 동안의 제공자 수익에 대한 정산을 생성합니다. 사용 기록을 집계하고, 플랫폼 수수료를 계산하며, 지급 기록을 생성합니다. **Admin API key가 필요합니다.**

**요청 본문:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `provider_id` | string | 예 | 정산할 제공자 |
| `period_start` | string | 예 | ISO 8601 시작 날짜 (예: `"2026-03-01T00:00:00Z"`) |
| `period_end` | string | 예 | ISO 8601 종료 날짜 |

**응답 (201):**

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

### 정산 목록 조회

```
GET /api/v1/settlements
```

정산 목록을 조회합니다. 제공자는 자신의 정산만, 관리자는 모든 정산을 볼 수 있습니다. **API key가 필요합니다.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `provider_id` | string | -- | 제공자별 필터링 (관리자 전용) |
| `status` | string | -- | 필터: `pending`, `processing`, `completed`, `failed` |
| `limit` | integer | `50` | 최대 결과 수 (1--100) |

**응답 (200):**

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

### 정산 지급 완료 처리 (관리자)

```
PATCH /api/v1/settlements/{settlement_id}/pay
```

트랜잭션 참조와 함께 정산을 지급 완료로 표시합니다. **Admin API key가 필요합니다.**

**요청 본문:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `payment_tx` | string | 예 | 트랜잭션 해시 또는 참조 |

**응답 (200):**

```json
{"status": "completed", "payment_tx": "0xabc123..."}
```

**에러 (404):** 정산을 찾을 수 없거나 이미 지급 완료됨.

---

## 팀 관리

### 팀 생성

```
POST /api/v1/teams
```

새 팀을 생성합니다. **API key가 필요합니다.**

**요청 본문:**

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `name` | string | 예 | -- | 팀 이름 (최대 200자) |
| `description` | string | 아니오 | `""` | 팀 설명 |
| `config` | object | 아니오 | `{}` | 사용자 정의 설정 |

**응답 (201):**

```json
{"id": "team-uuid", "name": "My AI Team", "owner_id": "my-agent"}
```

---

### 팀 목록 조회

```
GET /api/v1/teams
```

인증된 사용자가 소유한 팀 목록을 조회합니다. **API key가 필요합니다.** 인증되지 않은 경우 빈 목록을 반환합니다.

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `limit` | integer | `50` | 최대 결과 수 (1--100) |

---

### 팀 상세 조회

```
GET /api/v1/teams/{team_id}
```

멤버, 라우팅 규칙, 품질 게이트를 포함한 팀 상세 정보를 조회합니다. **인증 불필요.**

**응답 (200):**

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

### 팀 수정

```
PATCH /api/v1/teams/{team_id}
```

팀 정보를 수정합니다. **소유자 전용; API key가 필요합니다.**

**요청 본문 (모두 선택 사항):**

| 필드 | 타입 | 설명 |
|------|------|------|
| `name` | string | 새 이름 |
| `description` | string | 새 설명 |
| `config` | object | 수정된 설정 |

---

### 팀 삭제

```
DELETE /api/v1/teams/{team_id}
```

팀을 아카이브합니다 (소프트 삭제). **소유자 전용; API key가 필요합니다.**

**응답 (200):**

```json
{"status": "archived", "id": "team-uuid"}
```

---

### 팀 멤버 추가

```
POST /api/v1/teams/{team_id}/members
```

팀에 멤버를 추가합니다. **소유자 전용; API key가 필요합니다.**

**요청 본문:**

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `agent_id` | string | 예 | -- | 추가할 Agent |
| `role` | string | 아니오 | `"worker"` | `leader`, `worker`, `reviewer`, 또는 `router` |
| `skills` | string[] | 아니오 | `[]` | 라우팅을 위한 Agent의 스킬 |

**응답 (201):**

```json
{"id": "member-uuid", "team_id": "team-uuid", "agent_id": "agent-uuid"}
```

---

### 팀 멤버 목록 조회

```
GET /api/v1/teams/{team_id}/members
```

**인증 불필요.**

**응답 (200):**

```json
{
  "members": [
    {"id": "...", "team_id": "...", "agent_id": "...", "role": "worker", "skills": ["nlp"], "joined_at": "..."}
  ],
  "count": 3
}
```

---

### 팀 멤버 제거

```
DELETE /api/v1/teams/{team_id}/members/{agent_id}
```

**소유자 전용; API key가 필요합니다.**

**응답 (200):**

```json
{"status": "removed"}
```

---

### 라우팅 규칙 추가

```
POST /api/v1/teams/{team_id}/rules
```

키워드 기반 라우팅 규칙을 추가합니다. 수신된 작업이 키워드와 일치하면 대상 Agent로 라우팅됩니다. **소유자 전용; API key가 필요합니다.**

**요청 본문:**

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `name` | string | 예 | -- | 규칙 이름 |
| `keywords` | string[] | 예 | -- | 이 규칙을 트리거하는 키워드 |
| `target_agent_id` | string | 예 | -- | 라우팅 대상 Agent |
| `priority` | integer | 아니오 | `0` | 높은 우선순위 = 먼저 평가 |

**응답 (201):**

```json
{"id": "rule-uuid", "name": "NLP tasks"}
```

---

### 라우팅 규칙 목록 조회

```
GET /api/v1/teams/{team_id}/rules
```

**인증 불필요.** 우선순위 기준 내림차순으로 정렬된 규칙을 반환합니다.

**응답 (200):**

```json
{
  "rules": [
    {"id": "...", "name": "NLP tasks", "keywords": ["nlp", "text"], "target_agent_id": "...", "priority": 10, "enabled": true}
  ],
  "count": 2
}
```

---

### 라우팅 규칙 삭제

```
DELETE /api/v1/teams/{team_id}/rules/{rule_id}
```

**소유자 전용; API key가 필요합니다.**

---

### 품질 게이트 추가

```
POST /api/v1/teams/{team_id}/gates
```

출력 품질 기준을 적용하기 위한 품질 게이트를 추가합니다. **소유자 전용; API key가 필요합니다.**

**요청 본문:**

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `gate_type` | string | 예 | -- | `quality_score`, `latency`, `error_rate`, `coverage`, 또는 `custom` |
| `threshold` | float | 예 | -- | 임계값 (0.0--10.0) |
| `gate_order` | integer | 아니오 | `0` | 실행 순서 (낮을수록 먼저 실행) |
| `config` | object | 아니오 | `{}` | 게이트별 설정 |

**응답 (201):**

```json
{"id": "gate-uuid", "gate_type": "quality_score"}
```

---

### 품질 게이트 목록 조회

```
GET /api/v1/teams/{team_id}/gates
```

**인증 불필요.** `gate_order` 기준 오름차순으로 정렬된 게이트를 반환합니다.

**응답 (200):**

```json
{
  "gates": [
    {"id": "...", "gate_type": "quality_score", "threshold": 8.5, "gate_order": 0, "config": {}, "enabled": true}
  ],
  "count": 2
}
```

---

### 품질 게이트 삭제

```
DELETE /api/v1/teams/{team_id}/gates/{gate_id}
```

**소유자 전용; API key가 필요합니다.**

---

## Webhook

### 구독

```
POST /api/v1/webhooks
```

Webhook 구독을 생성합니다. **API key가 필요합니다.**

**요청 본문:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `url` | string | 예 | Webhook endpoint URL (**HTTPS 필수**) |
| `events` | string[] | 예 | 구독할 이벤트 (아래 참조) |
| `secret` | string | 예 | HMAC-SHA256 페이로드 서명용 시크릿 |

**사용 가능한 이벤트:**

| 이벤트 | 트리거 |
|--------|--------|
| `service.called` | 프록시를 통해 서비스가 호출됨 |
| `payment.completed` | 결제가 성공적으로 처리됨 |
| `reputation.updated` | Agent의 평판 점수가 변경됨 |
| `settlement.completed` | 정산이 지급 완료됨 |

**예시:**

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

**응답 (201):**

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

**Webhook 페이로드 형식:**

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

**Webhook Header:**

| Header | 설명 |
|--------|------|
| `X-ACF-Signature` | 페이로드 본문의 HMAC-SHA256 16진수 다이제스트 |
| `X-ACF-Event` | 이벤트 이름 (예: `service.called`) |
| `Content-Type` | `application/json` |

**서명 검증 (Python):**

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

**재시도 정책:** 전송 실패 시 지수 백오프(1초, 2초, 4초)로 최대 3회 재시도합니다.

**제한:** 소유자당 최대 20개의 webhook.

---

### Webhook 목록 조회

```
GET /api/v1/webhooks
```

Webhook 구독 목록을 조회합니다. **API key가 필요합니다.**

**응답 (200):**

```json
{
  "webhooks": [{ "id": "...", "url": "...", "events": [...], ... }],
  "count": 3
}
```

---

### 구독 해제

```
DELETE /api/v1/webhooks/{webhook_id}
```

Webhook 구독을 삭제합니다. **소유자 전용; API key가 필요합니다.**

**응답 (200):**

```json
{"status": "deleted", "webhook_id": "wh-uuid"}
```

---

## 템플릿

### 팀 템플릿 목록

```
GET /api/v1/templates/teams
```

사전 구성된 팀 설정을 조회합니다. **인증 불필요.**

**응답 (200):**

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

### 서비스 템플릿 목록

```
GET /api/v1/templates/services
```

사전 구성된 서비스 설정을 조회합니다. **인증 불필요.**

**응답 (200):**

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

## 제공자 포털

모든 제공자 endpoint는 provider API key가 필요합니다.

### 제공자 대시보드

```
GET /api/v1/provider/dashboard
```

제공자 개요: 서비스 수, 총 호출 수, 수익, 정산. **Provider API key가 필요합니다.**

**응답 (200):**

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

### 내 서비스

```
GET /api/v1/provider/services
```

사용 통계가 포함된 제공자의 서비스 목록을 조회합니다. **Provider API key가 필요합니다.**

**응답 (200):**

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

### 서비스 분석

```
GET /api/v1/provider/services/{service_id}/analytics
```

특정 서비스의 상세 분석. **소유자 전용; provider API key가 필요합니다.**

**응답 (200):**

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

**에러 (403):** `"Not your service"` — 다른 제공자의 서비스 분석을 볼 수 없습니다.

---

### 수익

```
GET /api/v1/provider/earnings
```

정산 이력이 포함된 수익 요약. **Provider API key가 필요합니다.**

**응답 (200):**

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

### 내 API Key

```
GET /api/v1/provider/keys
```

제공자의 API key 목록을 조회합니다 (secret은 반환되지 않습니다). **Provider API key가 필요합니다.**

**응답 (200):**

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

### API Key 폐기

```
DELETE /api/v1/provider/keys/{key_id}
```

제공자 자신의 API key를 폐기합니다. **소유자 전용; provider API key가 필요합니다.**

**응답 (200):**

```json
{"status": "revoked", "key_id": "acf_abc123"}
```

**에러 (403):** `"Not your key"` — 다른 제공자의 key를 폐기할 수 없습니다.

---

### 서비스 Endpoint 테스트

```
POST /api/v1/provider/services/{service_id}/test
```

서비스 endpoint의 연결성을 테스트합니다. **소유자 전용; provider API key가 필요합니다.** URL이 안전한지 검증합니다 (사설/루프백 주소로의 SSRF 차단).

**응답 (200):**

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

접속 불가 예시:

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

### 온보딩 진행 현황

```
GET /api/v1/provider/onboarding
```

5단계의 제공자 온보딩 진행 현황을 추적합니다. **Provider API key가 필요합니다.**

**응답 (200):**

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

## 관리자

모든 관리자 endpoint는 admin API key가 필요합니다.

### 플랫폼 통계

```
GET /api/v1/admin/stats
```

플랫폼 개요 통계. **Admin API key가 필요합니다.**

**응답 (200):**

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

### 일일 사용량

```
GET /api/v1/admin/usage/daily
```

일일 사용량 집계. **Admin API key가 필요합니다.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `days` | integer | `30` | 조회 기간 (일수, 1--90) |

**응답 (200):**

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

### 제공자 순위

```
GET /api/v1/admin/providers/ranking
```

사용량 및 수익 기준으로 제공자를 순위 매깁니다. **Admin API key가 필요합니다.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `limit` | integer | `20` | 최대 결과 수 (1--100) |
| `period` | string | `"all-time"` | `"all-time"` 또는 일수 (예: `"30"`) |

**응답 (200):**

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

### 서비스 상태

```
GET /api/v1/admin/services/health
```

모든 활성 서비스의 상태 개요. **Admin API key가 필요합니다.**

**응답 (200):**

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

### 결제 요약

```
GET /api/v1/admin/payments/summary
```

결제 수단별 사용량 분석. **Admin API key가 필요합니다.**

**응답 (200):**

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

### 분석 트렌드

```
GET /api/v1/admin/analytics/trends
```

일/주/월 기준 수익 및 호출 트렌드. **Admin API key가 필요합니다.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `granularity` | string | `"weekly"` | `"daily"`, `"weekly"`, 또는 `"monthly"` |
| `periods` | integer | `12` | 반환할 기간 수 (1--52) |

**응답 (200):**

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

### 인기 서비스

```
GET /api/v1/admin/analytics/top-services
```

수익, 호출 수, 또는 지연 시간 기준 상위 서비스. **Admin API key가 필요합니다.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `limit` | integer | `10` | 최대 결과 수 (1--50) |
| `sort_by` | string | `"revenue"` | `"revenue"`, `"calls"`, 또는 `"latency"` |
| `days` | integer | `30` | 조회 기간 (일수, 1--365) |

**응답 (200):**

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

### 구매자 지표

```
GET /api/v1/admin/analytics/buyers
```

구매자 참여 지표: 신규, 활성, 반복, 상위 지출자. **Admin API key가 필요합니다.**

**Query Parameter:**

| 매개변수 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `days` | integer | `30` | 조회 기간 (일수, 1--365) |

**응답 (200):**

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

### 제공자 수수료 정보

```
GET /api/v1/admin/providers/{provider_id}/commission
```

특정 제공자의 수수료 정보 (성장 프로그램 상태). **Admin API key가 필요합니다.**

**응답 (200):**

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

## 대시보드

### 관리자 HTML 대시보드

```
GET /admin/dashboard?key={key_id}:{secret}
```

플랫폼 지표, 차트, 제공자 순위, 서비스 상태가 포함된 브라우저 친화적 HTML 대시보드를 렌더링합니다. **Query parameter를 통한 admin API key가 필요합니다** (브라우저 친화적 인증).

**Query Parameter:**

| 매개변수 | 타입 | 필수 | 설명 |
|----------|------|------|------|
| `key` | string | 예 | `key_id:secret` 형식의 admin API key |

**응답:** 다음을 포함하는 HTML 페이지:
- 플랫폼 통계 (서비스, Agent, 팀, 수익, 정산)
- 일일 사용량 차트 (최근 7일)
- 결제 수단 분석
- 상위 10 제공자 순위
- 서비스 상태 테이블

---

## 헬스 체크

### 상태 확인

```
GET /health
```

**응답 (200):**

```json
{
  "status": "ok",
  "timestamp": "2026-03-19T10:00:00+00:00"
}
```

### 서비스 정보

```
GET /
```

**응답 (200):**

```json
{
  "service": "Agent Commerce Framework",
  "version": "0.6.0",
  "docs": "/docs"
}
```

---

## 에러 응답

모든 에러는 일관된 형식을 따릅니다:

```json
{
  "detail": "Human-readable error message"
}
```

| 상태 코드 | 의미 |
|-----------|------|
| 400 | 잘못된 요청 (유효성 검사 오류, 잘못된 입력) |
| 401 | API key 누락 또는 유효하지 않음 |
| 403 | 권한 부족 (역할 불일치) |
| 404 | 리소스를 찾을 수 없음 |
| 429 | 속도 제한 초과 |
| 500 | 내부 서버 오류 |
| 502 | 제공자에 연결할 수 없음 (프록시) |
| 504 | 제공자 응답 시간 초과 (프록시) |

---

## 속도 제한

모든 endpoint는 **IP당 분당 60회 요청**으로 제한되며, 버스트 허용량은 120회입니다. API key별 속도 제한도 key의 `rate_limit` 설정에 따라 적용됩니다.

속도 제한 초과 시 HTTP 429를 수신합니다:

```json
{"detail": "Rate limit exceeded. Try again later."}
```

속도 제한은 60초 슬라이딩 윈도우 이후 초기화됩니다.
