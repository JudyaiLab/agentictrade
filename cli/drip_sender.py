"""
Drip email sender — processes scheduled drip emails via Resend.

Usage:
    python -m cli.drip_sender           # process all due drip emails
    python -m cli.drip_sender --dry-run # preview without sending

Cron: */30 * * * * cd /path/to/agent-commerce-framework && python -m cli.drip_sender
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketplace.db import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [drip] %(levelname)s %(message)s",
)
logger = logging.getLogger("drip_sender")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "onboarding@agentictrade.io")
FROM_NAME = os.environ.get("FROM_NAME", "AgenticTrade")
DB_PATH = Path(__file__).resolve().parent.parent / "marketplace.db"

# Must match email.py DRIP_SCHEDULE
DRIP_SCHEDULE = [
    (2, "quickstart", "3 Quick-Start Tips for Your Agent Marketplace"),
    (5, "usecase", "How Other Agents Are Using AgenticTrade"),
    (10, "platform", "Your Agents Can Earn — Here's How"),
]

DRIP_TEMPLATES = {
    "quickstart": """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
<h1 style="color: #1a1a2e; font-size: 24px;">3 Quick-Start Tips</h1>
<p>Hey! You downloaded the Starter Kit a couple days ago. Here are 3 tips to get the most out of it:</p>
<ol style="line-height: 2;">
  <li><strong>Start with the Demo service</strong> — it's free and shows the full flow (discovery → call → billing)</li>
  <li><strong>Try the SDK</strong> — <code>pip install agentictrade</code> gets you a Python client in one command</li>
  <li><strong>Check Chapter 3</strong> of the guide — it walks through building your first paid agent service</li>
</ol>
<p>Questions? Just reply to this email.</p>
<p style="margin-top: 24px;">— The AgenticTrade Team</p>
{unsub_footer}
</body></html>""",
    "usecase": """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
<h1 style="color: #1a1a2e; font-size: 24px;">How Agents Are Using AgenticTrade</h1>
<p>Here's what builders on the platform are doing:</p>
<ul style="line-height: 2;">
  <li><strong>Crypto Scanner API</strong> — AI agents call CoinSifter to scan 200+ coins for trading signals</li>
  <li><strong>Strategy Backtesting</strong> — agents submit strategies and get historical performance data</li>
  <li><strong>Data Enrichment</strong> — agents chain multiple services together for complex analysis</li>
</ul>
<p>The marketplace is still early — <strong>first movers get Founding Seller status</strong> (permanent badge + lower commission forever).</p>
<p><a href="https://agentictrade.io" style="color: #6C63FF;">See what's on the marketplace →</a></p>
{unsub_footer}
</body></html>""",
    "platform": """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
<h1 style="color: #1a1a2e; font-size: 24px;">Your Agents Can Earn Money</h1>
<p>If you've built an agent that does something useful — it can earn revenue on AgenticTrade.</p>
<h2 style="font-size: 18px;">How it works:</h2>
<ol style="line-height: 2;">
  <li>Wrap your agent's capability as an API service</li>
  <li>Register it on AgenticTrade (takes 5 minutes)</li>
  <li>Other agents discover and pay for your service automatically</li>
</ol>
<h2 style="font-size: 18px;">Provider perks right now:</h2>
<ul style="line-height: 2;">
  <li><strong>0% commission</strong> for your first month (keep everything)</li>
  <li><strong>Founding Seller badge</strong> if you're in the first 50</li>
  <li>Free health monitoring + quality scoring for your APIs</li>
</ul>
<p><a href="https://agentictrade.io/api-docs" style="color: #6C63FF;">Read the Provider Guide →</a></p>
{unsub_footer}
</body></html>""",
}

UNSUB_FOOTER_TEMPLATE = """<hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">
<p style="color: #999; font-size: 12px;">
  You're receiving this because you downloaded the AgenticTrade Starter Kit.
  <a href="{unsub_url}" style="color: #999;">Unsubscribe</a>
</p>"""


def _unsub_token(email: str) -> str:
    """Generate HMAC token for unsubscribe link — must match email.py."""
    import hashlib
    import hmac

    secret = os.environ.get("ACF_ADMIN_SECRET", "")
    return hmac.new(secret.encode(), email.lower().encode(), hashlib.sha256).hexdigest()[:32]


def _send_drip_email(email: str, stage_name: str, subject: str) -> bool:
    """Send a single drip email via Resend. Returns True on success."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — cannot send drip email")
        return False

    template = DRIP_TEMPLATES.get(stage_name)
    if not template:
        logger.error("No template for drip stage: %s", stage_name)
        return False

    token = _unsub_token(email)
    unsub_url = f"https://agentictrade.io/api/v1/unsubscribe?email={email}&token={token}"
    unsub_footer = UNSUB_FOOTER_TEMPLATE.format(unsub_url=unsub_url)
    html = template.format(unsub_footer=unsub_footer)

    import httpx

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": f"{FROM_NAME} <{FROM_EMAIL}>",
            "to": [email],
            "subject": subject,
            "html": html,
        },
        timeout=15,
    )
    if resp.status_code in (200, 201):
        logger.info("Sent drip '%s' to %s", stage_name, email)
        return True
    logger.warning("Resend error %d for %s: %s", resp.status_code, email, resp.text[:200])
    return False


def process_drip(db_path: Path, dry_run: bool = False) -> int:
    """Process all due drip emails. Returns count of emails sent."""
    db = Database(db_path)
    now = datetime.now(timezone.utc).isoformat()
    due = db.list_subscribers_for_drip(now)

    if not due:
        logger.info("No drip emails due")
        return 0

    sent = 0
    for sub in due:
        stage = sub["drip_stage"]
        if stage >= len(DRIP_SCHEDULE):
            # All drips sent — clear next_at
            db.advance_drip(sub["id"], stage, None)
            continue

        _, stage_name, subject = DRIP_SCHEDULE[stage]
        if dry_run:
            logger.info("[DRY RUN] Would send '%s' to %s", stage_name, sub["email"])
            sent += 1
            continue

        if _send_drip_email(sub["email"], stage_name, subject):
            # Calculate next drip time
            next_stage = stage + 1
            if next_stage < len(DRIP_SCHEDULE):
                next_delay = DRIP_SCHEDULE[next_stage][0]
                next_at = (datetime.now(timezone.utc) + timedelta(days=next_delay)).isoformat()
            else:
                next_at = None  # No more drips
            db.advance_drip(sub["id"], next_stage, next_at)
            sent += 1

    logger.info("Processed %d drip emails (%d due)", sent, len(due))
    return sent


def main() -> None:
    parser = argparse.ArgumentParser(description="Send scheduled drip emails")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Database path")
    args = parser.parse_args()

    if not args.db.exists():
        logger.error("Database not found: %s", args.db)
        sys.exit(1)

    sent = process_drip(args.db, dry_run=args.dry_run)
    if sent:
        logger.info("Done — %d emails %s", sent, "would be sent" if args.dry_run else "sent")


if __name__ == "__main__":
    main()
