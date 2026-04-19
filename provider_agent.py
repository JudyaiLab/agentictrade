#!/usr/bin/env python3
"""AgenticTrade Provider Agent — run AI services locally, API key never leaves your machine.

Quick start:  pip install anthropic && python provider_agent.py --setup
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, urllib.request, urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

DEFAULT_PORT = 8080
MAX_INPUT = 10_000
_client = None

# ── .env loader (stdlib only) ────────────────────────────────────────────
def load_dotenv(p=None):
    p = Path(p) if p else Path.cwd() / ".env"
    loaded = {}
    if not p.is_file():
        return loaded
    for ln in p.read_text("utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln[0] == "#":
            continue
        m = re.match(r"^([A-Za-z_]\w*)=(.*)$", ln)
        if not m:
            continue
        k, v = m[1], m[2].strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        loaded[k] = v
        os.environ.setdefault(k, v)
    return loaded

_env = load_dotenv()

def _key():
    return os.environ.get("ANTHROPIC_API_KEY", "")

def _mask(k):
    return f"{k[:10]}...{k[-4:]}" if len(k) > 16 else "****"

def _ensure():
    global _client
    if _client:
        return _client
    try:
        import anthropic
    except ImportError:
        print("\n  [ERROR] pip install anthropic\n"); sys.exit(1)
    k = _key()
    if not k:
        print("\n  [ERROR] No API key. Run: python provider_agent.py --setup\n"); sys.exit(1)
    _client = anthropic.Anthropic(api_key=k)
    return _client

def _log(msg):
    print(f"  {time.strftime('%H:%M:%S')}  {msg}")

# ── Setup wizard ─────────────────────────────────────────────────────────
def cmd_setup():
    print(f"\n{'=' * 52}\n  AgenticTrade Provider Agent — Setup\n{'=' * 52}\n")
    env_path = Path.cwd() / ".env"
    existing = ""
    if env_path.is_file():
        for ln in env_path.read_text("utf-8").splitlines():
            m = re.match(r"^ANTHROPIC_API_KEY=(.+)$", ln.strip())
            if m:
                existing = m[1].strip().strip("'\""); break
    if existing:
        print(f"  Existing key: {_mask(existing)}")
        if input("  Overwrite? [y/N] ").strip().lower() not in ("y", "yes"):
            _next_steps(); return
    print("  Get your key: https://console.anthropic.com/settings/keys\n")
    k = input("  Paste API key: ").strip()
    if not k:
        print("  Aborted."); sys.exit(1)
    if not k.startswith("sk-ant-"):
        print("  [FAIL] Key should start with sk-ant-"); sys.exit(1)
    print("  Validating with Anthropic API...")
    try:
        import anthropic
        anthropic.Anthropic(api_key=k).messages.create(
            model="claude-haiku-4-5", max_tokens=16, timeout=15.0,
            messages=[{"role": "user", "content": "ping"}])
        print("  [OK] Key is valid.")
    except ImportError:
        print("  [ERROR] pip install anthropic"); sys.exit(1)
    except Exception as e:
        n = type(e).__name__
        if "Authentication" in n:
            print("  [FAIL] Key invalid or revoked."); sys.exit(1)
        elif "BadRequest" in n and any(w in str(e).lower() for w in ("credit", "billing")):
            print("  [FAIL] Account has no credits."); sys.exit(1)
        else:
            print(f"  [FAIL] {e}"); sys.exit(1)
    # Write .env
    lines, replaced = [], False
    if env_path.is_file():
        for ln in env_path.read_text("utf-8").splitlines():
            if ln.strip().startswith("ANTHROPIC_API_KEY="):
                lines.append(f"ANTHROPIC_API_KEY={k}"); replaced = True
            else:
                lines.append(ln)
    if not replaced:
        lines.append(f"ANTHROPIC_API_KEY={k}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Saved to {env_path}")
    _next_steps()

def _next_steps():
    print(f"""
  Next steps:
  1. Start:   python provider_agent.py
  2. Tunnel:  ngrok http {DEFAULT_PORT}
  3. Paste tunnel URL into AgenticTrade portal
  4. Verify:  python provider_agent.py --test
