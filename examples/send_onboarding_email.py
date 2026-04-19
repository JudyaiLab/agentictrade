"""
Send onboarding tutorial email to registered providers who haven't listed a service yet.

Usage:
    python3 send_onboarding_email.py              # dry-run (preview only)
    python3 send_onboarding_email.py --send        # actually send
"""
import argparse
import json
import os
import sqlite3
import httpx

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "marketplace.db")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")

SUBJECT_EN = "List Your API on AgenticTrade in 5 Minutes"
SUBJECT_ZH = "5 分鐘在 AgenticTrade 上架你的 API"

EMAIL_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">

<div style="text-align: center; padding: 20px 0; border-bottom: 2px solid #6366f1;">
    <h1 style="color: #6366f1; margin: 0; font-size: 24px;">AgenticTrade</h1>
</div>

<div style="padding: 24px 0;">
    <p>Hi {name},</p>

    <p>Thanks for signing up for AgenticTrade! We noticed you haven't listed your first API yet.</p>

    <p><strong>Good news: we just made it way easier.</strong></p>

    <h2 style="color: #6366f1; font-size: 18px;">New: List Your API in 2 Minutes</h2>

    <p>Log in to your dashboard and you'll see a <strong>step-by-step wizard</strong> that walks you through listing your API — no curl commands needed.</p>

    <div style="text-align: center; margin: 24px 0;">
        <a href="https://agentictrade.io/portal/login" style="display: inline-block; background: #6366f1; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 16px;">Log In & List Your API &rarr;</a>
    </div>

    <h2 style="color: #6366f1; font-size: 18px;">Don't Have an API Yet?</h2>

    <p>We have a <strong>template API</strong> you can fork and deploy in minutes:</p>

    <ol style="line-height: 1.8;">
        <li>Fork our <a href="https://github.com/JudyaiLab/agentictrade/tree/main/examples/template_api" style="color: #6366f1;">template API</a></li>
        <li>Replace the <code>/predict</code> endpoint with your logic</li>
        <li>Deploy to Railway, Render, or any cloud (free tiers available)</li>
        <li>List it on AgenticTrade using the dashboard wizard</li>
    </ol>

    <h2 style="color: #6366f1; font-size: 18px;">Why List Now?</h2>

    <ul style="line-height: 1.8;">
        <li><strong>First month: 0% commission</strong> — keep everything you earn</li>
        <li>AI agents automatically discover and pay for your API</li>
        <li>100 free-tier calls included — agents can try before they buy</li>
        <li>Real-time analytics dashboard for tracking usage and revenue</li>
    </ul>

    <p>Questions? Just reply to this email — we're here to help.</p>

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
    """Get registered providers who haven't listed any services."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    cursor = db.cursor()

    # Get all real providers (exclude test accounts)
    providers = cursor.execute("""
        SELECT pa.id, pa.email, pa.display_name, pa.api_key_id, pa.locale
        FROM provider_accounts pa
        WHERE pa.email NOT LIKE '%@test.local'
        AND pa.status = 'active'
    """).fetchall()

    result = []
    for p in providers:
        # Check if provider has any services
        svc_count = cursor.execute(
            "SELECT COUNT(*) FROM services WHERE provider_id = ?",
            (p["api_key_id"],),
        ).fetchone()[0]

        if svc_count == 0:
            result.append(dict(p))

    db.close()
    return result


def send_email(to_email: str, name: str, locale: str = "en"):
    """Send onboarding email via Resend."""
    subject = SUBJECT_ZH if locale.startswith("zh") else SUBJECT_EN
    html = EMAIL_HTML.format(name=name or "there")

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
    parser.add_argument("--send", action="store_true", help="Actually send emails")
    args = parser.parse_args()

    providers = get_providers_without_services()
    print(f"Found {len(providers)} providers without services:\n")

    for p in providers:
        name = p["display_name"] or p["email"].split("@")[0]
        locale = p.get("locale", "en") or "en"
        print(f"  {p['email']} ({name}, {locale})")

        if args.send:
            if not RESEND_API_KEY:
                print("  ERROR: RESEND_API_KEY not set")
                continue
            status, resp = send_email(p["email"], name, locale)
            print(f"  -> Sent! Status: {status}, ID: {resp.get('id', 'N/A')}")
        else:
            print("  -> [DRY RUN] Would send onboarding email")

    if not args.send:
        print(f"\nDry run complete. Use --send to actually send {len(providers)} emails.")


if __name__ == "__main__":
    main()
