"""
Agent Commerce Framework — Webhook Listener Example

A simple webhook receiver that validates HMAC signatures
and processes marketplace events.

Run: WEBHOOK_SECRET=your-secret python examples/webhook_listener.py
Then subscribe: curl -X POST http://localhost:8000/api/v1/webhooks \
  -H "Authorization: Bearer key_id:secret" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-server/webhook", "events": ["service.called"], "secret": "your-secret"}'
"""
import hashlib
import hmac
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
if not WEBHOOK_SECRET:
    print("ERROR: WEBHOOK_SECRET environment variable is required.")
    print("Set it to match the secret used when creating your webhook subscription.")
    sys.exit(1)
PORT = int(os.environ.get("WEBHOOK_PORT", "9000"))


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Verify HMAC signature
        signature = self.headers.get("X-ACF-Signature", "")
        expected = hmac.new(
            WEBHOOK_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            print(f"[WARN] Invalid signature! Got: {signature[:16]}...")
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"Invalid signature")
            return

        # Parse event
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        event = payload.get("event", "unknown")
        data = payload.get("data", {})
        timestamp = payload.get("timestamp", "")

        print(f"\n[EVENT] {event} at {timestamp}")
        print(f"  Data: {json.dumps(data, indent=2)}")

        # Handle specific events
        if event == "service.called":
            print(f"  Service {data.get('service_id')} called by {data.get('buyer_id')}")
            print(f"  Amount: ${data.get('amount_usd', 0)}")
            print(f"  Latency: {data.get('latency_ms', 0)}ms")
        elif event == "payment.completed":
            print(f"  Payment {data.get('payment_id')} completed")
        elif event == "settlement.completed":
            print(f"  Settlement for {data.get('provider_id')}: ${data.get('net_amount', 0)}")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass  # Suppress default logging


def main():
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    print(f"Webhook listener running on http://0.0.0.0:{PORT}")
    print("Waiting for events...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
