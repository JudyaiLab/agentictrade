"""
Send fully localized tutorial emails to registered providers.
Chinese (zh-tw) and English versions.

Usage:
    python3 send_localized_tutorial.py              # dry-run
    python3 send_localized_tutorial.py --send        # actually send
"""
import argparse
import os
import sqlite3
import httpx

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "marketplace.db")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")

# ── Chinese version ──────────────────────────────────────────

SUBJECT_ZH = "完整教學：5 分鐘在 AgenticTrade 上架你的 API"

EMAIL_ZH = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 640px; margin: 0 auto; padding: 20px; color: #333; line-height: 1.7;">

<div style="text-align: center; padding: 20px 0; border-bottom: 2px solid #6366f1;">
    <h1 style="color: #6366f1; margin: 0; font-size: 24px;">AgenticTrade</h1>
    <p style="color: #888; font-size: 14px; margin: 6px 0 0;">5 分鐘上架你的 API — 完整教學</p>
</div>

<div style="padding: 24px 0;">
    <p>{name} 你好，</p>

    <p>感謝你註冊 AgenticTrade！我們注意到你還沒上架第一個 API，所以整理了這份完整的步驟教學。照著做，5 分鐘就能讓 AI Agent 開始自動購買你的 API。</p>

    <div style="text-align: center; margin: 20px 0;">
        <a href="https://agentictrade.io/portal/login" style="display: inline-block; background: #6366f1; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 16px;">登入 Dashboard 開始上架 &rarr;</a>
    </div>

    <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">

    <h2 style="color: #6366f1; font-size: 20px;">第一步：登入 Dashboard</h2>
    <p>到 <a href="https://agentictrade.io/portal/login" style="color: #6366f1;">agentictrade.io</a> 用你的帳號密碼登入。新用戶登入後會直接看到引導式上架表單。</p>

    <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">

    <h2 style="color: #6366f1; font-size: 20px;">第二步：3 步驟引導上架</h2>
    <p>Dashboard 會顯示一個引導表單，跟著填就好：</p>

    <div style="background: #f8f9ff; border-radius: 8px; padding: 16px; margin: 12px 0;">
        <p style="margin: 0 0 8px;"><strong style="color: #6366f1;">1. API 資訊</strong></p>
        <ul style="margin: 0; padding-left: 20px;">
            <li>API 名稱（例如「文字摘要 API」）</li>
            <li>描述（AI Agent 會讀這段來決定要不要用你的服務）</li>
            <li>API 網址（你的對外 Endpoint URL）</li>
            <li>分類（AI、加密貨幣、資料分析、媒體內容、開發工具）</li>
        </ul>
    </div>

    <div style="background: #f8f9ff; border-radius: 8px; padding: 16px; margin: 12px 0;">
        <p style="margin: 0 0 8px;"><strong style="color: #6366f1;">2. 定價</strong></p>
        <ul style="margin: 0; padding-left: 20px;">
            <li>每次呼叫的價格（以 USDC 計價）</li>
            <li>免費試用次數（建議設 100 次，讓 Agent 先試用）</li>
            <li>標籤（方便被搜尋到）</li>
        </ul>
    </div>

    <div style="background: #f8f9ff; border-radius: 8px; padding: 16px; margin: 12px 0;">
        <p style="margin: 0 0 8px;"><strong style="color: #6366f1;">3. 確認上架</strong></p>
        <ul style="margin: 0; padding-left: 20px;">
            <li>預覽你填的資訊</li>
            <li>確認沒問題，按「List My API」就完成了</li>
        </ul>
    </div>

    <p>就這樣。從填表到上架，2 分鐘搞定。</p>

    <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">

    <h2 style="color: #6366f1; font-size: 20px;">第三步：還沒有 API？用範例模板建一個</h2>
    <p>我們在 GitHub 上準備了一個 FastAPI 範例，你可以直接複製、修改、部署：</p>

    <div style="background: #1a1a2e; border-radius: 8px; padding: 16px; margin: 12px 0; font-family: monospace; font-size: 13px; color: #e0e0e0; overflow-x: auto;">
        <span style="color: #888;"># 複製範例</span><br>
        git clone https://github.com/JudyaiLab/agentictrade.git<br>
        cd agent-commerce-framework/examples/template_api<br><br>
        <span style="color: #888;"># 安裝並啟動</span><br>
        pip install -r requirements.txt<br>
        uvicorn main:app --host 0.0.0.0 --port 8080<br><br>
        <span style="color: #888;"># 測試</span><br>
        curl -X POST http://localhost:8080/predict \\<br>
        &nbsp;&nbsp;-H "Content-Type: application/json" \\<br>
        &nbsp;&nbsp;-d '&#123;"text": "Hello world"&#125;'
    </div>

    <p>把 <code style="background:#f0f0f0; padding: 2px 6px; border-radius: 4px;">/predict</code> 裡的邏輯換成你自己的（天氣查詢、影像辨識、文字處理，什麼都行），然後部署到 <a href="https://railway.app" style="color: #6366f1;">Railway</a> 或 <a href="https://render.com" style="color: #6366f1;">Render</a>（都有免費方案），拿到網址後填進上架表單就完成了。</p>

    <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">

    <h2 style="color: #6366f1; font-size: 20px;">定價建議</h2>
    <table style="width: 100%; border-collapse: collapse; font-size: 14px; margin: 12px 0;">
        <tr style="background: #f8f9ff;">
            <th style="padding: 10px; text-align: left; border-bottom: 1px solid #eee;">API 類型</th>
            <th style="padding: 10px; text-align: left; border-bottom: 1px solid #eee;">建議價格</th>
        </tr>
        <tr>
            <td style="padding: 10px; border-bottom: 1px solid #eee;">通用型工具（天氣、匯率、基本 NLP）</td>
            <td style="padding: 10px; border-bottom: 1px solid #eee;">$0.01 — $0.05 / 次</td>
        </tr>
        <tr>
            <td style="padding: 10px; border-bottom: 1px solid #eee;">差異化服務（鏈上分析、醫療摘要）</td>
            <td style="padding: 10px; border-bottom: 1px solid #eee;">$0.50 — $2.00 / 次</td>
        </tr>
        <tr>
            <td style="padding: 10px;">免費試用額度</td>
            <td style="padding: 10px;"><strong>一定要設</strong>，建議 100 次</td>
        </tr>
    </table>

    <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">

    <h2 style="color: #6366f1; font-size: 20px;">為什麼現在上架？</h2>
    <ul style="line-height: 2;">
        <li><strong>首月 0% 佣金</strong>——你賺的全部是你的</li>
        <li>之後佣金只有 5-10%（對比 RapidAPI 的 25%）</li>
        <li>Founding Seller 特權——早期上架者享有永久優惠費率</li>
        <li>AI Agent 全天候自動購買——真正的被動收入</li>
        <li>即時數據面板追蹤使用量和收益</li>
    </ul>

    <div style="text-align: center; margin: 28px 0;">
        <a href="https://agentictrade.io/portal/login" style="display: inline-block; background: #22c55e; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 16px;">現在就上架我的 API &rarr;</a>
    </div>

    <p>有任何問題直接回覆這封信，我們很樂意幫忙。</p>

    <p>AgenticTrade 團隊</p>
