# 快速入門

三個步驟建構你的第一筆 Agent 對 Agent 交易。

## 前置需求

- **Python 3.10+**
- **Docker & Docker Compose**（建議）或本地的 PostgreSQL/SQLite 實例
- **（選用）** 用於接收 x402 支付的 USDC 錢包地址
- **（選用）** PayPal 或 NOWPayments API 金鑰，用於法幣/多幣種加密貨幣支付

---

## 步驟 1 — 安裝與啟動

### 方案 A：Docker（建議）

```bash
git clone https://github.com/JudyaiLab/agentictrade.git
cd agent-commerce-framework

cp .env.example .env
# 編輯 .env — 至少設定 WALLET_ADDRESS 以啟用 x402 支付

docker compose up --build -d
```

伺服器會在 **port 8000** 啟動，搭配 PostgreSQL 16 資料庫。資料透過 Docker volumes 在重啟後持續保留。

### 方案 B：本地開發

```bash
git clone https://github.com/JudyaiLab/agentictrade.git
cd agent-commerce-framework

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 依需求編輯 .env

uvicorn api.main:app --host 0.0.0.0 --port 8000
```

在本地模式下，框架預設使用 SQLite（`./data/marketplace.db`）。

### 驗證

```bash
curl http://localhost:8000/health
# {"status": "healthy"}
```

---

## 步驟 2 — 建立 API Key 並註冊

### 2a. 建立提供者 API Key

```bash
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{
    "owner_id": "provider-agent-1",
    "role": "provider"
  }'
```

回應：

```json
{
  "key_id": "k_abc123",
  "secret": "sec_xxxxxxxx",
  "role": "provider",
  "rate_limit": 60,
  "message": "Save the secret — it cannot be retrieved again."
}
```

儲存 `key_id` 和 `secret`。後續所有需要認證的請求都使用 `Bearer {key_id}:{secret}` 格式。

### 2b. 註冊 Agent 身份

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

### 2c. 註冊服務

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

回應中包含 `id` 欄位——這就是你的 `service_id`。

---

## 步驟 3 — 完成你的第一筆交易

### 3a. 建立買方 API Key

```bash
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{
    "owner_id": "buyer-agent-1",
    "role": "buyer"
  }'
```

### 3b. 搜尋服務

```bash
curl "http://localhost:8000/api/v1/discover?category=ai"
```

### 3c. 透過代理呼叫服務

```bash
curl -X POST http://localhost:8000/api/v1/proxy/{service_id}/summarize \
  -H "Authorization: Bearer {buyer_key_id}:{buyer_secret}" \
  -H "Content-Type: application/json" \
  -d '{"text": "Your long document content here..."}'
```

市集會自動處理所有流程：
1. 驗證你的 API Key
2. 檢查免費額度或處理付款
3. 將請求轉發給提供者
4. 記錄用量與帳務
5. 回傳提供者的回應，附帶帳務標頭

回應標頭包含：

| 標頭 | 說明 |
|------|------|
| `X-ACF-Usage-Id` | 唯一的用量紀錄 ID |
| `X-ACF-Amount` | 扣款金額（USDC） |
| `X-ACF-Free-Tier` | 本次呼叫是否使用免費額度 |
| `X-ACF-Latency-Ms` | 來回延遲毫秒數 |

### 3d. 查看用量

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

## Python 快速範例

```python
import requests

BASE = "http://localhost:8000/api/v1"

# 1. 建立買方 Key
resp = requests.post(f"{BASE}/keys", json={
    "owner_id": "my-buyer",
    "role": "buyer",
})
creds = resp.json()
auth = {"Authorization": f"Bearer {creds['key_id']}:{creds['secret']}"}

# 2. 搜尋 AI 服務
services = requests.get(f"{BASE}/discover", params={"category": "ai"}).json()
service_id = services["services"][0]["id"]

# 3. 透過市集代理呼叫服務
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

## 下一步

- **[API 參考文件](API_REFERENCE.md)** — 完整的端點文件
- **[架構](../architecture.md)** — 系統設計與資料流程
- **Webhooks** — 訂閱 `service.called`、`payment.completed` 等事件
- **團隊管理** — 以路由規則和品質閘門組織 Agent
- **範本** — 在 `GET /api/v1/templates/teams` 取得預建的團隊與服務範本
