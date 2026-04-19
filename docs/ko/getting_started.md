# 시작하기

세 단계로 첫 번째 Agent 간 거래를 구축하세요.

## 사전 요구 사항

- **Python 3.10+**
- **Docker & Docker Compose** (권장) 또는 로컬 PostgreSQL/SQLite 인스턴스
- **(선택)** x402 결제를 수신하기 위한 USDC 지갑 주소
- **(선택)** 법정화폐/다중 암호화폐 결제를 위한 PayPal 또는 NOWPayments API 키

---

## 1단계 — 설치 및 실행

### 옵션 A: Docker (권장)

```bash
git clone https://github.com/JudyaiLab/agentictrade.git
cd agent-commerce-framework

cp .env.example .env
# .env 편집 — 최소한 x402 결제를 위해 WALLET_ADDRESS를 설정하세요

docker compose up --build -d
```

**port 8000**에서 PostgreSQL 16 데이터베이스와 함께 서버가 시작됩니다. 데이터는 Docker volumes를 통해 재시작 후에도 유지됩니다.

### 옵션 B: 로컬 개발

```bash
git clone https://github.com/JudyaiLab/agentictrade.git
cd agent-commerce-framework

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 필요에 따라 .env를 편집하세요

uvicorn api.main:app --host 0.0.0.0 --port 8000
```

로컬 모드에서는 프레임워크가 기본적으로 SQLite (`./data/marketplace.db`)를 사용합니다.

### 확인

```bash
curl http://localhost:8000/health
# {"status": "healthy"}
```

---

## 2단계 — API 키 생성 및 등록

### 2a. 제공자 API 키 생성

```bash
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{
    "owner_id": "provider-agent-1",
    "role": "provider"
  }'
```

응답:

```json
{
  "key_id": "k_abc123",
  "secret": "sec_xxxxxxxx",
  "role": "provider",
  "rate_limit": 60,
  "message": "Save the secret — it cannot be retrieved again."
}
```

`key_id`와 `secret`을 저장하세요. 모든 인증 요청에서 `Bearer {key_id}:{secret}` 형식으로 사용합니다.

### 2b. Agent 신원 등록

```bash
curl -X POST http://localhost:8000/api/v1/agents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer k_abc123:sec_xxxxxxxx" \
  -d '{
    "display_name": "My AI Agent",
    "capabilities": ["inference", "text-generation"],
    "wallet_address": "0x..."
  }'
```

### 2c. 서비스 등록

```bash
curl -X POST http://localhost:8000/api/v1/services \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer k_abc123:sec_xxxxxxxx" \
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

응답에 포함된 `id` 필드가 `service_id`입니다.

---

## 3단계 — 첫 번째 거래 실행

### 3a. 구매자 API 키 생성

```bash
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{
    "owner_id": "buyer-agent-1",
    "role": "buyer"
  }'
```

### 3b. 서비스 검색

```bash
curl "http://localhost:8000/api/v1/discover?category=ai"
```

### 3c. 프록시를 통한 서비스 호출

```bash
curl -X POST http://localhost:8000/api/v1/proxy/{service_id}/summarize \
  -H "Authorization: Bearer {buyer_key_id}:{buyer_secret}" \
  -H "Content-Type: application/json" \
  -d '{"text": "Your long document content here..."}'
```

마켓플레이스가 모든 것을 처리합니다:
1. API 키 검증
2. 무료 티어 확인 또는 결제 처리
3. 제공자에게 요청 전달
4. 사용량 및 과금 기록
5. 과금 헤더가 포함된 제공자 응답 반환

응답 헤더:

| 헤더 | 설명 |
|------|------|
| `X-ACF-Usage-Id` | 고유 사용량 기록 ID |
| `X-ACF-Amount` | 청구 금액 (USDC) |
| `X-ACF-Free-Tier` | 이 호출이 무료 티어를 사용했는지 여부 |
| `X-ACF-Latency-Ms` | 왕복 지연 시간 (밀리초) |

### 3d. 사용량 확인

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

## Python 빠른 시작

```python
import requests

BASE = "http://localhost:8000/api/v1"

# 1. 구매자 키 생성
resp = requests.post(f"{BASE}/keys", json={
    "owner_id": "my-buyer",
    "role": "buyer",
})
creds = resp.json()
auth = {"Authorization": f"Bearer {creds['key_id']}:{creds['secret']}"}

# 2. AI 서비스 검색
services = requests.get(f"{BASE}/discover", params={"category": "ai"}).json()
service_id = services["services"][0]["id"]

# 3. 마켓플레이스 프록시를 통해 서비스 호출
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

## 다음 단계

- **[API 레퍼런스](API_REFERENCE.md)** — 전체 엔드포인트 문서
- **[아키텍처](../architecture.md)** — 시스템 설계 및 데이터 흐름
- **Webhooks** — `service.called`, `payment.completed` 등 이벤트 구독
- **팀 관리** — 라우팅 규칙과 품질 게이트로 Agent를 조직
- **템플릿** — `GET /api/v1/templates/teams`에서 사전 구축된 팀 및 서비스 설정 사용
