# 如何在 AgenticTrade 上架你的 AI API — 5 分鐘快速指南

*發布日期：2026 年 3 月 | 分類：API 服務提供者*

---

AI Agent 正在花真金白銀消費 — 而大部分的錢都流向了 RapidAPI、Stripe，或是被各種自建計費系統吞掉。如果你有 AI、資料或工具類 API，現在有一個更快的方式接觸這群新興買家：**把它上架到 AgenticTrade**。

這份指南帶你從零開始，5 分鐘內完成上架。不需要信用卡、不需要建立計費基礎設施、也不需要為期六週的整合專案。

## 為什麼選擇 AgenticTrade？

在動手之前，先看看三個關鍵數字：

| 平台 | 抽成比例 | 第一個月 |
|----------|-----------|-------------|
| Apple App Store | 30% | 30% |
| RapidAPI | 25% | 25% |
| **AgenticTrade** | **10%** | **0%** |

**AgenticTrade 的抽成比 RapidAPI 低 60%** — 而且第一個月完全免費。在 Agent 自動發現並為你的服務付費的同時，你可以保留 100% 的收入。

平台為你處理：
- **服務發現** — MCP 原生服務註冊表，Agent 和人類都能搜尋
- **支付** — 多軌道支援：USDC（x402）、300+ 種加密貨幣（NOWPayments）、法幣（PayPal）
- **計量** — 按次呼叫追蹤，搭配即時使用分析
- **信譽** — 自動品質評分（延遲、正常運行時間、可靠性），隨時間累積

你只管寫 API，其他的我們搞定。

---

## 前置需求

- 一個你想變現的 API（AI 模型端點、資料串流、工具服務等）
- 一個 AgenticTrade 帳號（免費註冊：[agentictrade.io](https://agentictrade.io)）
- curl，或任何 HTTP 用戶端

就是這些。

---

## 步驟一：註冊並取得 API Key

在 [agentictrade.io](https://agentictrade.io) 註冊帳號，然後前往 **Dashboard → API Keys → New Key**。

複製你的 `acf_live_xxx` 金鑰，格式如下：

```
acf_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

## 步驟二：註冊你的服務

只需要一個 `POST` 請求就能註冊你的 API。以下是一個實際範例 — 假設你要以每次呼叫 $0.01 的價格銷售一個加密貨幣情緒分析 API：

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

**回應：**

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

你的服務已經**上線**了。平台自動分配了一個唯一 ID（`svc_xxx`），並將第一個月的抽成比例設為 0%。

---

## 步驟三：建立 Proxy 專用的第二組 API Key

接下來才是真正強大的地方。AgenticTrade 不會讓買家直接存取你的原始 API 端點，而是透過平台的基礎設施代理請求。這代表：

- 買家看不到你的真實 URL（安全性 + 頻率限制）
- 在呼叫你的端點之前，支付會自動完成
- 使用量透明計量

建立一組專門用於 Proxy 存取的金鑰：

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

**回應：**

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

這組 Proxy 金鑰就是你提供給買家的。他們用這組金鑰透過 AgenticTrade 的 Proxy 端點呼叫你的服務。

---

## 步驟四：（選用）建立 MCP Tool Descriptor

如果你希望 AI Agent 能原生發現你的服務（透過 MCP — Model Context Protocol），可以註冊一個 Tool Descriptor：

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

現在任何使用 Claude、GPT 或任何 MCP 相容框架建構的 AI Agent，都可以原生發現並呼叫你的工具。

---

## 買家如何呼叫你的服務

從買家的角度來看，透過 AgenticTrade 呼叫你的服務看起來是這樣的：

```bash
# Buyer calls your service via the AgenticTrade proxy
curl -X POST https://agentictrade.io/api/v1/proxy/svc_4a8f2c1d9e3b/sentiment \
  -H "Authorization: Bearer acp_buyer_proxy_key" \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTC", "timeframe": "24h"}'

# Response from your API is returned as-is:
# {
#   "symbol": "BTC",
#   "sentiment_score": 42,
#   "confidence": 0.87,
#   "sources_analyzed": 18420
# }
```

平台會自動處理支付標頭（`X-ACF-Amount`、`X-ACF-Free-Tier` 等）。你的端點只需要回傳資料就好。

---

## 你的收入儀表板

即時追蹤收入：

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

結算以 USDC 透過 Base 網路自動撥付，無需手動開立發票。

---

## 抽成時程表：你實際需要支付多少

**Provider Growth Program** 的設計讓你在起步階段負擔更少：

| 期間 | 抽成比例 | 你保留 |
|--------|-----------|----------|
| 第 1 個月 | **0%** | 100% |
| 第 2–3 個月 | **5%** | 95% |
| 第 4 個月起 | **10%** | 90% |

對比 RapidAPI：從第一天起就收 25%，而且完全沒有階梯優惠。

以每月 $1,000 的 API 收入為例：
- **RapidAPI**：你保留 $750
- **AgenticTrade（第 4 個月起）**：你保留 $900
- **你省下的錢**：每月 $150 = 每年 $1,800

---

## 平台包含哪些功能

以下是你用那 10%（或 5%、或 0%）換來的全部內容：

| 功能 | 已包含 |
|---------|----------|
| 服務上架 + 發現 | ✅ |
| MCP Tool 註冊表 | ✅ |
| 多軌道支付（加密貨幣 + 法幣） | ✅ |
| 按次呼叫計量 + 分析 | ✅ |
| 信譽引擎（延遲、正常運行時間、可靠性評分） | ✅ |
| 自動 USDC 結算 | ✅ |
| 頻率限制 + SSRF 防護 | ✅ |
| Webhook 通知（payment.completed、service.called） | ✅ |
| 團隊管理 + 路由規則 | ✅ |
| 24/7 Proxy 基礎設施 | ✅ |

這些你都不需要自己建。全部內建在平台裡。

---

## 常見問題

**Q：我需要自己整合 x402 協定嗎？**
不需要。AgenticTrade 在伺服器端處理支付協定協商。你只需要回傳 API 回應，平台會自動注入計費標頭。

**Q：如果我的 API 運行成本很高怎麼辦？**
定價由你全權掌控。將 `price_per_call` 設定為足以覆蓋你的基礎設施成本再加上利潤的數字即可。平台只抽取一定比例，不會限制你的營收上限。

**Q：我可以隨時下架我的服務嗎？**
可以。一個 API 呼叫就能停用。沒有鎖定期，也沒有違約金。

**Q：結算的時程是怎樣的？**
每週自動以 USDC 撥付。企業級服務提供者可以申請自訂撥付排程。

---

## 下一步

1. **註冊帳號** — 在 [agentictrade.io](https://agentictrade.io) 免費註冊
2. **上架你的第一個服務** — 只需要 2 分鐘
3. **分享你的 Marketplace URL** 給 AI Agent 開發者
4. **在儀表板上看著收入持續增長**

第一個月完全免費，沒有任何理由不上架。

---

*AgenticTrade 由 JudyAI Lab 打造。平台已上線：[agentictrade.io](https://agentictrade.io)。*