""")

# ── Test mode ────────────────────────────────────────────────────────────
def cmd_test(port):
    url = f"http://127.0.0.1:{port}"
    srv = None
    try:
        urllib.request.urlopen(urllib.request.Request(url), timeout=2)
    except Exception:
        if not _key():
            print("  [ERROR] No API key. Run --setup first."); sys.exit(1)
        print(f"  Starting temporary agent on port {port}...")
        srv = HTTPServer(("127.0.0.1", port), Handler)
        Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.3)
    try:
        _run_tests(url)
    finally:
        if srv:
            srv.shutdown()

def _run_tests(url):
    print(f"\n  Testing {url}\n  {'-' * 44}")
    # 1) Health
    print("  1/3  Health check ...", end=" ", flush=True)
    try:
        r = json.loads(urllib.request.urlopen(url, timeout=5).read())
        print("PASS" if r.get("status") == "ok" else f"WARN {r}")
    except Exception as e:
        print(f"FAIL {e}"); return
    # 2) Prompt
    print("  2/3  Prompt execution ...", end=" ", flush=True)
    body = json.dumps({"config": {"model": "claude-haiku-4-5",
        "system_prompt": "Reply with exactly: pong", "temperature": 0,
        "max_tokens": 16}, "input": "ping"}).encode()
    try:
        req = urllib.request.Request(url, data=body,
            headers={"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        u = r.get("usage", {})
        print(f"PASS  \"{r.get('result','')}\"  "
              f"{u.get('input_tokens',0)}+{u.get('output_tokens',0)} tok  "
              f"{r.get('latency_ms',0)}ms")
    except urllib.error.HTTPError as e:
        print(f"FAIL HTTP {e.code}: {e.read().decode()[:120]}"); return
    except Exception as e:
        print(f"FAIL {e}"); return
    # 3) Error handling
    print("  3/3  Error handling ...", end=" ", flush=True)
    bad = json.dumps({"config": {}, "input": ""}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=bad,
            headers={"Content-Type": "application/json"}), timeout=5)
        print("WARN expected 400")
    except urllib.error.HTTPError as e:
        print("PASS" if e.code == 400 else f"WARN got {e.code}")
    except Exception as e:
        print(f"FAIL {e}"); return
    print(f"\n  All tests passed. Agent is ready.\n")

# ── HTTP handler ─────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            if not n:
                return self._err(400, "Empty body")
            d = json.loads(self.rfile.read(n))
            cfg, inp = d.get("config", {}), d.get("input", "")
            if not inp or not inp.strip():
                return self._err(400, "Missing 'input'")
            if len(inp) > MAX_INPUT:
                return self._err(400, f"Input > {MAX_INPUT} chars")
            model = cfg.get("model", "claude-haiku-4-5")
            sp = cfg.get("system_prompt", "")
            if not sp:
                return self._err(400, "Missing system_prompt")
            temp = float(cfg.get("temperature", 0.7))
            mt = int(cfg.get("max_tokens", 1024))
            c = _ensure()
            t0 = time.monotonic()
            r = c.messages.create(model=model, max_tokens=mt, system=sp,
                temperature=temp, timeout=30.0,
                messages=[{"role": "user", "content": inp}])
            ms = int((time.monotonic() - t0) * 1000)
            txt = "".join(b.text for b in r.content if b.type == "text")
            self._ok(200, {"result": txt, "model": model,
                "usage": {"input_tokens": r.usage.input_tokens,
                           "output_tokens": r.usage.output_tokens},
                "latency_ms": ms})
            _log(f"OK  {model}  in={r.usage.input_tokens} out={r.usage.output_tokens} {ms}ms")
        except json.JSONDecodeError:
            self._err(400, "Invalid JSON")
        except Exception as e:
            self._api_err(e)

    def do_GET(self):
        self._ok(200, {"status": "ok", "agent": "agentictrade-provider",
                        "key_configured": bool(_key())})

    def _ok(self, code, data):
        b = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _err(self, code, msg):
        _log(f"ERR {code}: {msg}")
        self._ok(code, {"error": msg})

    def _api_err(self, e):
        try:
            import anthropic as A
        except ImportError:
            return self._err(500, "Internal error")
        if isinstance(e, A.AuthenticationError):
            self._err(401, "API key invalid")
        elif isinstance(e, A.RateLimitError):
            self._err(429, "Rate limited — retry later")
        elif isinstance(e, A.BadRequestError):
            s = str(e)
            if any(w in s.lower() for w in ("credit", "billing")):
                self._err(402, "Credits exhausted")
            else:
                self._err(400, f"API error: {s[:200]}")
        else:
            _log(f"ERR {e}")
            self._err(500, "Internal error")

    def log_message(self, *a):
        pass

# ── Main ─────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="AgenticTrade Provider Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  python provider_agent.py --setup       Set up API key\n"
               "  python provider_agent.py               Start the agent\n"
               "  python provider_agent.py --test        Verify everything works\n"
               "  python provider_agent.py --port 9090   Custom port\n")
    ap.add_argument("--setup", action="store_true", help="Interactive setup wizard")
    ap.add_argument("--test", action="store_true", help="Self-test to verify agent works")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    if a.setup:
        return cmd_setup()
    if a.test:
        return cmd_test(a.port)
    k = _key()
    if not k:
        print(f"\n  [ERROR] No API key.\n\n"
              f"  Quick fix:   python provider_agent.py --setup\n"
              f"  Or manually: export ANTHROPIC_API_KEY=sk-ant-...\n")
        sys.exit(1)
    if not k.startswith("sk-ant-"):
        print("  [WARN] Key doesn't start with sk-ant-")
    srv = HTTPServer((a.host, a.port), Handler)
    print(f"\n{'=' * 52}\n  AgenticTrade Provider Agent\n{'=' * 52}")
    print(f"  Status:   Running")
    print(f"  Listen:   http://{a.host}:{a.port}")
    print(f"  API key:  {_mask(k)}")
    print(f"  .env:     {'loaded' if _env else 'not found (using env vars)'}")
    print(f"{'-' * 52}")
    print(f"  Tunnel:   ngrok http {a.port}")
    print(f"  Verify:   python provider_agent.py --test")
    print(f"  Stop:     Ctrl+C\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down..."); srv.shutdown()

if __name__ == "__main__":
    main()
