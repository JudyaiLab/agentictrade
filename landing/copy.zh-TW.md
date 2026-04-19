# AgenticTrade — Landing Page 文案（繁體中文）
# agentictrade.io

---

## HERO 區塊

**主標：**
> 這是一個 API 市集。但付錢的不是人類——是 AI Agent。

**副標：**
> Provider（供應商）：上架一次，全球 AI Agent 自動發現、呼叫、付款給你。
> Builder（開發者）：幾分鐘完成整合——Agent 負責發現、認證、結帳。

**按鈕：**
[開始以 Agent 身份開發] [上架你的 API]

---

## 給 PROVIDER

**標題：** 你的 API。Agent 自動找到它。自動付錢給你。

大多數 API 市集是為人類開發者設計的。你要寫文件、維護整合手冊、回覆同一堆問題用 Email 回覆到翻。

AgenticTrade 不一樣。你的 API 會得到一個機器可讀的 MCP Tool Descriptor——Agent 可以發現它、理解它的功能、用正確的參數呼叫它，並且用 USDC 或 USDT 自動付款給你。

不需要人類介入。沒有帳單客服工單。只有收入。

**功能：**
- MCP Tool Descriptor 從你的 API 規格自動產生
- 機器可讀文件——取代傳統給人類看的文件
- Proxy Key 系統——你的真實 API Key 永遠不會離開我們的基礎設施
- USDC/USDT 或法幣自動結算
- 用量分析——每個 Agent 的使用明細

**按鈕：** [上架你的 API——第一個月免費]

---

## 給 AGENT BUILDERS

**標題：** 別再把 API Key 寫死在程式碼裡了。讓你的 Agent 自己處理。

當你把 API 寫進 AI Agent 時，你其實在賭幾件事：
1. API 會一直可用
2. 價格不會變
3. API Key 不會外洩
4. 擁有帳戶的那個人不會撤銷權限

AgenticTrade 把這四個失敗場景全部消除。你的 Agent 透過 Proxy Key 連線、透過 MCP Descriptor 發現功能、用真實用量付費——用從來不需要人類碰的微交易。

**功能：**
- 一次整合，無限供應商——MCP Descriptor 登錄系統
- Proxy Key 認證——不再外洩 API Key
- Agent 從你一次充值的錢包餘額自己付款
- 即時用量日誌——每個 Agent、每次 API 呼叫的明細
- 不會突然遇到 rate limit——Agent 在呼叫前就能看到價格

**按鈕：** [打造一個自己付帳的 Agent]

---

## HOW IT WORKS（運作方式）

**標題：** 三步驟。帳單零人類接觸。

### 供應商（Provider）
1. **註冊你的 API**——貼上你的 OpenAPI 規格或手動定義
2. **設定價格**——按次計費、階梯式、或免費增值。Agent 在呼叫前會先讀取定價資訊
3. **自動收款**——款項以 USDC/USDT 或法幣結算，不需要開發票

### Agent 開發者（Builder）
1. **發現**——瀏覽 MCP 登錄系統或讓你的 Agent 自動查詢
2. **整合**——Proxy Key + MCP Descriptor = Agent 看得懂怎麼呼叫這個 API
3. **部署**——Agent 呼叫、Agent 付款。你只負責充值錢包。就這樣。

---

## 佣金比較表

| | AgenticTrade | RapidAPI |
|---|---|---|
| 第一個月 | **0%** | 0% |
| 第 2–3 個月 | **5%** | 25% |
| 第 4 個月起 | **10%** | 25% |
| Agent 對 Agent 付款 | 有 | 無 |
| MCP Tool Descriptor | 有 | 無 |
| Proxy Key 保護 | 有 | 無 |
| USDC/USDT 結算 | 有 | 無 |

**結論：** RapidAPI 永遠抽 25%。我們第一個月 0%、第 2-3 個月 5%、第 4 個月起 10%。而且不像 RapidAPI，你的 Agent 在這裡真的可以自己付帳。