</div>

<div style="padding: 16px 0; border-top: 1px solid #eee; font-size: 12px; color: #888; text-align: center;">
    <p>你收到這封信是因為你在 agentictrade.io 註冊了帳號。<br>
    <a href="https://agentictrade.io" style="color: #6366f1;">AgenticTrade</a> — AI Agent 的 API 交易市集</p>
</div>

</body>
</html>
"""

# ── English version ──────────────────────────────────────────

SUBJECT_EN = "Full Tutorial: List Your API on AgenticTrade in 5 Minutes"

EMAIL_EN = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 640px; margin: 0 auto; padding: 20px; color: #333; line-height: 1.7;">

<div style="text-align: center; padding: 20px 0; border-bottom: 2px solid #6366f1;">
    <h1 style="color: #6366f1; margin: 0; font-size: 24px;">AgenticTrade</h1>
    <p style="color: #888; font-size: 14px; margin: 6px 0 0;">List Your API in 5 Minutes — Full Tutorial</p>
</div>

<div style="padding: 24px 0;">
    <p>Hi {name},</p>

    <p>Thanks for signing up for AgenticTrade! We noticed you haven't listed your first API yet, so we put together this step-by-step guide. Follow along and AI agents will start buying your API automatically.</p>

    <div style="text-align: center; margin: 20px 0;">
        <a href="https://agentictrade.io/portal/login" style="display: inline-block; background: #6366f1; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 16px;">Log In & List Your API &rarr;</a>
    </div>

    <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">

    <h2 style="color: #6366f1; font-size: 20px;">Step 1: Log In to Your Dashboard</h2>
    <p>Go to <a href="https://agentictrade.io/portal/login" style="color: #6366f1;">agentictrade.io</a> and log in. New users will see a guided onboarding wizard right away.</p>

    <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">

    <h2 style="color: #6366f1; font-size: 20px;">Step 2: List Your API with the 3-Step Wizard</h2>
    <p>The dashboard shows a guided form. Just fill it in:</p>

    <div style="background: #f8f9ff; border-radius: 8px; padding: 16px; margin: 12px 0;">
        <p style="margin: 0 0 8px;"><strong style="color: #6366f1;">1. API Info</strong></p>
        <ul style="margin: 0; padding-left: 20px;">
            <li>Service name (e.g. "Text Summarizer API")</li>
            <li>Description (AI agents read this to decide whether to use your service)</li>
            <li>Endpoint URL (your public API address)</li>
            <li>Category (AI, Crypto, Data, Media, Developer Tools)</li>
        </ul>
    </div>

    <div style="background: #f8f9ff; border-radius: 8px; padding: 16px; margin: 12px 0;">
        <p style="margin: 0 0 8px;"><strong style="color: #6366f1;">2. Pricing</strong></p>
        <ul style="margin: 0; padding-left: 20px;">
            <li>Price per call (in USDC)</li>
            <li>Free tier calls (we recommend 100 so agents can try first)</li>
            <li>Tags (helps with discoverability)</li>
        </ul>
    </div>

    <div style="background: #f8f9ff; border-radius: 8px; padding: 16px; margin: 12px 0;">
        <p style="margin: 0 0 8px;"><strong style="color: #6366f1;">3. Confirm</strong></p>
        <ul style="margin: 0; padding-left: 20px;">
            <li>Review your service details</li>
            <li>Click "List My API" and you're live</li>
        </ul>
    </div>

    <p>That's it. 3 steps, 2 minutes, and your API is on the marketplace.</p>

    <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">

    <h2 style="color: #6366f1; font-size: 20px;">Step 3: Don't Have an API? Build One in 5 Minutes</h2>
    <p>We have a FastAPI template on GitHub you can fork, customize, and deploy:</p>

    <div style="background: #1a1a2e; border-radius: 8px; padding: 16px; margin: 12px 0; font-family: monospace; font-size: 13px; color: #e0e0e0; overflow-x: auto;">
        <span style="color: #888;"># Clone the template</span><br>
        git clone https://github.com/JudyaiLab/agentictrade.git<br>
        cd agent-commerce-framework/examples/template_api<br><br>
        <span style="color: #888;"># Install &amp; run</span><br>
        pip install -r requirements.txt<br>
        uvicorn main:app --host 0.0.0.0 --port 8080<br><br>
        <span style="color: #888;"># Test it</span><br>
        curl -X POST http://localhost:8080/predict \\<br>
        &nbsp;&nbsp;-H "Content-Type: application/json" \\<br>
        &nbsp;&nbsp;-d '&#123;"text": "Hello world"&#125;'
    </div>

    <p>Replace the <code style="background:#f0f0f0; padding: 2px 6px; border-radius: 4px;">/predict</code> logic with your own (weather data, image recognition, text analysis — anything), deploy to <a href="https://railway.app" style="color: #6366f1;">Railway</a> or <a href="https://render.com" style="color: #6366f1;">Render</a> (both have free tiers), paste the URL into the wizard, and you're done.</p>

    <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">

    <h2 style="color: #6366f1; font-size: 20px;">Pricing Guide</h2>
    <table style="width: 100%; border-collapse: collapse; font-size: 14px; margin: 12px 0;">
        <tr style="background: #f8f9ff;">
            <th style="padding: 10px; text-align: left; border-bottom: 1px solid #eee;">API Type</th>
            <th style="padding: 10px; text-align: left; border-bottom: 1px solid #eee;">Suggested Price</th>
        </tr>
        <tr>
            <td style="padding: 10px; border-bottom: 1px solid #eee;">General utilities (weather, exchange rates, NLP)</td>
            <td style="padding: 10px; border-bottom: 1px solid #eee;">$0.01 — $0.05 / call</td>
        </tr>
        <tr>
            <td style="padding: 10px; border-bottom: 1px solid #eee;">Specialized services (on-chain analysis, medical)</td>
            <td style="padding: 10px; border-bottom: 1px solid #eee;">$0.50 — $2.00 / call</td>
        </tr>
        <tr>
            <td style="padding: 10px;">Free tier</td>
            <td style="padding: 10px;"><strong>Always set one</strong> — we recommend 100 calls</td>
        </tr>
    </table>

    <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;">

    <h2 style="color: #6366f1; font-size: 20px;">Why List Now?</h2>
    <ul style="line-height: 2;">
        <li><strong>First month: 0% commission</strong> — keep everything you earn</li>
        <li>After that: only 5-10% (vs. RapidAPI's 25%)</li>
        <li>Founding Seller perks — early listers get permanent discounted rates</li>
        <li>AI agents buy 24/7 automatically — true passive income</li>
        <li>Real-time analytics dashboard for tracking usage and revenue</li>
    </ul>

    <div style="text-align: center; margin: 28px 0;">
        <a href="https://agentictrade.io/portal/login" style="display: inline-block; background: #22c55e; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 16px;">List My API Now &rarr;</a>
    </div>

    <p>Questions? Just reply to this email — we're happy to help.</p>

    <p>Best,<br>The AgenticTrade Team</p>
</div>

<div style="padding: 16px 0; border-top: 1px solid #eee; font-size: 12px; color: #888; text-align: center;">
    <p>You're receiving this because you registered at agentictrade.io.<br>
    <a href="https://agentictrade.io" style="color: #6366f1;">AgenticTrade</a> — The marketplace where AI agents trade APIs.</p>
</div>

</body>
</html>
"""


