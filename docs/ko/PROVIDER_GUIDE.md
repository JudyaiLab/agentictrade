---
title: "AgenticTrade 제공자 온보딩 가이드"
description: "AgenticTrade에서 AI API를 등록, 구성, 수익화하는 완전한 개발자 가이드 — 가입부터 첫 자동 결제까지."
date: 2026-03-21
tags: ["agentictrade", "api", "developers", "onboarding", "provider"]
categories: ["Developer Guide"]
---

# AgenticTrade 제공자 온보딩 가이드

AgenticTrade에 오신 것을 환영합니다. 이 가이드는 계정 생성부터 AI Agent로부터 첫 자동 마이크로 페이먼트를 수령하기까지의 전체 제공자 여정을 안내합니다. 예상 소요 시간: **20~30분**. 블록체인 경험은 필요하지 않습니다.

## 목표

AgenticTrade는 AI Agent가 API를 자동으로 검색, 인증, 결제하는 마켓플레이스입니다. 제공자로서 API를 한 번 등록하면, 호환되는 모든 Agent가 이를 찾아 호출하고 결제할 수 있습니다 — 인보이스를 보내거나 결제를 추심할 필요가 없습니다.

플랫폼은 API와 Agent 사이에 위치합니다. 검색 (MCP Tool Descriptor), 과금 (USDC/USDT 또는 법정화폐 마이크로 정산), 인증 (프록시 키), 평판 (거래 이력 + 평점)을 처리합니다.

## 1단계: 제공자 계정 생성

### 옵션 A: 웹 포털 (권장)

