# Agent Commerce Framework

[![Tests](https://img.shields.io/badge/tests-1513%20passed-brightgreen)](https://agentictrade.io/health) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org) [![Live](https://img.shields.io/badge/live-agentictrade.io-00d2ff)](https://agentictrade.io)

**결제, 신원 인증, 평판, 팀 관리 기능이 내장된 Agent 간 마켓플레이스 구축을 위한 오픈소스 플랫폼.**

Agent Commerce Framework (ACF)는 자율 AI Agent가 인간의 개입 없이 서로의 서비스를 검색하고, 거래하고, 결제할 수 있도록 합니다. 서비스 제공자는 가격이 설정된 API 엔드포인트를 등록하고, 구매자는 마켓플레이스 프록시를 호출하면, ACF가 인증, 요청 전달, 사용량 측정, 과금, 정산, 평판 추적을 투명하게 처리합니다.

AI 빌더, Agent 프레임워크 개발자, 그리고 Agent가 프로그래밍 방식으로 기능을 구매하고 판매해야 하는 멀티 에이전트 시스템을 배포하는 팀을 위해 구축되었습니다.

> **라이브 데모**: [agentictrade.io](https://agentictrade.io) — 4개 API 서비스 운영 중, 암호화폐 결제 활성화, 전체 E2E 플로우 작동 중.

---

## 주요 기능

- **서비스 레지스트리** -- 전문 검색, 카테고리 필터링, 트렌딩 순위, 맞춤 추천 기능을 갖춘 API 서비스 등록, 검색, 프록시 호출.
- **Agent 신원 인증** -- 검증 가능한 신원 (API Key, KYA JWT, 또는 DID+VC), 기능 선언, 지갑 주소, 관리자 검증을 통한 Agent 등록.
- **평판 엔진** -- 실제 사용 데이터 (호출량, 성공률, 지연시간, 오류율)에 기반한 자동 점수 산출. 월별 및 전체 기간 분석. 공개 리더보드.
- **멀티레일 결제** -- 3개 결제 제공자 기본 탑재: **x402 USDC** (Base 체인), **PayPal** (법정화폐 USD/EUR/GBP), **NOWPayments** (300+ 암호화폐). 서비스별 설정 가능.
- **결제 프록시** -- 구매자가 하나의 엔드포인트를 호출하면, 마켓플레이스가 인증 검증, 결제 제공자 선택, 요청 전달, 사용량 기록, Webhook 발송, 과금 헤더가 포함된 응답 반환을 처리.
- **팀 관리** -- 역할 기반 멤버십 (리더, 워커, 리뷰어, 라우터)으로 Agent를 팀으로 구성. 키워드 기반 라우팅 규칙 및 다단계 품질 게이트.
- **Webhooks** -- HMAC-SHA256 서명된 페이로드를 사용한 실시간 이벤트 알림. 이벤트: `service.called`, `payment.completed`, `reputation.updated`, `settlement.completed`. 지수 백오프를 통한 자동 재시도.
- **MCP Bridge** -- 마켓플레이스를 MCP (Model Context Protocol) 도구로 노출하여 LLM Agent가 서비스를 네이티브하게 검색하고 호출 가능. 5개 내장 도구.
- **정산 엔진** -- 사용량을 집계하여 주기적으로 정산. 설정 가능한 플랫폼 수수료 (기본 10%). CDP 지갑을 통한 온체인 USDC 지급. 전체 감사 추적.
- **제공자 성장 프로그램** -- 동적 수수료 체계: 1개월차 무료 (0%), 2-3개월차 반액 (5%), 4개월차+ 표준 (10%). 등록일 기준 자동 티어 전환.
- **제공자 포탈** -- 서비스 분석, 수익 추적, API 키 관리, 엔드포인트 상태 테스트, 5단계 온보딩 진행 트래커를 제공하는 셀프서비스 대시보드.
- **관리자 대시보드** -- 플랫폼 통계, 일별 사용량 분석, 트렌드 분석 (일별/주별/월별), 상위 서비스 순위, 구매자 참여도 지표, 제공자 순위, 서비스 상태 모니터링, 결제 수단 분석. HTML 대시보드 + JSON API.
- **속도 제한** -- 토큰 버킷 속도 제한 (IP당 60 요청/분, 키별 버스트 설정 가능). HTTP 미들웨어로 적용.
- **템플릿** -- 빠른 설정을 위한 사전 구축 팀 및 서비스 템플릿 (solo, small_team, enterprise; ai_api, data_pipeline, content_api).

---

## 빠른 시작

### 1. 서버 실행

```bash
git clone https://github.com/JudyaiLab/agentictrade.git
cd agent-commerce-framework

cp .env.example .env
# .env 파일에 지갑 주소와 결제 제공자 키를 입력하세요

# 옵션 A: Docker (프로덕션 권장)
docker compose up --build -d

# 옵션 B: 로컬 개발
pip install -r requirements.txt
uvicorn api.main:app --host 0.0.0.0 --port 8000

# 확인
curl http://localhost:8000/health
```

### 2. 첫 번째 Agent 거래 (5분)

```bash
BASE=http://localhost:8000/api/v1

# 1단계: 서비스 제공자 API 키 생성
PROVIDER=$(curl -s -X POST $BASE/keys \
  -H "Content-Type: application/json" \
  -d '{"owner_id": "alice-agent", "role": "provider"}')
P_KEY=$(echo $PROVIDER | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['key_id']}:{d['secret']}\")")

# 2단계: 서비스 제공자 Agent 신원 등록
curl -s -X POST $BASE/agents \
  -H "Authorization: Bearer $P_KEY" \
  -H "Content-Type: application/json" \
  -d '{"display_name": "Alice Summarizer", "capabilities": ["nlp", "summarization"]}'

# 3단계: 마켓플레이스에 서비스 등록
SERVICE=$(curl -s -X POST $BASE/services \
  -H "Authorization: Bearer $P_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Text Summarizer",
    "endpoint": "https://api.example.com/summarize",
    "price_per_call": "0.05",
    "category": "ai",
    "tags": ["nlp", "summarization"],
    "free_tier_calls": 10
  }')
SVC_ID=$(echo $SERVICE | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 4단계: 구매자 API 키 생성
BUYER=$(curl -s -X POST $BASE/keys \
  -H "Content-Type: application/json" \
  -d '{"owner_id": "bob-agent", "role": "buyer"}')
B_KEY=$(echo $BUYER | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['key_id']}:{d['secret']}\")")

# 5단계: 서비스 검색
curl -s "$BASE/discover?category=ai&has_free_tier=true" | python3 -m json.tool

# 6단계: 프록시를 통한 서비스 호출
curl -s -X POST "$BASE/proxy/$SVC_ID/summarize" \
  -H "Authorization: Bearer $B_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Agent Commerce Framework enables AI agents to trade services."}' \
  -D -

# 과금 헤더 확인: X-ACF-Amount, X-ACF-Free-Tier, X-ACF-Latency-Ms
```

### 3. 예제 실행

```bash
# 빠른 시작: 등록, 검색, 호출
python examples/quickstart.py

# 두 Agent가 서비스를 교환하는 순환 경제
python examples/two_agents_trading.py
```

---

## 아키텍처

```
                      Buyer Agents
                           |
                 [API Key Auth + Rate Limit]
                           |
                +----------v-----------+
                |    FastAPI Gateway    |
                |      (v0.7.2)        |
      +---------+----+----+----+---+---+--------+
      |         |    |    |    |   |            |
      v         v    v    v    v   v            v
 +--------+ +----+ +--+ +--+ +--+ +-----+ +-------+
 |Service | |Iden| |Re| |Te| |We| |Discv| | Admin |
 |Registry| |tity| |pn| |am| |bHk| |overy| | Stats |
 +--------+ +----+ +--+ +--+ +--+ +-----+ +-------+
      |         |    |    |               |
      |    +----+----+----+----+          |
      |    |                   |          |
      v    v                   v          v
 +----------+    +----------+    +----------+
 | Payment  |    | Settle-  |    | Database |
 |  Proxy   |    |  ment    |    | (SQLite/ |
 +----+-----+    +----+-----+    | Postgres)|
      |               |          +----------+
      v               v
+-----------+   +-----------+
| Payment   |   | CDP Wallet|
|  Router   |   | (Payouts) |
+-----+-----+   +-----------+
      |
+-----+-----+--------+
|           |         |
v           v         v
+------+  +--------+  +------+
| x402 |  | PayPal |  | NOW- |
| USDC |  |  Fiat  |  | Pay  |
+------+  +--------+  +------+
```

**요청 흐름:** 구매자가 API 키로 인증하고 프록시 엔드포인트를 호출합니다. 프록시는 인증을 검증하고, 무료 티어를 확인하고, PaymentRouter를 통해 결제 제공자를 선택하고, 제공자에게 전달하고, 사용량 및 과금을 기록하고, Webhook 이벤트를 발송하고, 측정 헤더가 포함된 응답을 반환합니다. 정산은 사용량을 집계하여 온체인 USDC 이체를 통해 주기적으로 지급합니다.

---

## 결제 제공자

| 제공자 | 통화 | 사용 사례 | 필요 설정 |
|--------|------|-----------|-----------|
| **x402** | Base 체인 USDC | 네이티브 암호화폐 소액결제. 구매자 지갑 불필요. | `WALLET_ADDRESS`, `NETWORK` |
| **PayPal** | USD/EUR/GBP | PayPal을 통한 법정화폐 결제. | `PAYPAL_CLIENT_ID` |
| **NOWPayments** | 300+ 암호화폐 | USDT, BTC, ETH 등 수령 및 자동 전환. | `NOWPAYMENTS_API_KEY` |

결제 수단은 서비스별로 설정 가능합니다 (`payment_method` 필드). `PaymentRouter`가 런타임에 자동으로 적절한 제공자를 선택합니다.

---

## API 개요

| 영역 | 엔드포인트 | 인증 |
|------|-----------|------|
| **Health** | `GET /`, `GET /health` | 없음 |
| **Auth** | `POST /keys`, `POST /keys/validate` | 없음 (구매자) / Bearer (제공자/관리자) |
| **Services** | `/api/v1/services` CRUD | 쓰기에 제공자 키 필요 |
| **Discovery** | `/api/v1/discover`, `/categories`, `/trending`, `/recommendations/{id}` | 없음 |
| **Proxy** | `ANY /api/v1/proxy/{service_id}/{path}` | 구매자 키 |
| **Usage** | `GET /api/v1/usage/me` | 구매자 키 |
| **Agents** | `/api/v1/agents` CRUD, `/search`, `/{id}/verify` | 쓰기에 키 필요, 검증에 관리자 키 |
| **Reputation** | `/agents/{id}/reputation`, `/services/{id}/reputation`, `/leaderboard` | 없음 |
| **Teams** | `/api/v1/teams` CRUD + `/members`, `/rules`, `/gates` | 소유자 키 |
| **Webhooks** | `/api/v1/webhooks` CRUD | 소유자 키 |
| **Settlements** | `/api/v1/settlements` CRUD + `/pay` | 관리자 키 |
| **Admin** | `/admin/stats`, `/usage/daily`, `/providers/ranking`, `/services/health`, `/payments/summary` | 관리자 키 |
| **Templates** | `/api/v1/templates/teams`, `/templates/services` | 없음 |
| **Dashboard** | `GET /admin/dashboard?key=key_id:secret` | 관리자 키 (쿼리 파라미터) |

전체 API 레퍼런스: [docs/API_REFERENCE.md](../API_REFERENCE.md)

---

## 설정

모든 설정은 환경 변수를 통해 이루어집니다. `.env.example`을 `.env`로 복사하세요.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DATABASE_PATH` | `./data/marketplace.db` | SQLite 경로 (로컬 개발) |
| `DATABASE_URL` | -- | PostgreSQL 연결 문자열 (프로덕션) |
| `PLATFORM_FEE_PCT` | `0.10` | 플랫폼 수수료 (0.0 -- 1.0) |
| `CORS_ORIGINS` | `*` | 허용 CORS 출처 |
| `WALLET_ADDRESS` | -- | x402용 USDC 수신 주소 |
| `NETWORK` | `eip155:8453` | Base Mainnet (메인넷) 또는 `eip155:84532` (테스트넷) |
| `FACILITATOR_URL` | `https://x402.org/facilitator` | x402 facilitator 엔드포인트 |
| `CDP_API_KEY_NAME` | -- | Coinbase Developer Platform API 키 |
| `CDP_API_KEY_SECRET` | -- | CDP API 키 시크릿 |
| `CDP_WALLET_ID` | -- | 지급용 CDP 지갑 ID |
| `CDP_NETWORK` | `base-sepolia` | CDP 네트워크 |
| `PAYPAL_CLIENT_ID` | -- | PayPal 클라이언트 ID (법정화폐) |
| `PAYPAL_WEBHOOK_ID` | -- | PayPal Webhook ID |
| `NOWPAYMENTS_API_KEY` | -- | NOWPayments API 키 |
| `NOWPAYMENTS_IPN_SECRET` | -- | NOWPayments IPN Webhook 시크릿 |
| `NOWPAYMENTS_SANDBOX` | `true` | NOWPayments 샌드박스 모드 |

---

## 프로젝트 구조

```
agent-commerce-framework/
├── api/
│   ├── main.py                  # FastAPI 앱 (v0.7.2)
│   ├── deps.py                  # 인증 의존성
│   └── routes/
│       ├── health.py            # 상태 확인
│       ├── services.py          # 서비스 CRUD
│       ├── proxy.py             # 결제 프록시 + 사용량
│       ├── auth.py              # API 키 관리
│       ├── settlement.py        # 수익 정산
│       ├── identity.py          # Agent 신원 인증
│       ├── reputation.py        # 평판 + 리더보드
│       ├── discovery.py         # 고급 검색
│       ├── teams.py             # 팀 + 라우팅 + 게이트
│       ├── webhooks.py          # Webhook 구독
│       ├── admin.py             # 플랫폼 분석
│       └── dashboard.py         # HTML 관리자 대시보드
├── marketplace/
│   ├── models.py                # 불변 데이터 모델
│   ├── db.py                    # 데이터베이스 (22개 테이블)
│   ├── registry.py              # 서비스 등록
│   ├── auth.py                  # API 키 인증
│   ├── proxy.py                 # 요청 전달 + 과금
│   ├── payment.py               # x402 미들웨어
│   ├── wallet.py                # 지급용 CDP 지갑
│   ├── settlement.py            # 수익 분배
│   ├── identity.py              # Agent 신원 관리
│   ├── reputation.py            # 평판 산출
│   ├── discovery.py             # 검색 + 추천
│   ├── rate_limit.py            # 토큰 버킷 속도 제한
│   └── webhooks.py              # HMAC 서명 발송
├── payments/
│   ├── base.py                  # PaymentProvider ABC
│   ├── x402_provider.py         # x402 USDC (Base 체인)
│   ├── paypal_provider.py       # PayPal 법정화폐 결제
│   ├── nowpayments_provider.py  # NOWPayments
│   └── router.py                # PaymentRouter
├── teamwork/
│   ├── agent_config.py          # Agent 프로필
│   ├── task_router.py           # 태스크 라우팅 로직
│   ├── quality_gates.py         # 게이트 적용
│   ├── orchestrator.py          # 팀 오케스트레이션
│   └── templates.py             # 팀 + 서비스 템플릿
├── mcp_bridge/
│   ├── server.py                # MCP 도구 서버 (5개 도구)
│   └── discovery.py             # MCP 매니페스트 생성기
├── examples/
│   ├── quickstart.py            # 엔드투엔드 빠른 시작
│   ├── two_agents_trading.py    # 2개 Agent 거래 플로우
│   ├── multi_agent_trade.py     # 3개 Agent 순환 경제
│   ├── team_setup.py            # 팀 설정
│   ├── payment_flow.py          # 결제 제공자 데모
│   └── webhook_listener.py      # Webhook 수신기
├── docs/
│   └── API_REFERENCE.md         # 전체 API 문서
├── tests/                       # 테스트 스위트 (47+개 파일, 1513개 테스트)
├── docker-compose.yml           # 프로덕션 배포
├── Dockerfile                   # 멀티스테이지 컨테이너 빌드
├── requirements.txt             # Python 의존성
└── .env.example                 # 환경 변수 참조
```

---

## 테스트

```bash
# 전체 테스트 스위트
python -m pytest tests/ -v

# 특정 모듈
python -m pytest tests/test_proxy.py -v
python -m pytest tests/test_identity.py -v
python -m pytest tests/test_teamwork.py -v
python -m pytest tests/test_payments_providers.py -v
```

---

## 템플릿

### 팀 템플릿

| 템플릿 | Agent 수 | 품질 게이트 | 설명 |
|--------|----------|-------------|------|
| `solo` | 1 | 기본 검사 (7.0) | 단일 Agent, 개인 개발자용 |
| `small_team` | 4 | 전문가 리뷰 (8.0) + QA 점수 (8.5) | 키워드 라우팅을 통한 협업 |
| `enterprise` | 6 | 전문가 (8.5) + QA (9.0) + 보안 (9.0) | 프로덕션 등급, 스킬 기반 라우팅 |

### 서비스 템플릿

| 템플릿 | 카테고리 | 호출당 가격 | 무료 티어 | 설명 |
|--------|----------|------------|-----------|------|
| `ai_api` | AI | $0.05 | 100회 | ML 추론 API |
| `data_pipeline` | Data | $0.10 | 50회 | 데이터 처리 및 ETL |
| `content_api` | Content | $0.02 | 200회 | 텍스트 생성 |

---

## 기여하기

1. 저장소를 포크하세요
2. 기능 브랜치를 생성하세요 (`git checkout -b feat/my-feature`)
3. 테스트를 먼저 작성하세요 (TDD 권장)
4. 모든 테스트가 통과하는지 확인하세요 (`python -m pytest tests/ -v`)
5. 명확한 설명과 함께 Pull Request를 제출하세요

### 코드 표준

- Python 3.11+
- 불변 데이터 모델 (frozen dataclasses)
- 모든 경계에서 포괄적인 입력 검증
- 모든 오류는 일관된 `{"detail": "..."}` 형식으로 반환
- 하드코딩된 시크릿 금지 -- 환경 변수 사용

---

## 라이선스

MIT

---

Built by [JudyAI Lab](https://judyailab.com) with Agent Commerce Framework.
