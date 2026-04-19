# API 參考文件

Base URL: `http://localhost:8000`

所有需驗證的 endpoint 皆須附帶以下 header：

```
Authorization: Bearer {key_id}:{secret}
```

---

## 目錄

- [驗證](#驗證)
- [服務註冊](#服務註冊)
- [服務探索](#服務探索)
- [付款代理](#付款代理)
- [Agent 身份識別](#agent-身份識別)
- [信譽系統](#信譽系統)
- [結算](#結算)
- [團隊管理](#團隊管理)
- [Webhooks](#webhooks)
- [範本](#範本)
- [供應商入口](#供應商入口)
- [管理後台](#管理後台)
- [儀表板](#儀表板)
- [健康檢查](#健康檢查)
- [錯誤回應](#錯誤回應)
- [速率限制](#速率限制)

---

## 驗證

### 建立 API Key

```
POST /api/v1/keys
```

建立新的 API Key。**買方 key 無需驗證即可建立。** 供應商及管理員 key 則需要使用現有的 Bearer token 進行驗證。

**請求主體：**

| 欄位 | 類型 | 必填 | 預設值 | 說明 |
|-------|------|----------|---------|-------------|
| `owner_id` | string | 是 | -- | key 擁有者的唯一識別碼 |
| `role` | string | 否 | `"buyer"` | 角色：`buyer`、`provider`、`admin` |
| `rate_limit` | integer | 否 | `60` | 每分鐘最大請求數 |
| `wallet_address` | string | 否 | `null` | USDC 錢包地址 |

**範例：**

```bash
# 建立買方 key（無需驗證）
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{"owner_id": "my-agent", "role": "buyer"}'

# 建立供應商 key（需要驗證）
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {existing_key_id}:{existing_secret}" \
  -d '{"owner_id": "my-agent", "role": "provider"}'
```

**回應 (201)：**

```json
{
  "key_id": "acf_a1b2c3d4e5f6g7h8",
  "secret": "sec_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "role": "provider",
  "rate_limit": 60,
  "message": "Save the secret — it cannot be retrieved again."
}
```

> **重要：** `secret` 僅在建立時回傳一次，請妥善保存。

---

### 驗證 API Key

```
POST /api/v1/keys/validate
```

驗證 API Key 配對。若有效則回傳擁有者及角色資訊。

**請求主體：**

| 欄位 | 類型 | 必填 | 說明 |
|-------|------|----------|-------------|
| `key_id` | string | 是 | Key ID |
| `secret` | string | 是 | Key secret |

**回應 (200)：**

```json
{
  "valid": true,
  "owner_id": "my-agent",
  "role": "provider",
  "rate_limit": 60
}
```

**錯誤 (401)：** 憑證無效。

---

## 服務註冊

### 註冊服務

```
POST /api/v1/services
```

在市場中註冊新服務。**需要供應商或管理員 API Key。**

**請求主體：**

| 欄位 | 類型 | 必填 | 預設值 | 說明 |
|-------|------|----------|---------|-------------|
| `name` | string | 是 | -- | 服務名稱 |
| `description` | string | 否 | `""` | 服務說明 |
| `endpoint` | string | 是 | -- | 供應商的 API URL（須以 `https://` 或 `http://` 開頭） |
| `price_per_call` | string | 是 | -- | 每次呼叫價格（例如 `"0.05"`） |
| `category` | string | 否 | `""` | 分類（例如 `ai`、`data`、`content`） |
| `tags` | string[] | 否 | `[]` | 可搜尋的標籤 |
| `payment_method` | string | 否 | `"x402"` | `x402`、`paypal` 或 `nowpayments` |
| `free_tier_calls` | integer | 否 | `0` | 每位買方的免費呼叫次數 |

**範例：**

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

**回應 (201)：**

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

### 列出服務

```
GET /api/v1/services
```

列出並搜尋服務。**無需驗證。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `query` | string | -- | 在名稱與說明中進行文字搜尋 |
| `category` | string | -- | 依分類篩選 |
| `status` | string | `"active"` | 依狀態篩選（`active`、`paused`、`removed`） |
| `limit` | integer | `50` | 最大回傳數量（1--100） |
| `offset` | integer | `0` | 分頁偏移量 |

**回應 (200)：**

```json
{
  "services": [{ "id": "...", "name": "...", "pricing": {...}, ... }],
  "count": 10,
  "offset": 0,
  "limit": 50
}
```

---

### 取得服務

```
GET /api/v1/services/{service_id}
```

取得單一服務的詳細資訊。**無需驗證。**

**回應 (200)：** 服務物件（格式同註冊服務的回應）。

**錯誤 (404)：** 找不到服務。

---

### 更新服務

```
PATCH /api/v1/services/{service_id}
```

更新服務。**僅限擁有者；需要供應商 API Key。**

**請求主體（所有欄位皆為選填）：**

| 欄位 | 類型 | 說明 |
|-------|------|-------------|
| `name` | string | 新名稱 |
| `description` | string | 新說明 |
| `endpoint` | string | 新的 endpoint URL |
| `price_per_call` | string | 新價格 |
| `status` | string | `active`、`paused` 或 `removed` |
| `category` | string | 新分類 |
| `tags` | string[] | 新標籤 |

**回應 (200)：** 更新後的服務物件。

---

### 刪除服務

```
DELETE /api/v1/services/{service_id}
```

軟刪除服務（將狀態設為 `removed`）。**僅限擁有者；需要供應商 API Key。**

**回應 (200)：**

```json
{"status": "removed", "id": "550e8400-..."}
```

---

## 服務探索

### 搜尋服務（進階）

```
GET /api/v1/discover
```

進階服務探索，支援全文搜尋、篩選及排序。**無需驗證。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `q` | string | -- | 全文搜尋查詢 |
| `category` | string | -- | 依分類篩選 |
| `tags` | string | -- | 以逗號分隔的標籤篩選（例如 `nlp,sentiment`） |
| `min_price` | string | -- | 每次呼叫最低價格（例如 `"0.01"`） |
| `max_price` | string | -- | 每次呼叫最高價格（例如 `"1.00"`） |
| `payment_method` | string | -- | `x402`、`paypal` 或 `nowpayments` |
| `has_free_tier` | boolean | -- | 設為 `true` 以篩選提供免費呼叫的服務 |
| `sort_by` | string | `"created_at"` | 排序方式：`created_at`、`price` 或 `name` |
| `limit` | integer | `50` | 最大回傳數量（1--100） |
| `offset` | integer | `0` | 分頁偏移量 |

**範例：**

```bash
curl "http://localhost:8000/api/v1/discover?q=nlp&category=ai&has_free_tier=true&sort_by=price&limit=10"
```

**回應 (200)：**

```json
{
  "services": [{ "id": "...", "name": "...", "pricing": {...}, ... }],
  "total": 25,
  "offset": 0,
  "limit": 10
}
```

---

### 列出分類

```
GET /api/v1/discover/categories
```

取得所有服務分類及各分類的啟用服務數量。**無需驗證。**

**回應 (200)：**

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

### 熱門服務

```
GET /api/v1/discover/trending
```

取得依使用量排名的熱門服務。**無需驗證。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `limit` | integer | `10` | 最大回傳數量 |

**回應 (200)：**

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

### 個人化推薦

```
GET /api/v1/discover/recommendations/{agent_id}
```

根據 Agent 的使用歷史取得服務推薦。回傳該 Agent 最常使用分類中的服務，並排除已使用過的服務。**無需驗證。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `limit` | integer | `5` | 最大推薦數量 |

**回應 (200)：**

```json
{
  "recommendations": [{ "id": "...", "name": "...", ... }],
  "count": 5
}
```

---

## 付款代理

### 代理請求

```
ANY /api/v1/proxy/{service_id}/{path}
```

將請求轉送至服務供應商，自動處理付款流程。支援 `GET`、`POST`、`PUT`、`PATCH`、`DELETE`。**需要買方或供應商 API Key。**

市場會處理整個付款流程：

1. 驗證您的 API Key 並檢查速率限制
2. 查詢服務及定價
3. 檢查免費額度（依買方、依服務計算）
4. 透過設定的付款供應商（x402/PayPal/NOWPayments）建立付款
5. 將您的請求轉送至供應商的 endpoint
6. 記錄使用量與帳務
7. 發送 Webhook 事件（`service.called`）
8. 回傳供應商的回應，附帶帳務 header

**路徑參數：**

| 參數 | 說明 |
|-------|-------------|
| `service_id` | 要呼叫的服務 |
| `path` | 附加至供應商 endpoint 的路徑 |

**範例：**

```bash
curl -X POST http://localhost:8000/api/v1/proxy/{service_id}/analyze \
  -H "Authorization: Bearer {key_id}:{secret}" \
  -H "Content-Type: application/json" \
  -d '{"text": "Analyze this document"}'
```

**回應：** 供應商的原始回應，外加以下帳務 header：

| Header | 說明 |
|--------|-------------|
| `X-ACF-Usage-Id` | 唯一使用記錄 ID |
| `X-ACF-Amount` | 收費金額（例如 `"0.05"`） |
| `X-ACF-Free-Tier` | 若在免費額度內則為 `"true"` |
| `X-ACF-Latency-Ms` | 來回延遲時間（毫秒） |

**錯誤碼：**

| 代碼 | 意義 |
|------|---------|
| 401 | API Key 遺失或無效 |
| 404 | 找不到服務 |
| 429 | 超過速率限制 |
| 502 | 供應商無法連線 |
| 504 | 供應商逾時 |

---

### 查詢我的使用量

```
GET /api/v1/usage/me
```

取得已驗證買方的使用統計。**需要 API Key。**

**回應 (200)：**

```json
{
  "buyer_id": "my-agent",
  "total_calls": 42,
  "total_spent_usd": "2.10",
  "avg_latency_ms": 205.3
}
```

---

## Agent 身份識別

### 註冊 Agent

```
POST /api/v1/agents
```

註冊新的 Agent 身份。**需要 API Key。**

**請求主體：**

| 欄位 | 類型 | 必填 | 預設值 | 說明 |
|-------|------|----------|---------|-------------|
| `display_name` | string | 是 | -- | Agent 顯示名稱 |
| `identity_type` | string | 否 | `"api_key_only"` | `api_key_only`、`kya_jwt` 或 `did_vc` |
| `capabilities` | string[] | 否 | `[]` | 宣告的能力 |
| `wallet_address` | string | 否 | `null` | USDC 錢包地址 |
| `metadata` | object | 否 | `{}` | 自訂中繼資料 |

**範例：**

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

**回應 (201)：**

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

### 列出 Agent

```
GET /api/v1/agents
```

列出 Agent。**無需驗證。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `status` | string | `"active"` | 依狀態篩選 |
| `limit` | integer | `50` | 最大回傳數量（1--100） |
| `offset` | integer | `0` | 分頁偏移量 |

**回應 (200)：**

```json
{
  "agents": [{ "agent_id": "...", "display_name": "...", ... }],
  "count": 10
}
```

---

### 搜尋 Agent

```
GET /api/v1/agents/search
```

依名稱或 ID 搜尋 Agent。**無需驗證。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `q` | string | `""` | 搜尋查詢 |
| `limit` | integer | `20` | 最大回傳數量（1--100） |

**回應 (200)：**

```json
{
  "agents": [{ "agent_id": "...", "display_name": "...", ... }],
  "count": 3
}
```

---

### 取得 Agent

```
GET /api/v1/agents/{agent_id}
```

取得 Agent 詳細資訊。**無需驗證。**

**回應 (200)：** Agent 物件（格式同註冊回應，不含 `owner_id`）。

**錯誤 (404)：** 找不到 Agent。

---

### 更新 Agent

```
PATCH /api/v1/agents/{agent_id}
```

更新 Agent。**僅限擁有者；需要 API Key。**

**請求主體（所有欄位皆為選填）：**

| 欄位 | 類型 | 說明 |
|-------|------|-------------|
| `display_name` | string | 新顯示名稱 |
| `capabilities` | string[] | 更新後的能力 |
| `wallet_address` | string | 新錢包地址 |
| `status` | string | `active`、`suspended` 或 `deactivated` |
| `metadata` | object | 更新後的中繼資料 |

**回應 (200)：** 更新後的 Agent 物件。

---

### 停用 Agent

```
DELETE /api/v1/agents/{agent_id}
```

軟停用 Agent。**僅限擁有者；需要 API Key。**

**回應 (200)：**

```json
{"status": "deactivated", "agent_id": "550e8400-..."}
```

---

### 驗證 Agent（管理員）

```
POST /api/v1/agents/{agent_id}/verify
```

將 Agent 標記為已驗證。**需要管理員 API Key。**

**回應 (200)：** 含 `"verified": true` 的 Agent 物件。

---

## 信譽系統

信譽分數根據實際使用資料自動計算，無需使用者評分或手動輸入。

**評分公式：**

| 組成項目 | 權重 | 計算方式 |
|-----------|--------|-------------|
| 延遲分數 | 30% | `10.0 - (avg_latency_ms / 1000)`，限制在 [0, 10] |
| 可靠性分數 | 40% | `success_rate / 10`，限制在 [0, 10] |
| 回應品質 | 30% | `(1 - error_rate/100) * 10`，限制在 [0, 10] |
| **總分** | -- | 三個組成項目的加權平均 |

### 取得 Agent 信譽

```
GET /api/v1/agents/{agent_id}/reputation
```

取得 Agent 的信譽分數。**無需驗證。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `period` | string | `"all-time"` | `"all-time"` 或 `"YYYY-MM"` 格式 |
| `compute` | boolean | `false` | 若為 `true`，從即時使用資料重新計算 |

**回應 (200)：**

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

### 取得服務信譽

```
GET /api/v1/services/{service_id}/reputation
```

取得特定服務的信譽記錄。**無需驗證。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `period` | string | `"all-time"` | `"all-time"` 或 `"YYYY-MM"` 格式 |

**回應 (200)：**

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

### 信譽排行榜

```
GET /api/v1/reputation/leaderboard
```

取得依信譽分數排名的頂尖 Agent。**無需驗證。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `limit` | integer | `20` | 最大回傳數量（1--100） |

**回應 (200)：**

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

## 結算

### 建立結算（管理員）

```
POST /api/v1/settlements
```

為供應商在指定期間的收入建立結算。彙總使用記錄、計算平台手續費，並建立出款記錄。**需要管理員 API Key。**

**請求主體：**

| 欄位 | 類型 | 必填 | 說明 |
|-------|------|----------|-------------|
| `provider_id` | string | 是 | 要結算的供應商 |
| `period_start` | string | 是 | ISO 8601 起始日期（例如 `"2026-03-01T00:00:00Z"`） |
| `period_end` | string | 是 | ISO 8601 結束日期 |

**回應 (201)：**

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

### 列出結算

```
GET /api/v1/settlements
```

列出結算記錄。供應商僅能查看自己的紀錄；管理員可查看全部。**需要 API Key。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `provider_id` | string | -- | 依供應商篩選（僅限管理員） |
| `status` | string | -- | 篩選：`pending`、`processing`、`completed`、`failed` |
| `limit` | integer | `50` | 最大回傳數量（1--100） |

**回應 (200)：**

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

### 標記結算已付款（管理員）

```
PATCH /api/v1/settlements/{settlement_id}/pay
```

以交易參考編號標記結算為已付款。**需要管理員 API Key。**

**請求主體：**

| 欄位 | 類型 | 必填 | 說明 |
|-------|------|----------|-------------|
| `payment_tx` | string | 是 | 交易雜湊或參考編號 |

**回應 (200)：**

```json
{"status": "completed", "payment_tx": "0xabc123..."}
```

**錯誤 (404)：** 找不到結算記錄或已付款。

---

## 團隊管理

### 建立團隊

```
POST /api/v1/teams
```

建立新團隊。**需要 API Key。**

**請求主體：**

| 欄位 | 類型 | 必填 | 預設值 | 說明 |
|-------|------|----------|---------|-------------|
| `name` | string | 是 | -- | 團隊名稱（最多 200 字元） |
| `description` | string | 否 | `""` | 團隊說明 |
| `config` | object | 否 | `{}` | 自訂設定 |

**回應 (201)：**

```json
{"id": "team-uuid", "name": "My AI Team", "owner_id": "my-agent"}
```

---

### 列出團隊

```
GET /api/v1/teams
```

列出已驗證使用者擁有的團隊。**需要 API Key。** 未驗證時回傳空列表。

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `limit` | integer | `50` | 最大回傳數量（1--100） |

---

### 取得團隊

```
GET /api/v1/teams/{team_id}
```

取得團隊詳細資訊，包含成員、路由規則及品質閘門。**無需驗證。**

**回應 (200)：**

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

### 更新團隊

```
PATCH /api/v1/teams/{team_id}
```

更新團隊詳細資訊。**僅限擁有者；需要 API Key。**

**請求主體（所有欄位皆為選填）：**

| 欄位 | 類型 | 說明 |
|-------|------|-------------|
| `name` | string | 新名稱 |
| `description` | string | 新說明 |
| `config` | object | 更新後的設定 |

---

### 刪除團隊

```
DELETE /api/v1/teams/{team_id}
```

封存團隊（軟刪除）。**僅限擁有者；需要 API Key。**

**回應 (200)：**

```json
{"status": "archived", "id": "team-uuid"}
```

---

### 新增團隊成員

```
POST /api/v1/teams/{team_id}/members
```

將成員加入團隊。**僅限擁有者；需要 API Key。**

**請求主體：**

| 欄位 | 類型 | 必填 | 預設值 | 說明 |
|-------|------|----------|---------|-------------|
| `agent_id` | string | 是 | -- | 要加入的 Agent |
| `role` | string | 否 | `"worker"` | `leader`、`worker`、`reviewer` 或 `router` |
| `skills` | string[] | 否 | `[]` | Agent 的技能（用於路由） |

**回應 (201)：**

```json
{"id": "member-uuid", "team_id": "team-uuid", "agent_id": "agent-uuid"}
```

---

### 列出團隊成員

```
GET /api/v1/teams/{team_id}/members
```

**無需驗證。**

**回應 (200)：**

```json
{
  "members": [
    {"id": "...", "team_id": "...", "agent_id": "...", "role": "worker", "skills": ["nlp"], "joined_at": "..."}
  ],
  "count": 3
}
```

---

### 移除團隊成員

```
DELETE /api/v1/teams/{team_id}/members/{agent_id}
```

**僅限擁有者；需要 API Key。**

**回應 (200)：**

```json
{"status": "removed"}
```

---

### 新增路由規則

```
POST /api/v1/teams/{team_id}/rules
```

新增關鍵字路由規則。當傳入的任務符合關鍵字時，會路由至目標 Agent。**僅限擁有者；需要 API Key。**

**請求主體：**

| 欄位 | 類型 | 必填 | 預設值 | 說明 |
|-------|------|----------|---------|-------------|
| `name` | string | 是 | -- | 規則名稱 |
| `keywords` | string[] | 是 | -- | 觸發此規則的關鍵字 |
| `target_agent_id` | string | 是 | -- | 路由目標 Agent |
| `priority` | integer | 否 | `0` | 優先順序越高 = 越先評估 |

**回應 (201)：**

```json
{"id": "rule-uuid", "name": "NLP tasks"}
```

---

### 列出路由規則

```
GET /api/v1/teams/{team_id}/rules
```

**無需驗證。** 依優先順序降序排列。

**回應 (200)：**

```json
{
  "rules": [
    {"id": "...", "name": "NLP tasks", "keywords": ["nlp", "text"], "target_agent_id": "...", "priority": 10, "enabled": true}
  ],
  "count": 2
}
```

---

### 刪除路由規則

```
DELETE /api/v1/teams/{team_id}/rules/{rule_id}
```

**僅限擁有者；需要 API Key。**

---

### 新增品質閘門

```
POST /api/v1/teams/{team_id}/gates
```

新增品質閘門以強制執行產出標準。**僅限擁有者；需要 API Key。**

**請求主體：**

| 欄位 | 類型 | 必填 | 預設值 | 說明 |
|-------|------|----------|---------|-------------|
| `gate_type` | string | 是 | -- | `quality_score`、`latency`、`error_rate`、`coverage` 或 `custom` |
| `threshold` | float | 是 | -- | 閾值（0.0--10.0） |
| `gate_order` | integer | 否 | `0` | 執行順序（數字越小越先執行） |
| `config` | object | 否 | `{}` | 閘門專屬設定 |

**回應 (201)：**

```json
{"id": "gate-uuid", "gate_type": "quality_score"}
```

---

### 列出品質閘門

```
GET /api/v1/teams/{team_id}/gates
```

**無需驗證。** 依 `gate_order` 升序排列。

**回應 (200)：**

```json
{
  "gates": [
    {"id": "...", "gate_type": "quality_score", "threshold": 8.5, "gate_order": 0, "config": {}, "enabled": true}
  ],
  "count": 2
}
```

---

### 刪除品質閘門

```
DELETE /api/v1/teams/{team_id}/gates/{gate_id}
```

**僅限擁有者；需要 API Key。**

---

## Webhooks

### 訂閱

```
POST /api/v1/webhooks
```

建立 Webhook 訂閱。**需要 API Key。**

**請求主體：**

| 欄位 | 類型 | 必填 | 說明 |
|-------|------|----------|-------------|
| `url` | string | 是 | Webhook endpoint URL（**必須使用 HTTPS**） |
| `events` | string[] | 是 | 要訂閱的事件（參見下方） |
| `secret` | string | 是 | 用於 HMAC-SHA256 負載簽章的 secret |

**可用事件：**

| 事件 | 觸發條件 |
|-------|---------|
| `service.called` | 透過代理呼叫了某個服務 |
| `payment.completed` | 付款處理成功 |
| `reputation.updated` | Agent 的信譽分數已變更 |
| `settlement.completed` | 結算已出款 |

**範例：**

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

**回應 (201)：**

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

**Webhook 負載格式：**

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

**Webhook Header：**

| Header | 說明 |
|--------|-------------|
| `X-ACF-Signature` | 負載主體的 HMAC-SHA256 十六進位摘要 |
| `X-ACF-Event` | 事件名稱（例如 `service.called`） |
| `Content-Type` | `application/json` |

**簽章驗證（Python）：**

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

**重試機制：** 傳送失敗時最多重試 3 次，使用指數退避策略（1 秒、2 秒、4 秒）。

**限制：** 每位擁有者最多 20 個 Webhook。

---

### 列出 Webhooks

```
GET /api/v1/webhooks
```

列出您的 Webhook 訂閱。**需要 API Key。**

**回應 (200)：**

```json
{
  "webhooks": [{ "id": "...", "url": "...", "events": [...], ... }],
  "count": 3
}
```

---

### 取消訂閱

```
DELETE /api/v1/webhooks/{webhook_id}
```

刪除 Webhook 訂閱。**僅限擁有者；需要 API Key。**

**回應 (200)：**

```json
{"status": "deleted", "webhook_id": "wh-uuid"}
```

---

## 範本

### 列出團隊範本

```
GET /api/v1/templates/teams
```

取得預建的團隊設定。**無需驗證。**

**回應 (200)：**

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

### 列出服務範本

```
GET /api/v1/templates/services
```

取得預建的服務設定。**無需驗證。**

**回應 (200)：**

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

## 供應商入口

所有供應商 endpoint 皆需要供應商 API Key。

### 供應商儀表板

```
GET /api/v1/provider/dashboard
```

供應商總覽：服務數量、總呼叫次數、營收、結算。**需要供應商 API Key。**

**回應 (200)：**

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

### 我的服務

```
GET /api/v1/provider/services
```

列出供應商自己的服務及使用統計。**需要供應商 API Key。**

**回應 (200)：**

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

### 服務分析

```
GET /api/v1/provider/services/{service_id}/analytics
```

特定服務的詳細分析。**僅限擁有者；需要供應商 API Key。**

**回應 (200)：**

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

**錯誤 (403)：** `"Not your service"` — 無法查看其他供應商服務的分析。

---

### 收入

```
GET /api/v1/provider/earnings
```

收入摘要及結算歷史。**需要供應商 API Key。**

**回應 (200)：**

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

### 我的 API Key

```
GET /api/v1/provider/keys
```

列出供應商自己的 API Key（不會回傳 secret）。**需要供應商 API Key。**

**回應 (200)：**

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

### 撤銷 API Key

```
DELETE /api/v1/provider/keys/{key_id}
```

撤銷供應商自己的某個 API Key。**僅限擁有者；需要供應商 API Key。**

**回應 (200)：**

```json
{"status": "revoked", "key_id": "acf_abc123"}
```

**錯誤 (403)：** `"Not your key"` — 無法撤銷其他供應商的 key。

---

### 測試服務 Endpoint

```
POST /api/v1/provider/services/{service_id}/test
```

測試服務 endpoint 的連線狀態。**僅限擁有者；需要供應商 API Key。** 會驗證 URL 的安全性（防止對私有/回送地址的 SSRF 攻擊）。

**回應 (200)：**

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

無法連線的範例：

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

### 入門進度

```
GET /api/v1/provider/onboarding
```

追蹤供應商的入門進度（共 5 個步驟）。**需要供應商 API Key。**

**回應 (200)：**

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

## 管理後台

所有管理後台 endpoint 皆需要管理員 API Key。

### 平台統計

```
GET /api/v1/admin/stats
```

平台總覽統計。**需要管理員 API Key。**

**回應 (200)：**

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

### 每日使用量

```
GET /api/v1/admin/usage/daily
```

每日使用量彙總。**需要管理員 API Key。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `days` | integer | `30` | 回溯天數（1--90） |

**回應 (200)：**

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

### 供應商排名

```
GET /api/v1/admin/providers/ranking
```

依使用量和營收排名供應商。**需要管理員 API Key。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `limit` | integer | `20` | 最大回傳數量（1--100） |
| `period` | string | `"all-time"` | `"all-time"` 或天數（例如 `"30"`） |

**回應 (200)：**

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

### 服務健康狀態

```
GET /api/v1/admin/services/health
```

所有啟用服務的健康狀態總覽。**需要管理員 API Key。**

**回應 (200)：**

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

### 付款摘要

```
GET /api/v1/admin/payments/summary
```

依付款方式的使用量明細。**需要管理員 API Key。**

**回應 (200)：**

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

### 分析趨勢

```
GET /api/v1/admin/analytics/trends
```

依日/週/月的營收及呼叫趨勢。**需要管理員 API Key。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `granularity` | string | `"weekly"` | `"daily"`、`"weekly"` 或 `"monthly"` |
| `periods` | integer | `12` | 回傳的期間數量（1--52） |

**回應 (200)：**

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

### 熱門服務

```
GET /api/v1/admin/analytics/top-services
```

依營收、呼叫次數或延遲排名的熱門服務。**需要管理員 API Key。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `limit` | integer | `10` | 最大回傳數量（1--50） |
| `sort_by` | string | `"revenue"` | `"revenue"`、`"calls"` 或 `"latency"` |
| `days` | integer | `30` | 回溯期間天數（1--365） |

**回應 (200)：**

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

### 買方指標

```
GET /api/v1/admin/analytics/buyers
```

買方互動指標：新買方、活躍、回購、消費排行。**需要管理員 API Key。**

**Query Parameters：**

| 參數 | 類型 | 預設值 | 說明 |
|-------|------|---------|-------------|
| `days` | integer | `30` | 回溯期間天數（1--365） |

**回應 (200)：**

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

### 供應商佣金資訊

```
GET /api/v1/admin/providers/{provider_id}/commission
```

取得特定供應商的佣金資訊（成長計畫狀態）。**需要管理員 API Key。**

**回應 (200)：**

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

## 儀表板

### 管理後台 HTML 儀表板

```
GET /admin/dashboard?key={key_id}:{secret}
```

渲染適用於瀏覽器的 HTML 儀表板，包含平台指標、圖表、供應商排名及服務健康狀態。**需要透過 query parameter 提供管理員 API Key**（適用於瀏覽器的驗證方式）。

**Query Parameters：**

| 參數 | 類型 | 必填 | 說明 |
|-------|------|----------|-------------|
| `key` | string | 是 | 管理員 API Key，格式為 `key_id:secret` |

**回應：** HTML 頁面，包含：
- 平台統計（服務、Agent、團隊、營收、結算）
- 每日使用量圖表（最近 7 天）
- 付款方式分佈
- 前 10 名供應商排名
- 服務健康狀態表

---

## 健康檢查

### 健康狀態

```
GET /health
```

**回應 (200)：**

```json
{
  "status": "ok",
  "timestamp": "2026-03-19T10:00:00+00:00"
}
```

### 服務資訊

```
GET /
```

**回應 (200)：**

```json
{
  "service": "Agent Commerce Framework",
  "version": "0.6.0",
  "docs": "/docs"
}
```

---

## 錯誤回應

所有錯誤皆遵循一致的格式：

```json
{
  "detail": "Human-readable error message"
}
```

| 狀態碼 | 意義 |
|-------------|---------|
| 400 | 錯誤的請求（驗證錯誤、無效輸入） |
| 401 | API Key 遺失或無效 |
| 403 | 權限不足（角色錯誤） |
| 404 | 找不到資源 |
| 429 | 超過速率限制 |
| 500 | 伺服器內部錯誤 |
| 502 | 供應商無法連線（代理） |
| 504 | 供應商逾時（代理） |

---

## 速率限制

所有 endpoint 的速率限制為**每個 IP 每分鐘 60 個請求**，允許突發 120 個請求。每個 API Key 的速率限制也會依據該 key 的 `rate_limit` 設定來強制執行。

超過速率限制時，您會收到 HTTP 429：

```json
{"detail": "Rate limit exceeded. Try again later."}
```

速率限制在 60 秒的滑動窗口後重設。