def get_providers_without_services():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    providers = cursor.execute("""
        SELECT pa.id, pa.email, pa.display_name, pa.api_key_id, pa.locale
        FROM provider_accounts pa
        WHERE pa.email NOT LIKE '%@test.local'
        AND pa.status = 'active'
    """).fetchall()
    result = []
    for p in providers:
        svc_count = cursor.execute(
            "SELECT COUNT(*) FROM services WHERE provider_id = ?",
            (p["api_key_id"],),
        ).fetchone()[0]
        if svc_count == 0:
            result.append(dict(p))
    db.close()
    return result


def send_email(to_email, name, locale="en"):
    is_zh = locale.startswith("zh")
    subject = SUBJECT_ZH if is_zh else SUBJECT_EN
    template = EMAIL_ZH if is_zh else EMAIL_EN
    html = template.format(name=name or "there")

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": "AgenticTrade <onboarding@agentictrade.io>",
            "to": [to_email],
            "subject": subject,
            "html": html,
        },
        timeout=10,
    )
    return resp.status_code, resp.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()

    providers = get_providers_without_services()
    print(f"Found {len(providers)} providers without services:\n")
    for p in providers:
        name = p["display_name"] or p["email"].split("@")[0]
        locale = p.get("locale", "en") or "en"
        lang = "zh-tw" if locale.startswith("zh") else "en"
        print(f"  {p['email']} ({name}) -> {lang}")
        if args.send:
            if not RESEND_API_KEY:
                print("  ERROR: RESEND_API_KEY not set")
                continue
            status, resp = send_email(p["email"], name, locale)
            print(f"  -> Sent! Status: {status}, ID: {resp.get('id', 'N/A')}")
        else:
            print("  -> [DRY RUN]")

    if not args.send:
        print(f"\nDry run. Use --send to send {len(providers)} emails.")


if __name__ == "__main__":
    main()