---

## 社會認證 / 信任信號

**數據（正式上線後填入真實數字）：**
- X,XXX 個 API 已上架
- X,XXX 位 Agent 開發者
- $XX,XXX 已付給供應商

**使用者見證（正式上線後填入）：**

> 「我們把天氣 API 搬到 AgenticTrade。三週內，三個企業級 Agent 完成整合——沒有業務電話、沒有合約談判。5% 佣金對比 RapidAPI 的 25%，差異非常明顯。」
> — [供應商名稱]，WeatherData API

> 「終於有一個 API 市集，我的 Agent 可以真的為自己使用的東西付錢。MCP Descriptor 這個功能太神奇了——不用再猜參數格式。」
> — [開發者名稱]，Agent 開發者

**信任信號：**
- 所有付款上鏈，可驗證
- SOC 2 Type II 審查進行中
- Proxy Key 基礎設施已審計 [日期]
- 24/7 事件應變機制

---

## FAQ（常見問題）

**Q: AgenticTrade 跟 RapidAPI 的核心差異是什麼？**
A: 兩件事。第一，Agent 可以自己在這裡付款——不只是人類。你的 AI Agent 可以擁有自己的錢包、發現 API、並為使用量付款，全程不需要你介入。第二，我們的佣金是 0%→5%→10%，而不是 RapidAPI 的固定 25%。以一個每月 $10,000 收入的供應商來說，這是每月 $2,500 進你口袋，而不是進他們口袋。

**Q: MCP Tool Descriptor 是什麼？**
A: MCP（Model Context Protocol）Tool Descriptor 是對 API 功能的機器可讀描述——包含 API 做什麼、需要什麼參數、回傳什麼結果。你的 Agent 讀取它、理解怎麼呼叫 API、並正確執行——不需要你寫死任何東西。

**Q: Proxy Key 系統是怎麼運作的？**
A: 當你上架 API 時，你給我們的是真實金鑰的代理版本——不是金鑰本身。Agent 透過我們的基礎設施使用 Proxy Key 呼叫你的 API。你的真實金鑰永遠不會暴露。你可以隨時輪換金鑰，而不需要動到 Agent 程式碼。

**Q: AgenticTrade 支援哪些代幣？**
A: 供應商可以選擇以 USDC、USDT 或法幣收款。Agent 開發者以 USDC 或 USDT 充值錢包。

**Q: 如果我的 Agent 不是加密原生應用，還能用 AgenticTrade 嗎？**
A: 可以。Agent 開發者不需要了解任何加密貨幣相關知識。你用信用卡購買 USDC 充值錢包，你的 Agent 處理剩下的事。對於供應商來說，你可以收款法幣，完全不需要碰錢包。

**Q: Agent 是怎麼發現我的 API 的？**
A: MCP 登錄系統可以按功能搜尋。Agent 可以這樣查詢：「找一個能查首爾天氣、回傳攝氏溫度的天氣 API」。如果你的 MCP Descriptor 符合，Agent 就知道怎麼呼叫、要付多少錢，可以一步完成。

**Q: 如果我的 API 掛了怎麼辦？**
A: AgenticTrade 不控制你的 API——你是供應商。我們處理付款失敗和重試機制，但可用性由你和消費你服務的 Agent 之間決定。SLA 承諾是你和你的用戶之間的事。

**Q: AgenticTrade 現在上線了嗎？**
A: 目前在 early access 階段。[註冊搶先體驗 / 加入等待清單]

---

## 最終 CTA

**標題：**
> 網路是給人類用的。你的 Agent 需要自己的經濟系統。

**內文：**
AgenticTrade 是專為 AI Agent——而非部署它們的人類——打造的 API 市集。上架你的 API，讓全球 Agent 為你帶來收入。或者打造一個可以自己付帳的 Agent。

**按鈕：**
[註冊成為供應商] [以 AgenticTrade 開始開發]

**備註：**
不需要信用卡。第一個月佣金 0%。
