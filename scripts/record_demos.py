"""
Record demo clips for ARC hackathon video using Playwright.
Outputs MP4 files to docs/demo_clips/
"""
import subprocess
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path(__file__).parent.parent / "docs" / "demo_clips"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://agentictrade.io"


def record_clip_b_discovery():
    """Clip B: Service Discovery — browser showing API response."""
    print("\n=== Recording Clip B: Service Discovery ===")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            record_video_dir=str(OUTPUT_DIR),
            record_video_size={"width": 1280, "height": 720},
            viewport={"width": 1280, "height": 720},
        )
        page = ctx.new_page()

        # Show discover API
        page.goto(f"{BASE_URL}/api/v1/discover?q=coin")
        page.wait_for_timeout(4000)

        # Show MCP manifest
        page.goto(f"{BASE_URL}/.well-known/mcp.json")
        page.wait_for_timeout(4000)

        page.close()
        ctx.close()
        browser.close()

    _rename_latest("clip_b_discovery.webm")
    print("  -> clip_b_discovery.webm")


def record_clip_d_nano_dashboard():
    """Clip D: Nano Dashboard."""
    print("\n=== Recording Clip D: Nano Dashboard ===")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            record_video_dir=str(OUTPUT_DIR),
            record_video_size={"width": 1280, "height": 720},
            viewport={"width": 1280, "height": 720},
        )
        page = ctx.new_page()

        page.goto(f"{BASE_URL}/nano-dashboard")
        page.wait_for_timeout(3000)

        # Scroll down to show transactions
        page.evaluate("window.scrollTo(0, 400)")
        page.wait_for_timeout(3000)

        # Scroll more to show transaction table
        page.evaluate("window.scrollTo(0, 800)")
        page.wait_for_timeout(3000)

        page.close()
        ctx.close()
        browser.close()

    _rename_latest("clip_d_nano_dashboard.webm")
    print("  -> clip_d_nano_dashboard.webm")


def _login_and_get_context(p, email="judycing@gmail.com", password=""):
    """Login via portal and return context with session cookie."""
    browser = p.chromium.launch()
    ctx = browser.new_context(
        record_video_dir=str(OUTPUT_DIR),
        record_video_size={"width": 1280, "height": 720},
        viewport={"width": 1280, "height": 720},
    )
    page = ctx.new_page()
    page.goto(f"{BASE_URL}/portal/login")
    page.wait_for_timeout(1000)
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_timeout(2000)
    return browser, ctx, page


def record_clip_f_budget(session_cookie: str):
    """Clip F: Budget Management UI."""
    print("\n=== Recording Clip F: Budget Management ===")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            record_video_dir=str(OUTPUT_DIR),
            record_video_size={"width": 1280, "height": 720},
            viewport={"width": 1280, "height": 720},
        )
        # Use localhost with direct session injection
        ctx.add_cookies([{
            "name": "acf_portal_session",
            "value": session_cookie,
            "domain": "localhost",
            "path": "/",
        }])
        page = ctx.new_page()

        page.goto("http://localhost:8092/portal/budget")
        page.wait_for_timeout(2000)

        # Fill in budget values
        daily_input = page.query_selector('input[name="daily_limit_usd"]')
        if daily_input:
            page.fill('input[name="daily_limit_usd"]', "1.00")
            page.wait_for_timeout(500)
            page.fill('input[name="per_tx_limit_usd"]', "0.01")
            page.wait_for_timeout(500)
            page.fill('input[name="monthly_limit_usd"]', "10.00")
            page.wait_for_timeout(500)
            page.click('button[type="submit"]')
            page.wait_for_timeout(3000)
            page.evaluate("window.scrollTo(0, 300)")
            page.wait_for_timeout(2000)
        else:
            print("  [WARN] Budget form not found — may have redirected to login")
            page.wait_for_timeout(3000)

        page.close()
        ctx.close()
        browser.close()

    _rename_latest("clip_f_budget.webm")
    print("  -> clip_f_budget.webm")