가장 빠른 시작 방법입니다. [agentictrade.io/portal/register](https://agentictrade.io/portal/register)를 방문하여 등록 양식을 작성하세요:

1. 이메일, 표시 이름, 비밀번호를 입력합니다
2. 인증 링크를 통해 이메일을 확인합니다
3. **제공자 대시보드**로 자동 리디렉션됩니다

포털은 등록 시 **Vendor API Key**를 자동으로 생성합니다. 대시보드의 **설정 → API 토큰**에서 확인하세요.

### 옵션 B: API 우선 (고급)

프로그래밍 방식의 온보딩을 선호하는 경우, [agentictrade.io](https://agentictrade.io)에서 API를 통해 가입하세요. 필요한 것:

- 유효한 이메일 주소
- 기본 비즈니스 정보 (이름, 웹사이트, 카테고리)
- **선택 사항:** 암호화폐 지갑 (Coinbase Wallet, MetaMask 또는 모든 EVM 호환 지갑) — USDC 정산을 원하는 경우에만 필요. 필요 없다면 PayPal을 통한 법정화폐 정산을 이용할 수 있습니다.

등록이 완료되면 대시보드에서 **Vendor API Key**를 받게 됩니다. 이 키는 모든 제공자 측 API 호출을 인증합니다. 비밀로 유지하세요 — 프록시 키 생성, 디스크립터 게시, 서비스 리스팅 관리 권한이 있습니다.

```
# Vendor API Key 형식:
atx_vendor_7f8a9b2c3d4e5f6...
```

환경 변수로 저장하세요:

```bash
export AGENTICTRADE_VENDOR_KEY="atx_vendor_your_key_here"
```

### 암호화폐가 처음이신가요? 걱정 마세요

- **USDC는 디지털 달러입니다.** 1 USDC = 1 USD, 항상 동일합니다. 변동성이 큰 암호화폐가 아닌, 미국 달러에 고정된 스테이블코인입니다.
- **암호화폐를 구매할 필요가 없습니다.** 플랫폼은 PayPal을 통한 법정화폐 정산을 완전히 지원합니다. 암호화폐는 완전히 선택 사항입니다.
- **USDC를 수령하고 싶다면:** [Coinbase Wallet](https://www.coinbase.com/wallet)을 추천합니다 — 무료이며, 약 2분이면 설정 가능하고, AgenticTrade와 바로 호환됩니다.
- **Base L2**는 이더리움의 빠르고 저렴한 레이어입니다. 모든 온체인 정산은 Base에서 이루어지므로, 거래 수수료가 달러가 아닌 센트의 일부에 불과합니다.

## 2단계: API 서비스 등록

먼저 플랫폼에 판매할 서비스를 알려야 합니다. 서비스 레지스트리 엔드포인트를 호출하여 API를 등록합니다:

```bash
curl -X POST https://api.agentictrade.io/v1/services \
  -H "Authorization: Bearer $AGENTICTRADE_VENDOR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Your API Name",
    "slug": "your-api-slug",
    "description": "API의 기능과 대상 사용자 설명.",
    "base_url": "https://api.yourdomain.com/v1",
    "protocol": "rest",
    "auth_type": "bearer",
    "category": ["data", "ml", "utility"],
    "pricing_model": "per_call",
    "price_usd": 0.001,
    "mcp_enabled": true,
    "tags": ["relevant", "keywords"]
  }'
```

응답에서 `service_id`와 수수료 등급 확인을 받게 됩니다. 신규 제공자는 자동으로 **1개월차 0% 수수료**가 적용됩니다 — 조건 없이 수익의 100%를 가져갑니다.

### 주요 등록 필드

| 필드 | 설명 |
|------|------|
| `slug` | URL 및 API 호출에 사용되는 고유 식별자. 한 번 설정하면 변경 불가. |
| `base_url` | 실제 API 엔드포인트. AgenticTrade가 이 URL을 통해 호출을 프록시합니다. |
| `pricing_model` | `per_call`, `per_token`, `subscription` 또는 `tiered`. 대부분의 제공자는 `per_call`로 시작합니다. |
| `price_usd` | USD 기준 호출당 가격. MCP 디스크립터에서 도구별 가격을 더 세밀하게 설정할 수 있습니다. |
| `mcp_enabled` | `true`로 설정하면 MCP Tool Descriptor 게시가 활성화됩니다. |

## 3단계: 프록시 API 키 발급

Agent에게 원본 API 키를 절대 제공하지 마세요. 대신 AgenticTrade를 통해 **프록시 키**를 생성하세요. 프록시 키는 플랫폼의 과금 레이어를 통해 라우팅되어, 기존 인증 시스템을 건드리지 않고 사용량 측정, 속도 제한 적용, 호출 Agent 지갑 청구를 처리합니다.

```bash
curl -X POST https://api.agentictrade.io/v1/services/YOUR_SERVICE_ID/keys \
  -H "Authorization: Bearer $AGENTICTRADE_VENDOR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Production Agent Key",
    "scope": ["read", "execute"],
    "rate_limit": 1000,
    "rate_limit_window": "minute"
  }'
```

응답에 `proxy_key`가 포함됩니다. 이것을 Agent에게 제공하세요 — 필요한 유일한 자격 증명입니다. 다양한 Agent나 용도별로 여러 프록시 키를 발급하고, 다른 키에 영향 없이 개별적으로 폐기할 수 있습니다.

### 프록시 키 구조

```
atx_pk_prod_K8mNpQrStUvWxYz1234567890
 ^^^^ ^^ ^^^^^^
 │    │   └─ 고유 키 식별자
 │    └─ 환경 (prod/staging)
 └─ 접두사 (AgenticTrade 프록시 키임을 표시)
```

## 4단계: MCP Tool Descriptor 게시

MCP Tool Descriptor는 API를 **AI Agent가 검색 가능하게** 만드는 요소입니다. API가 제공하는 모든 도구 — 이름, 파라미터, 반환 타입, 호출당 비용을 설명하는 JSON 스키마입니다. 게시하면 AgenticTrade MCP 레지스트리에 등록됩니다.

MCP 호환 프레임워크 (LangChain, CrewAI, AutoGPT 등)로 구축된 Agent는 도구를 자동으로 검색하고, 스키마를 파싱하여, 수동 통합 없이 호출을 시작할 수 있습니다.

```bash
curl -X PUT https://api.agentictrade.io/v1/mcp/YOUR_SERVICE_ID/descriptor \
  -H "Authorization: Bearer $AGENTICTRADE_VENDOR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "schema_version": "1.0",
    "name": "your_api_slug",
    "description": "API가 Agent에게 제공하는 기능 설명.",
    "category": "data",
    "tools": [
      {
        "name": "tool_name",
        "description": "이 특정 도구의 기능 설명.",
        "input_schema": {
          "type": "object",
          "properties": {
            "param_name": {
              "type": "string",
              "description": "이 파라미터가 제어하는 내용."
            }
          },
          "required": ["param_name"]
        },
        "pricing": {
          "cost_usd": 0.001,
          "unit": "per_call"
        }
      }
    ],
    "auth": {
      "type": "bearer",
      "proxy_key_hint": "AgenticTrade 프록시 키를 Bearer 토큰으로 사용하세요."
    },
    "rate_limits": {
      "requests_per_minute": 1000
    }
  }'
```

### Descriptor 작성 모범 사례

- **설명을 구체적으로 작성하세요.** Agent는 도구 설명을 기반으로 API 호출 여부를 결정합니다. 모호한 설명은 품질 게이트 검색에서 필터링됩니다.
- **도구별 가격을 설정하세요.** API에 컴퓨팅 비용이 다른 여러 엔드포인트가 있다면, 평균이 아닌 각 도구의 개별 가격을 설정하세요.
- **실제 예시를 사용하세요.** 가능한 경우 파라미터에 `examples` 배열을 추가하세요 — Agent는 구체적인 입출력 쌍에서 더 잘 학습합니다.
- **점진적으로 게시하세요.** 가장 유용한 2~3개 도구부터 시작하세요. 서비스 등록을 변경하지 않고도 나중에 추가할 수 있습니다.

## 5단계: 전체 호출 플로우 테스트

라이브 전에 엔드투엔드 전체 플로우를 검증하세요. AgenticTrade 대시보드를 사용하여 Agent 호출을 시뮬레이션합니다:

```bash
# 1. Agent가 디스크립터를 가져옴
curl https://api.agentictrade.io/v1/mcp/your-api-slug/descriptor.json

# 2. Agent가 프록시를 통해 호출 (vendor key가 아닌 agent wallet key 사용)
curl -X POST https://api.agentictrade.io/v1/call \
  -H "Authorization: Bearer AGENT_WALLET_KEY" \
  -H "X-Service: your-api-slug" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "tool_name",
    "params": { "param_name": "test_value" }
  }'
```

API는 `base_url`에서 표준 호출을 수신합니다. 프록시 레이어가 투명하게 측정 및 과금을 추가합니다. 제공자 대시보드를 확인하세요 — 15분 이내에 비용과 함께 호출 로그가 표시될 것입니다.

## 6단계: 결제 수령 방법 이해

AgenticTrade는 **T+1 롤링 기준**으로 수익을 정산합니다 — 어제의 호출 수익이 오늘 지갑에 입금됩니다. 정산은 자동으로 실행됩니다; 인보이스 생성이 필요 없습니다.

| 단계 | 기간 | 수수료 | 수령액 |
|------|------|--------|--------|
| 런칭 월 | 1개월차 | **0%** | 100% |
| 성장 단계 | 2~3개월차 | **5%** | 95% |
| Standard | 4개월차+ | **10%** | 90% |

USDC, USDT 또는 법정화폐 (PayPal 통합)로 정산을 수령할 수 있습니다. 최소 지급 기준은 $10 상당입니다. 대시보드에서 Agent별 호출 내역, 수익 트렌드, 평판 점수를 확인할 수 있습니다.

## 7단계: 모니터링 및 최적화

라이브 이후 제공자 대시보드가 커맨드 센터입니다:

- **호출 로그**: 모든 Agent 호출, 타임스탬프, 비용, Agent 지갑 ID
- **수익 대시보드**: 일간/주간/월간 수익 및 트렌드 차트
- **평판 점수**: 지연시간, 안정성, 응답 품질 지표에서 자동 산출
- **속도 제한 모니터링**: 어떤 Agent가 제한에 도달하는지 확인하고 임계값 조정

높은 평판 점수는 검색 순위를 향상시킵니다. 낮은 지연시간과 높은 안정성을 유지하여 품질 게이트 Agent 프레임워크에서의 검색 가능성을 극대화하세요.

## 수수료 비교

재정적으로 왜 이것이 중요한지 살펴보세요. 일반적인 AI API 마진 기준:

| 월 매출 | RapidAPI (25%) | AgenticTrade (10%) | 연간 절감액 |
|---------|---------------|--------------------|------------|
| $5,000 | $15,000/년 | $6,000/년 | **$9,000** |
| $20,000 | $60,000/년 | $24,000/년 | **$36,000** |
| $50,000 | $150,000/년 | $60,000/년 | **$90,000** |

첫 3개월은 더 유리합니다: 0% 이후 5% 수수료. 등록하지 않을 이유가 없습니다.

## 분쟁 해결 (Dispute Resolution)

AgenticTrade는 구매자와 제공자 모두를 보호하기 위해 에스크로 시스템을 사용합니다. 구매자가 API를 호출하면, 결제금이 에스크로에 보관된 후 제공자에게 지급됩니다. 구매자가 거래에 이의를 제기하면 다음과 같은 절차가 진행됩니다.

### 에스크로 보관 기간

보관 기간은 거래 금액에 따라 달라집니다 — 소액 결제는 더 빠르게 처리됩니다:

| 거래 금액 | 보관 기간 | 분쟁 제기 기한 |
|-----------|-----------|---------------|
| $1 미만 | 1일 | 24시간 |
| $1 – $100 | 3일 | 72시간 (3일) |
| $100 초과 | 7일 | 7일 |

보관 기간 동안 구매자는 분쟁을 제기할 수 있습니다. 분쟁이 제기되지 않으면, 보관 기간이 끝날 때 결제금이 자동으로 제공자에게 지급됩니다.

### 구매자가 분쟁을 제기하면

1. **구매자가 분쟁 제기** — 구매자가 사유를 작성하고, 카테고리(`service_not_delivered`, `quality_issue`, `unauthorized_charge`, `wrong_output`, `timeout_or_error`, `other`)를 선택하며, 증거 URL을 첨부할 수 있습니다.
2. **알림 수신** — 대시보드를 확인하거나 `escrow.dispute_opened` 웹훅 이벤트를 수신합니다.
3. **반증 제출** — 분쟁 기한이 종료되기 전에 대응해야 합니다.
4. **관리자 검토 및 해결** — 양측이 해결하지 못하면, 관리자가 구속력 있는 결정을 내립니다.
5. **분쟁 기한이 관리자 조치 없이 만료되면**, 보관금은 제공자에게 자동 지급됩니다.

### 반증 제출 방법

거래에 대한 분쟁이 제기되면, API를 통해 귀하의 입장을 제출하세요:

```bash
curl -X POST https://api.agentictrade.io/v1/escrow/holds/{hold_id}/dispute/respond \
  -H "Authorization: Bearer $AGENTICTRADE_VENDOR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "서비스가 정상적으로 제공되었습니다. 로그에서 해당 타임스탬프에 올바른 출력과 함께 200 응답이 확인됩니다.",
    "evidence_urls": [
      "https://your-logging-dashboard.com/logs/abc123",
      "https://screenshots.example.com/response-proof.png"
    ]
  }'
```

**효과적인 반증을 위한 팁:**
- 요청이 정상 처리되었음을 보여주는 서버 측 로그 포함
- 분쟁된 거래와 일치하는 타임스탬프 제공
- 가동 시간과 응답 품질을 확인하는 모니터링 대시보드 링크
- 증거 URL은 반드시 `https://` 사용 (최대 10개 URL, 각 최대 2048자)

### 해결 결과

모든 분쟁은 세 가지 결과 중 하나로 종료됩니다. 각 결과가 지급에 미치는 영향은 다음과 같습니다:

| 결과 | 내용 | 지급액 |
|------|------|--------|
| `release_to_provider` | 제공자 유리 판결 | **전액 지급** |
| `refund_buyer` | 구매자 유리 판결 | **$0** — 전액 구매자에게 환불 |
| `partial_refund` | 부분 판결 | **일부 금액** — `보관금 - 환불금`을 수령 |

`partial_refund`의 경우, 관리자가 정확한 환불 금액을 지정합니다. 나머지는 제공자에게 지급됩니다. 예를 들어, $10 보관금에 $3 부분 환불이면, 제공자는 $7을 수령합니다.

### 분쟁 모니터링

제공자 요약 엔드포인트를 통해 에스크로 상태를 추적하세요:

```bash
curl https://api.agentictrade.io/v1/escrow/providers/{your_provider_id}/summary \
  -H "Authorization: Bearer $AGENTICTRADE_VENDOR_KEY"
```

이 엔드포인트는 `total_held`, `total_released`, `total_refunded`, `pending_count`를 반환합니다. 대시보드에서 개별 보관 내역과 분쟁 증거도 확인할 수 있습니다.

### 분쟁 최소화

분쟁을 처리하는 가장 좋은 방법은 분쟁을 피하는 것입니다:
- 높은 가동 시간과 낮은 지연시간 유지 (평판 점수에 직접 영향)
- 요청 실패 시 명확한 오류 메시지 반환 — 오류를 이해하는 Agent는 분쟁을 제기할 가능성이 낮습니다
- MCP 디스크립터에 정확한 가격 설정 — 예상치 못한 요금은 흔한 분쟁 원인입니다
- 구매자가 발견하기 전에 호출 로그에서 이상 징후 모니터링

## 바로 시작하기

[agentictrade.io](https://agentictrade.io)에서 가입하세요 — 등록 무료, 첫 달 0% 수수료. MCP 디스크립터를 10분 이내에 라이브할 수 있습니다. 그런 다음 Agent가 찾아오도록 하세요.

---

*문의사항이 있으시면 [API 문서](https://docs.agentictrade.io)를 확인하거나 support@agentictrade.io로 이메일을 보내주세요.*
