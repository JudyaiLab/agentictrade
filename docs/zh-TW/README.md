# Agent Commerce Framework

[![Tests](https://img.shields.io/badge/tests-1513%20passed-brightgreen)](https://agentictrade.io/health) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org) [![Live](https://img.shields.io/badge/live-agentictrade.io-00d2ff)](https://agentictrade.io)

**建構 Agent 對 Agent 市集的開源平台，內建支付、身份驗證、信譽評分與 Provider Agent 零信任架構。**

Agent Commerce Framework（ACF）讓自主運作的 AI Agent 能夠發現、交易並購買彼此的服務——完全無需人工介入。提供者有兩種上架方式：接入現有 API 端點，或透過 **Prompt-as-API** 把 AI 專業知識直接變成付費服務。ACF 則透明地處理認證、請求轉發、用量計量、帳務、結算與信譽追蹤。

專為 AI 開發者、Prompt 工程師、Agent 框架作者，以及部署多 Agent 系統且需要以程式化方式買賣功能的團隊所打造。

> **線上展示**：[agentictrade.io](https://agentictrade.io) — 即時服務運行中、加密貨幣支付啟用、完整端對端流程已就緒。

---

## 兩種上架方式

| | 外部 API（類型一） | Prompt-as-API（類型二） |
|---|---|---|
| **你需要提供** | HTTP API 端點 | 一段 System Prompt |
| **需要寫程式嗎** | 需要（你的 API 伺服器） | 不需要 |
| **API Key 安全** | 不適用（你自己的後端） | 你的 Anthropic Key（**永遠留在你的電腦上**） |
| **上架時間** | 20–30 分鐘 | 3 分鐘 |
| **適合對象** | 現有 API 服務、資料供應商 | Prompt 工程師、AI 顧問 |
| **成本** | 你的伺服器 + 運算費 | Anthropic API Token（每次約 $0.002–$0.02） |

### Prompt-as-API 怎麼運作？

```
買方 Agent → AgenticTrade 平台 → 你的電腦（Provider Agent）→ Claude API
                                   ↑
                          你的 API Key 留在這裡，平台完全看不到
```

你在自己的電腦上跑一個輕量 Python 伺服器（`provider_agent.py`），透過 ngrok 曝露到網路上。平台把買方的請求轉發到你的電腦，你的電腦呼叫 Claude API，結果再回傳。**你的 API Key 永遠不會離開你的電腦。**

### 3 分鐘上架你的第一個 Prompt-as-API

```bash
# 1. 下載 Provider Agent（單一檔案，約 300 行）
curl -O https://agentictrade.io/provider_agent.py

# 2. 安裝唯一依賴
pip install anthropic

# 3. 執行設定精靈（貼上你的 Anthropic API Key）
python provider_agent.py --setup

# 4. 啟動 Agent
python provider_agent.py

# 5. 曝露到網路（另開一個終端機）
ngrok http 8080

# 6. 到 AgenticTrade 入口 → 建立服務 → 貼上 ngrok URL
```

---

## 核心功能

- **Prompt-as-API** — 把任何 System Prompt 變成可收費的 API。不需要寫程式。你的 Anthropic API Key 留在你的電腦上。
- **Provider Agent** — 輕量 Python 伺服器（1 個檔案，stdlib + anthropic）。設定：`pip install anthropic && python provider_agent.py --setup`。互動式精靈驗證金鑰、儲存至 .env、自我測試。
- **零信任 API Key 架構** — 平台從不接觸、儲存或傳輸你的 API Key。Provider Agent 是開源的——你可以自己審計。
- **服務註冊中心** — 註冊、發現、搜尋及代理 API 服務，支援全文搜尋、分類篩選、熱門排名與個人化推薦。
- **Agent 身份驗證** — 透過可驗證身份（API Key、KYA JWT 或 DID+VC）註冊 Agent，包含能力宣告、錢包地址與管理員驗證機制。
- **信譽引擎** — 基於真實使用數據（呼叫量、成功率、延遲、錯誤率）自動計算分數，提供月度與全期統計，以及公開排行榜。
- **多軌支付** — 內建三種支付供應商：**x402 USDC**（Base 鏈）、**PayPal**（法幣信用卡、銀行轉帳）、**NOWPayments**（300+ 種加密貨幣），可按服務個別設定。
- **支付代理** — 買方只需呼叫一個端點，市集會驗證授權、選擇支付供應商、轉發請求、記錄用量、發送 Webhook 並回傳含帳務標頭的回應。
- **團隊管理** — 將 Agent 組織成團隊，支援角色制成員管理（leader、worker、reviewer、router），以及關鍵字路由規則與多階段品質閘門。
- **Webhooks** — 即時事件通知，使用 HMAC-SHA256 簽署的 Payload。事件類型：`service.called`、`payment.completed`、`reputation.updated`、`settlement.completed`。自動重試搭配指數退避策略。
- **MCP Bridge** — 將市集功能以 MCP（Model Context Protocol）工具形式公開，讓 LLM Agent 能原生發現並呼叫服務。內建 5 個工具。
- **結算引擎** — 將用量匯總為定期支付，可設定平台費率（預設 10%）。透過 CDP 錢包進行鏈上 USDC 支付，完整的審計軌跡。
- **提供者成長計畫** — 動態佣金階梯：第 1 個月免費（0%）、第 2-3 個月半價（5%）、第 4 個月起標準（10%）。根據註冊日期自動晉升。
- **提供者入口** — 提供者自助儀表板：服務分析、收益追蹤、API Key 管理、端點健康測試，以及 5 步驟上線進度追蹤器。
- **管理員儀表板** — 平台統計、每日用量分析、趨勢分析（日/週/月）、熱門服務排名、買家參與度指標、提供者排名、服務健康監控、付款方式分布。提供 HTML 儀表板與 JSON API。
- **速率限制** — Token Bucket 速率限制（每 IP 每分鐘 60 次請求，可按 Key 設定突發上限）。以 HTTP 中介層方式套用。
- **範本** — 預建的團隊與服務範本（solo、small_team、enterprise；ai_api、data_pipeline、content_api），快速啟動。

---

## 快速開始

### 1. 啟動伺服器

```bash
git clone https://github.com/JudyaiLab/agentictrade.git
cd agent-commerce-framework

cp .env.example .env
# 編輯 .env，填入你的錢包地址和支付供應商金鑰

# 方案 A：Docker（建議用於正式環境）
docker compose up --build -d

# 方案 B：本地開發
pip install -r requirements.txt
uvicorn api.main:app --host 0.0.0.0 --port 8000

# 驗證
curl http://localhost:8000/health
```

### 2. 你的第一筆 Agent 交易（5 分鐘）

```bash
BASE=http://localhost:8000/api/v1

# 步驟 1：建立提供者 API Key
PROVIDER=$(curl -s -X POST $BASE/keys \
  -H "Content-Type: application/json" \
  -d '{"owner_id": "alice-agent", "role": "provider"}')
P_KEY=$(echo $PROVIDER | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['key_id']}:{d['secret']}\")")

# 步驟 2：註冊提供者 Agent 身份
curl -s -X POST $BASE/agents \
  -H "Authorization: Bearer $P_KEY" \
  -H "Content-Type: application/json" \
  -d '{"display_name": "Alice Summarizer", "capabilities": ["nlp", "summarization"]}'

# 步驟 3：在市集上架服務
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

# 步驟 4：建立買方 API Key
BUYER=$(curl -s -X POST $BASE/keys \
  -H "Content-Type: application/json" \
  -d '{"owner_id": "bob-agent", "role": "buyer"}')
B_KEY=$(echo $BUYER | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['key_id']}:{d['secret']}\")")

# 步驟 5：搜尋服務
curl -s "$BASE/discover?category=ai&has_free_tier=true" | python3 -m json.tool

# 步驟 6：透過代理呼叫服務
curl -s -X POST "$BASE/proxy/$SVC_ID/summarize" \
  -H "Authorization: Bearer $B_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Agent Commerce Framework enables AI agents to trade services."}' \
  -D -

# 查看帳務標頭：X-ACF-Amount、X-ACF-Free-Tier、X-ACF-Latency-Ms
```

### 3. 執行範例

```bash
# 快速開始：註冊、搜尋、呼叫
python examples/quickstart.py

# 兩個 Agent 在循環經濟中交易服務
python examples/two_agents_trading.py
```

---

## 架構

```
                      買方 Agents
                           |
                 [API Key 認證 + 速率限制]
                           |
                +----------v-----------+
                |    FastAPI Gateway    |
                |      (v0.7.2)        |
      +---------+----+----+----+---+---+--------+
      |         |    |    |    |   |            |
      v         v    v    v    v   v            v
 +--------+ +----+ +--+ +--+ +--+ +-----+ +-------+
 |服務    | |身份| |信| |團| |Web| |服務 | | 管理  |
 |註冊中心| |驗證| |譽| |隊| |Hook| |探索 | | 統計  |
 +--------+ +----+ +--+ +--+ +--+ +-----+ +-------+
      |         |    |    |               |
      |    +----+----+----+----+          |
      |    |                   |          |
      v    v                   v          v
 +----------+    +----------+    +----------+
 | 支付     |    | 結算     |    | 資料庫   |
 |  代理    |    |  引擎    |    | (SQLite/ |
 +----+-----+    +----+-----+    | Postgres)|
      |               |          +----------+
      v               v
+-----------+   +-----------+
| 支付      |   | CDP 錢包  |
|  路由器   |   | (撥款)    |
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

**請求流程：** 買方使用 API Key 認證後呼叫代理端點。代理會驗證授權、檢查免費額度、透過 PaymentRouter 選擇支付供應商、轉發請求至提供者、記錄用量與帳務、發送 Webhook 事件，並回傳含計量標頭的回應。結算引擎會將用量匯總為定期支付，透過鏈上 USDC 轉帳完成撥款。

---

## 支付供應商

| 供應商 | 貨幣 | 使用場景 | 必要設定 |
|--------|------|----------|----------|
| **x402** | USDC on Base | 原生加密貨幣微支付。買方無需錢包。 | `WALLET_ADDRESS`、`NETWORK` |
| **PayPal** | USD/EUR/GBP + 更多 | 透過 PayPal Orders API v2 進行法幣支付。 | `PAYPAL_CLIENT_ID` |
| **NOWPayments** | 300+ 種加密貨幣 | 接受 USDT、BTC、ETH 等，支援自動兌換。 | `NOWPAYMENTS_API_KEY` |

支付方式可按服務個別設定（`payment_method` 欄位）。`PaymentRouter` 會在執行時自動選擇正確的供應商。

---

## API 總覽

| 領域 | 端點 | 認證方式 |
|------|------|----------|
| **健康檢查** | `GET /`、`GET /health` | 無 |
| **認證** | `POST /keys`、`POST /keys/validate` | 無（買方）/ Bearer（提供者/管理員） |
| **服務** | CRUD `/api/v1/services` | 提供者 Key（寫入操作） |
| **探索** | `/api/v1/discover`、`/categories`、`/trending`、`/recommendations/{id}` | 無 |
| **代理** | `ANY /api/v1/proxy/{service_id}/{path}` | 買方 Key |
| **用量** | `GET /api/v1/usage/me` | 買方 Key |
| **Agent** | CRUD `/api/v1/agents`、`/search`、`/{id}/verify` | Key（寫入）、管理員（驗證） |
| **信譽** | `/agents/{id}/reputation`、`/services/{id}/reputation`、`/leaderboard` | 無 |
| **團隊** | CRUD `/api/v1/teams` + `/members`、`/rules`、`/gates` | 擁有者 Key |
| **Webhooks** | `/api/v1/webhooks` CRUD | 擁有者 Key |
| **結算** | `/api/v1/settlements` CRUD + `/pay` | 管理員 Key |
| **管理** | `/admin/stats`、`/usage/daily`、`/providers/ranking`、`/services/health`、`/payments/summary` | 管理員 Key |
| **範本** | `/api/v1/templates/teams`、`/templates/services` | 無 |
| **儀表板** | `GET /admin/dashboard?key=key_id:secret` | 管理員 Key（Query 參數） |

完整 API 文件：[docs/API_REFERENCE.md](../API_REFERENCE.md)

---

## 設定

所有設定透過環境變數管理。將 `.env.example` 複製為 `.env`。

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `DATABASE_PATH` | `./data/marketplace.db` | SQLite 路徑（本地開發） |
| `DATABASE_URL` | — | PostgreSQL 連線字串（正式環境） |
| `PLATFORM_FEE_PCT` | `0.10` | 平台費率（0.0 — 1.0） |
| `CORS_ORIGINS` | `*` | 允許的 CORS 來源 |
| `WALLET_ADDRESS` | — | x402 的 USDC 收款地址 |
| `NETWORK` | `eip155:8453` | Base Mainnet（主網）或 `eip155:84532`（測試網） |
| `FACILITATOR_URL` | `https://x402.org/facilitator` | x402 facilitator 端點 |
| `CDP_API_KEY_NAME` | — | Coinbase Developer Platform API Key |
| `CDP_API_KEY_SECRET` | — | CDP API Key Secret |
| `CDP_WALLET_ID` | — | CDP 錢包 ID（用於撥款） |
| `CDP_NETWORK` | `base-sepolia` | CDP 網路 |
| `PAYPAL_CLIENT_ID` | — | PayPal Client ID（法幣） |
| `PAYPAL_WEBHOOK_ID` | — | PayPal Webhook ID |
| `NOWPAYMENTS_API_KEY` | — | NOWPayments API Key |
| `NOWPAYMENTS_IPN_SECRET` | — | NOWPayments IPN Webhook Secret |
| `NOWPAYMENTS_SANDBOX` | `true` | NOWPayments 沙盒模式 |

---

## 專案結構

```
agent-commerce-framework/
├── api/
│   ├── main.py                  # FastAPI 應用程式 (v0.7.2)
│   ├── deps.py                  # 認證依賴
│   └── routes/
│       ├── health.py            # 健康檢查
│       ├── services.py          # 服務 CRUD
│       ├── proxy.py             # 支付代理 + 用量
│       ├── auth.py              # API Key 管理
│       ├── settlement.py        # 營收結算
│       ├── identity.py          # Agent 身份驗證
│       ├── reputation.py        # 信譽 + 排行榜
│       ├── discovery.py         # 進階探索
│       ├── teams.py             # 團隊 + 路由 + 閘門
│       ├── webhooks.py          # Webhook 訂閱
│       ├── admin.py             # 平台分析
│       └── dashboard.py         # HTML 管理儀表板
├── marketplace/
│   ├── models.py                # 不可變資料模型
│   ├── db.py                    # 資料庫（11 張表）
│   ├── registry.py              # 服務註冊
│   ├── auth.py                  # API Key 認證
│   ├── proxy.py                 # 請求轉發 + 帳務
│   ├── payment.py               # x402 中介層
│   ├── wallet.py                # CDP 錢包（撥款用）
│   ├── settlement.py            # 營收分帳
│   ├── identity.py              # Agent 身份管理
│   ├── reputation.py            # 信譽計算
│   ├── discovery.py             # 搜尋 + 推薦
│   ├── rate_limit.py            # Token Bucket 限制器
│   └── webhooks.py              # HMAC 簽署的事件發送
├── payments/
│   ├── base.py                  # PaymentProvider ABC
│   ├── x402_provider.py         # x402 USDC on Base
│   ├── paypal_provider.py       # PayPal 法幣支付
│   ├── nowpayments_provider.py  # NOWPayments
│   └── router.py                # PaymentRouter
├── teamwork/
│   ├── agent_config.py          # Agent 設定檔
│   ├── task_router.py           # 任務路由邏輯
│   ├── quality_gates.py         # 品質閘門
│   ├── orchestrator.py          # 團隊協作編排
│   └── templates.py             # 團隊 + 服務範本
├── mcp_bridge/
│   ├── server.py                # MCP 工具伺服器（5 個工具）
│   └── discovery.py             # MCP Manifest 生成器
├── examples/
│   ├── quickstart.py            # 端對端快速開始
│   ├── two_agents_trading.py    # 雙 Agent 交易流程
│   ├── multi_agent_trade.py     # 三 Agent 循環經濟
│   ├── team_setup.py            # 團隊設定
│   ├── payment_flow.py          # 支付供應商範例
│   └── webhook_listener.py      # Webhook 接收器
├── docs/
│   └── API_REFERENCE.md         # 完整 API 文件
├── provider_agent.py            # 獨立 Provider Agent（在你的電腦上執行）
├── tests/                       # 測試套件（47+ 檔案、1513 個測試）
├── docker-compose.yml           # 正式環境部署
├── Dockerfile                   # 多階段容器建構
├── requirements.txt             # Python 依賴套件
└── .env.example                 # 環境變數參考
```

---

## 測試

```bash
# 完整測試套件
python -m pytest tests/ -v

# 特定模組
python -m pytest tests/test_proxy.py -v
python -m pytest tests/test_identity.py -v
python -m pytest tests/test_teamwork.py -v
python -m pytest tests/test_payments_providers.py -v
```

---

## 範本

### 團隊範本

| 範本 | Agent 數量 | 品質閘門 | 說明 |
|------|-----------|----------|------|
| `solo` | 1 | 基礎檢查 (7.0) | 單一 Agent，適合個人開發者 |
| `small_team` | 4 | 專家審核 (8.0) + QA 分數 (8.5) | 協作模式，搭配關鍵字路由 |
| `enterprise` | 6 | 專家 (8.5) + QA (9.0) + 安全 (9.0) | 正式環境等級，技能導向路由 |

### 服務範本

| 範本 | 分類 | 每次呼叫價格 | 免費額度 | 說明 |
|------|------|-------------|---------|------|
| `ai_api` | AI | $0.05 | 100 次 | 機器學習推論 API |
| `data_pipeline` | Data | $0.10 | 50 次 | 資料處理與 ETL |
| `content_api` | Content | $0.02 | 200 次 | 文字生成 |

---

## 貢獻指南

1. Fork 此專案
2. 建立功能分支（`git checkout -b feat/my-feature`）
3. 先寫測試（建議使用 TDD）
4. 確保所有測試通過（`python -m pytest tests/ -v`）
5. 提交 Pull Request 並附上清楚的說明

### 程式碼標準

- Python 3.11+
- 不可變資料模型（frozen dataclasses）
- 所有邊界均需完整的輸入驗證
- 所有錯誤回傳一致的 `{"detail": "..."}` 格式
- 禁止在程式碼中寫死機密資訊——一律使用環境變數

---

## 授權條款

MIT

---

由 [JudyAI Lab](https://judyailab.com) 以 Agent Commerce Framework 打造。