def record_clip_g_negotiations(session_cookie: str):
    """Clip G: Negotiation History."""
    print("\n=== Recording Clip G: Negotiation History ===")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            record_video_dir=str(OUTPUT_DIR),
            record_video_size={"width": 1280, "height": 720},
            viewport={"width": 1280, "height": 720},
        )
        ctx.add_cookies([{
            "name": "acf_portal_session",
            "value": session_cookie,
            "domain": "localhost",
            "path": "/",
        }])
        page = ctx.new_page()

        page.goto("http://localhost:8092/portal/negotiations")
        page.wait_for_timeout(3000)

        # Try to click first negotiation row to expand
        rows = page.query_selector_all(".neg-toggle")
        if rows:
            rows[0].click()
            page.wait_for_timeout(2000)

        page.close()
        ctx.close()
        browser.close()

    _rename_latest("clip_g_negotiations.webm")
    print("  -> clip_g_negotiations.webm")


def record_clip_h_reputation(session_cookie: str):
    """Clip H: Reputation & Leaderboard."""
    print("\n=== Recording Clip H: Reputation & Leaderboard ===")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            record_video_dir=str(OUTPUT_DIR),
            record_video_size={"width": 1280, "height": 720},
            viewport={"width": 1280, "height": 720},
        )
        ctx.add_cookies([{
            "name": "acf_portal_session",
            "value": session_cookie,
            "domain": "localhost",
            "path": "/",
        }])
        page = ctx.new_page()

        # Portal reputation
        page.goto("http://localhost:8092/portal/reputation")
        page.wait_for_timeout(3000)

        # Scroll to discount section
        page.evaluate("window.scrollTo(0, 400)")
        page.wait_for_timeout(2000)

        # Scroll to leaderboard
        page.evaluate("window.scrollTo(0, 800)")
        page.wait_for_timeout(3000)

        # Switch to public leaderboard
        page.goto(f"{BASE_URL}/reputation")
        page.wait_for_timeout(3000)

        # Scroll to leaderboard table
        page.evaluate("window.scrollTo(0, 500)")
        page.wait_for_timeout(3000)

        page.close()
        ctx.close()
        browser.close()

    _rename_latest("clip_h_reputation.webm")
    print("  -> clip_h_reputation.webm")


def record_terminal_clips():
    """Record terminal demos using script + ffmpeg (Clip A, C)."""
    print("\n=== Recording Clip C: Agent-to-Agent Trading (terminal) ===")
    script_log = OUTPUT_DIR / "clip_c_terminal.log"

    # Use `script` to capture terminal output, then we'll provide the log
    result = subprocess.run(
        ["bash", "-c", f"""
        cd .
        source .venv/bin/activate
        echo "\\033[1;36m=== Agent-to-Agent Trading Demo ===\\033[0m"
        echo ""
        python examples/two_agents_trading.py 2>&1
        """],
        capture_output=True, text=True, timeout=60,
    )
    script_log.write_text(result.stdout)
    print(f"  -> clip_c_terminal.log ({len(result.stdout)} bytes)")
    return result.stdout


def _rename_latest(target_name: str):
    """Rename the latest .webm file in OUTPUT_DIR."""
    webms = sorted(OUTPUT_DIR.glob("*.webm"), key=lambda f: f.stat().st_mtime)
    if webms:
        latest = webms[-1]
        target = OUTPUT_DIR / target_name
        if target.exists():
            target.unlink()
        latest.rename(target)


if __name__ == "__main__":
    import sys

    print("AgenticTrade Demo Clip Recorder")
    print(f"Output: {OUTPUT_DIR}")
    print("=" * 50)

    # Get session cookie for portal pages
    # Generate a fresh session
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from marketplace.provider_auth import sign_session
    session_cookie = sign_session("c80afc18-d62d-4e26-874f-d75025284220")
    print(f"Session cookie generated for JudyaiLab")

    # Record all clips
    record_clip_b_discovery()
    record_clip_d_nano_dashboard()
    record_clip_f_budget(session_cookie)
    record_clip_g_negotiations(session_cookie)
    record_clip_h_reputation(session_cookie)
    terminal_output = record_terminal_clips()

    print("\n" + "=" * 50)
    print("All clips recorded!")
    print(f"Files in: {OUTPUT_DIR}")
    for f in sorted(OUTPUT_DIR.iterdir()):
        size = f.stat().st_size
        print(f"  {f.name} ({size:,} bytes)")
