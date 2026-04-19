"""
Payment Proxy — Routes requests through marketplace with automatic payment.
Core innovation: buyers don't need x402 SDK, marketplace handles payment.
"""
from __future__ import annotations

import time
import uuid
import logging
from datetime import datetime, timezone

MAX_TIMEOUT = 300  # Maximum allowed timeout in seconds
IDEMPOTENCY_TTL_HOURS = 24  # Idempotency key expiry window (like Stripe)
from decimal import Decimal
from typing import Optional

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx

from .db import Database
from .models import UsageRecord
from .payment import extract_payment_tx

logger = logging.getLogger("proxy")

# Internal hosts allowed to bypass SSRF protection (platform-owned services)
_INTERNAL_ALLOWED = set(
    h.strip() for h in os.environ.get("ACF_INTERNAL_HOSTS", "").split(",")
) - {""}
# Always trust localhost for platform-hosted service gateway rewrites
_INTERNAL_ALLOWED.add("127.0.0.1")


class ProxyError(Exception):
    """Payment proxy errors."""


class _CircuitBreaker:
    """Simple per-provider circuit breaker (closed → open → half-open).

    When consecutive failures exceed *threshold*, the circuit opens for
    *recovery_seconds*.  After that period one probe request is allowed
    (half-open).  If the probe succeeds the circuit closes; otherwise
    it reopens.
    """

    def __init__(self, threshold: int = 5, recovery_seconds: int = 60):
        self._threshold = threshold
        self._recovery = recovery_seconds
        # provider_id → {"failures": int, "opened_at": float | None}
        self._state: dict[str, dict] = {}

    def allow(self, provider_id: str) -> bool:
        s = self._state.get(provider_id)
        if s is None or s["opened_at"] is None:
            return True
        elapsed = time.monotonic() - s["opened_at"]
        if elapsed >= self._recovery:
            return True  # half-open: allow one probe
        return False

    def record_success(self, provider_id: str) -> None:
        self._state[provider_id] = {"failures": 0, "opened_at": None}

    def record_failure(self, provider_id: str) -> None:
        s = self._state.setdefault(
            provider_id, {"failures": 0, "opened_at": None},
        )
        s["failures"] += 1
        if s["failures"] >= self._threshold:
            s["opened_at"] = time.monotonic()


class PaymentProxy:
    """
    Proxies requests to service providers with payment handling.

    Flow:
    1. Buyer sends request to marketplace proxy
    2. Proxy looks up service pricing
    3. Proxy selects payment provider via PaymentRouter (if available)
    4. Proxy forwards request to provider
    5. Proxy records usage + payment
    6. Returns provider response to buyer
    """

    def __init__(
        self,
        db: Database,
        platform_fee_pct: Decimal = Decimal("0.10"),
        timeout_seconds: int = 30,
        payment_router: object | None = None,
        webhook_manager: object | None = None,
        commission_engine: object | None = None,
    ):
        self.db = db
        self.platform_fee_pct = platform_fee_pct
        self.timeout = min(timeout_seconds, MAX_TIMEOUT)
        self._payment_router = payment_router
        self._webhook_manager = webhook_manager
        self._commission_engine = commission_engine
        self._circuit_breaker = _CircuitBreaker()

    def _claim_free_tier_sync(
        self, service_id: str, buyer_id: str, free_calls: int,
        record_id: str, provider_id: str,
    ) -> bool:
        """Atomically check free tier eligibility AND insert a placeholder
        usage record to prevent TOCTOU race conditions.

        If eligible, a usage record with amount_usd=0 is inserted inside
        the exclusive transaction so the slot is immediately claimed.
        The caller must later update the record with actual response data
        via ``update_usage_record``.
        """
        with self.db.connect() as conn:
            conn.execute("BEGIN EXCLUSIVE")
            try:
                row = conn.execute(
                    """SELECT COUNT(*) as cnt FROM usage_records
                       WHERE service_id = ? AND buyer_id = ?""",
                    (service_id, buyer_id),
                ).fetchone()
                current_count = row["cnt"] if row else 0
                if current_count < free_calls:
                    now = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        """INSERT INTO usage_records
                           (id, buyer_id, service_id, provider_id, timestamp,
                            latency_ms, status_code, amount_usd, payment_method,
                            payment_tx, request_id)
                           VALUES (?, ?, ?, ?, ?, 0, 0, 0, 'free_tier', NULL, NULL)""",
                        (record_id, buyer_id, service_id, provider_id, now),
                    )
                    conn.execute("COMMIT")
                    return True
                conn.execute("COMMIT")
                return False
            except Exception:
                conn.execute("ROLLBACK")
                raise

    async def forward_request(
        self,
        service: dict,
        buyer_id: str,
        method: str,
        path: str,
        headers: dict | None = None,
        body: bytes | None = None,
        query_params: dict | None = None,
        request_headers: dict | None = None,
        x402_paid: bool = False,
        request_id: str | None = None,
    ) -> ProxyResult:
        """
        Forward a request to a service provider.

        Returns ProxyResult with response data and billing info.
        """
        if service.get("status") != "active":
            raise ProxyError("Service is not active")

        # Idempotency: if request_id already exists within TTL window,
        # return cached result to prevent duplicate billing on retries.
        # Keys expire after IDEMPOTENCY_TTL_HOURS (default 24h, like Stripe).
        if request_id:
            existing = self.db.get_usage_by_request_id(
                request_id, ttl_hours=IDEMPOTENCY_TTL_HOURS,
            )
            if existing:
                cached_amount = Decimal(str(existing.get("amount_usd", 0)))
                return ProxyResult(
                    status_code=existing.get("status_code", 200),
                    body=b'{"status":"already_processed","request_id":"' + request_id.encode() + b'"}',
                    headers={"Content-Type": "application/json", "X-Idempotent": "true"},
                    latency_ms=existing.get("latency_ms", 0),
                    billing=BillingInfo(
                        amount=cached_amount,
                        platform_fee=Decimal("0"),
                        provider_amount=cached_amount,
                        usage_id=existing.get("id", ""),
                        free_tier=cached_amount == 0,
                    ),
                    error=None,
                )

        endpoint = service["endpoint"].rstrip("/")
        target_url = f"{endpoint}{path}" if path else endpoint

        # Rewrite platform-hosted service URLs to internal gateway
        # (avoids loopback through nginx when proxy calls our own /ext/ routes)
        _PLATFORM_HOST = os.environ.get("ACF_PLATFORM_HOST", "https://agentictrade.io")
        if target_url.startswith(f"{_PLATFORM_HOST}/ext/"):
            target_url = target_url.replace(
                _PLATFORM_HOST, "http://127.0.0.1:8092", 1
            )

        # Runtime SSRF protection — resolve hostname and block private IPs
        # (skip for prompt services — they call platform's Claude API, no external HTTP)
        if service.get("service_type") != "prompt":
            try:
                parsed = urlparse(target_url)
                if parsed.hostname and parsed.hostname not in _INTERNAL_ALLOWED:
                    try:
                        addrs = socket.getaddrinfo(parsed.hostname, None)
                    except socket.gaierror:
                        # DNS resolution failed — will be caught by httpx later
                        addrs = []
                    for addr_info in addrs:
                        try:
                            resolved_ip = ipaddress.ip_address(addr_info[4][0])
                            if (
                                resolved_ip.is_private
                                or resolved_ip.is_loopback
                                or resolved_ip.is_link_local
                                or resolved_ip.is_reserved
                            ):
                                raise ProxyError(
                                    "Service endpoint resolves to a private address"
                                )
                        except ValueError:
                            pass  # Non-IP address format, let httpx handle
            except ProxyError:
                raise

        price = Decimal(str(service.get("price_per_call", 0)))
        provider_id = service["provider_id"]
        service_id = service["id"]

        # Generate record_id early so it can be used as order_id for payments
        record_id = str(uuid.uuid4())

        # Check free tier with atomic claim to prevent TOCTOU race.
        # The claim inserts a placeholder usage record inside an exclusive
        # transaction so concurrent requests cannot exceed the free tier.
        free_calls = service.get("free_tier_calls", 0)
        is_free_tier = False
        free_tier_claimed = False
        if free_calls > 0:
            is_free_tier = await self.db.arun(
                self._claim_free_tier_sync,
                service_id, buyer_id, free_calls,
                record_id, provider_id,
            )
            if is_free_tier:
                price = Decimal("0")
                free_tier_claimed = True  # usage record already inserted

        # For paid calls: handle payment based on method
        payment_id = None
        payment_method = service.get("payment_method", "x402")
        if price > 0:
            if x402_paid:
                # x402 middleware already verified crypto payment — skip balance deduction
                # Require valid transaction hash as proof of payment
                tx_hash = extract_payment_tx(dict(headers or {}))
                if not tx_hash or len(tx_hash) < 10:
                    # No valid tx hash — fall back to balance deduction
                    logger.warning(
                        "x402_paid=True but no valid tx hash for service %s, falling back to balance",
                        service_id,
                    )
                    if not await self.db.arun(self.db.deduct_balance, buyer_id, price):
                        raise ProxyError(
                            f"Insufficient balance. Required: ${price} USDC. "
                            f"Deposit funds via POST /api/v1/deposits"
                        )
                    payment_id = f"balance:{record_id}"
                else:
                    payment_id = tx_hash
                    payment_method = "x402"
                    logger.info(
                        "x402 payment verified: $%s from %s for service %s (tx: %s)",
                        price, buyer_id, service_id, tx_hash[:16],
                    )
            else:
                # Deduct from buyer's pre-paid balance
                if not await self.db.arun(self.db.deduct_balance, buyer_id, price):
                    raise ProxyError(
                        f"Insufficient balance. Required: ${price} USDC. "
                        f"Deposit funds via POST /api/v1/deposits"
                    )
                payment_id = f"balance:{record_id}"
                logger.info(
                    "Balance deducted: $%s from %s for service %s",
                    price, buyer_id, service_id,
                )

        # Circuit breaker: fail fast if provider is down
        # (skip for prompt services — they use platform's Claude API, not provider infra)
        is_prompt_service = service.get("service_type") == "prompt"
        if not is_prompt_service and not self._circuit_breaker.allow(provider_id):
            raise ProxyError(
                f"Provider {provider_id} circuit open — too many recent failures. "
                f"Retrying shortly."
            )

        start_time = time.monotonic()
        status_code = 0
        response_body = b""
        response_headers = {}
        error_msg = None

        if is_prompt_service:
            # ── Prompt-as-API: route to Provider Agent ──
            import json as _json

            service_endpoint = service.get("endpoint", "")

            if service_endpoint.startswith("prompt://pending"):
                # Provider hasn't set up their agent yet
                status_code = 503
                error_msg = "Service unavailable: provider agent not connected yet"
                response_body = _json.dumps({"error": error_msg}).encode()
                response_headers = {"Content-Type": "application/json"}
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                return status_code, response_body, response_headers, error_msg, elapsed_ms

            if service_endpoint.startswith(("http://", "https://")):
                # Forward to Provider Agent — API key stays on provider's machine
                try:
                    prompt_cfg_raw = service.get("prompt_config", {})
                    if isinstance(prompt_cfg_raw, str):
                        prompt_cfg_raw = _json.loads(prompt_cfg_raw)

                    # Extract user input from request body
                    user_input = ""
                    if body:
                        try:
                            body_json = _json.loads(body)
                            user_input = body_json.get("input", "")
                        except (ValueError, TypeError):
                            user_input = body.decode("utf-8", errors="replace")

                    if not user_input:
                        status_code = 400
                        error_msg = "Missing 'input' field in request body"
                        response_body = _json.dumps(
                            {"error": error_msg, "usage": "POST with {\"input\": \"your text\"}"}
                        ).encode()
                        response_headers = {"Content-Type": "application/json"}
                    else:
                        # Forward prompt_config + input to provider's agent
                        agent_payload = _json.dumps({
                            "config": {
                                "model": prompt_cfg_raw.get("model", "claude-haiku-4-5"),
                                "system_prompt": prompt_cfg_raw.get("system_prompt", ""),
                                "temperature": prompt_cfg_raw.get("temperature", 0.7),
                                "max_tokens": prompt_cfg_raw.get("max_tokens", 1024),
                            },
                            "input": user_input,
                        }).encode()
                        agent_headers = {
                            "Content-Type": "application/json",
                            "User-Agent": "AgenticTrade-Platform/1.0",
                        }

                        timeout_override = service.get("timeout_override")
                        effective_timeout = min(int(timeout_override), MAX_TIMEOUT) if timeout_override else self.timeout

                        async with httpx.AsyncClient(timeout=effective_timeout) as agent_client:
                            resp = await agent_client.post(
                                service_endpoint,
                                content=agent_payload,
                                headers=agent_headers,
                            )
                        status_code = resp.status_code
                        response_body = resp.content
                        response_headers = dict(resp.headers)
                        if status_code >= 400:
                            try:
                                error_msg = _json.loads(resp.content).get("error", resp.text)
                            except Exception:
                                error_msg = resp.text[:200]

                except httpx.TimeoutException:
                    status_code = 504
                    error_msg = "Provider agent timed out"
                    response_body = _json.dumps({"error": error_msg}).encode()
                    response_headers = {"Content-Type": "application/json"}
                except httpx.ConnectError:
                    status_code = 502
                    error_msg = "Cannot reach provider agent"
                    response_body = _json.dumps({"error": error_msg}).encode()
                    response_headers = {"Content-Type": "application/json"}
                except Exception as e:
                    status_code = 500
                    error_msg = "Prompt routing error"
                    logger.error("Provider agent error: %s", e)
                    response_body = _json.dumps({"error": "Internal error"}).encode()
                    response_headers = {"Content-Type": "application/json"}
            else:
                # Fallback: invalid endpoint
                status_code = 503
                error_msg = "Service not properly configured"
                response_body = _json.dumps({"error": error_msg}).encode()
                response_headers = {"Content-Type": "application/json"}
        else:
            # ── Standard HTTP forward to provider endpoint ──
            # Determine effective timeout: per-service override > platform default
            timeout_override = service.get("timeout_override")
            if timeout_override is not None:
                effective_timeout = min(int(timeout_override), MAX_TIMEOUT)
            else:
                effective_timeout = self.timeout

            try:
                async with httpx.AsyncClient(timeout=effective_timeout) as client:
                    # Build request
                    req_headers = dict(headers or {})
                    # Remove hop-by-hop headers
                    for h in ("host", "connection", "transfer-encoding",
                              "authorization", "cookie", "x-api-key"):
                        req_headers.pop(h, None)

                    response = await client.request(
                        method=method.upper(),
                        url=target_url,
                        headers=req_headers,
                        content=body,
                        params=query_params,
                    )

                    status_code = response.status_code
                    response_body = response.content
                    response_headers = dict(response.headers)

            except httpx.TimeoutException:
                status_code = 504
                error_msg = "Provider timeout"
                self._circuit_breaker.record_failure(provider_id)
            except httpx.ConnectError:
                status_code = 502
                error_msg = "Provider unreachable"
                self._circuit_breaker.record_failure(provider_id)
            except Exception as e:
                status_code = 500
                error_msg = "Proxy error: an internal error occurred"
                logger.error("Proxy forward error: %s", e)
                self._circuit_breaker.record_failure(provider_id)

        # Record circuit breaker success on non-5xx responses
        if status_code < 500 and error_msg is None:
            self._circuit_breaker.record_success(provider_id)

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        # Snapshot commission rate at transaction time (ASC 606 compliance).
        # This ensures the rate used for settlement matches the rate at the
        # time the service was called, even if the provider's tier changes later.
        commission_rate = None
        if self._commission_engine is not None and price > 0:
            try:
                commission_rate = str(
                    self._commission_engine.get_effective_rate(
                        provider_id, transaction_amount=price,
                    )
                )
            except Exception:
                commission_rate = str(self.platform_fee_pct)

        # Record usage: if free tier was atomically claimed, update the
        # placeholder; otherwise insert a new record.
        now = datetime.now(timezone.utc).isoformat()

        if free_tier_claimed:
            # Update the placeholder record inserted by _claim_free_tier_sync
            updates = {
                "latency_ms": elapsed_ms,
                "status_code": status_code,
                "amount_usd": float(price) if status_code < 500 else 0,
                "payment_method": payment_method,
                "payment_tx": payment_id or extract_payment_tx(request_headers or {}),
                "request_id": request_id,
            }
            if commission_rate is not None:
                updates["commission_rate"] = commission_rate
            await self.db.arun(self.db.update_usage_record, record_id, updates)
        else:
            usage_record = {
                "id": record_id,
                "buyer_id": buyer_id,
                "service_id": service_id,
                "provider_id": provider_id,
                "timestamp": now,
                "latency_ms": elapsed_ms,
                "status_code": status_code,
                "amount_usd": float(price) if status_code < 500 else 0,
                "payment_method": payment_method,
                "payment_tx": payment_id or extract_payment_tx(request_headers or {}),
                "request_id": request_id,
                "commission_rate": commission_rate,
            }
            await self.db.arun(self.db.insert_usage, usage_record)

        # Dispatch webhook event (fire-and-forget with error logging)
        if self._webhook_manager is not None and status_code < 500:
            try:
                def _on_webhook_done(task):
                    if task.exception():
                        logger.warning(
                            "Webhook dispatch error: %s", task.exception()
                        )

                wh_task = asyncio.ensure_future(
                    self._webhook_manager.dispatch("service.called", {
                        "usage_id": record_id,
                        "service_id": service_id,
                        "buyer_id": buyer_id,
                        "provider_id": provider_id,
                        "amount_usd": float(price),
                        "payment_method": payment_method,
                        "status_code": status_code,
                        "latency_ms": elapsed_ms,
                    })
                )
                wh_task.add_done_callback(_on_webhook_done)
            except Exception as e:
                logger.warning("Webhook dispatch failed: %s", e)

        # After recording usage, check velocity and flag if severely exceeded
        velocity_flagged = False
        if price > 0:
            from .velocity import check_transaction_velocity, should_block_transaction
            alerts = check_transaction_velocity(
                self.db, buyer_id=buyer_id, provider_id=provider_id
            )
            if alerts and should_block_transaction(alerts):
                logger.warning(
                    "Transaction held due to velocity alert for buyer %s", buyer_id
                )
                velocity_flagged = True

        # Calculate splits
        platform_fee = price * self.platform_fee_pct
        provider_amount = price - platform_fee

        return ProxyResult(
            status_code=status_code,
            body=response_body,
            headers=response_headers,
            latency_ms=elapsed_ms,
            billing=BillingInfo(
                amount=price,
                platform_fee=platform_fee,
                provider_amount=provider_amount,
                usage_id=record_id,
                free_tier=is_free_tier,
            ),
            error=error_msg,
            velocity_flagged=velocity_flagged,
        )


    async def forward_probe(
        self,
        service: dict,
        buyer_id: str,
        method: str,
        path: str,
        headers: dict | None = None,
        body: bytes | None = None,
        query_params: dict | None = None,
    ) -> dict:
        """Forward a zero-cost capability probe to test service compatibility.

        Probes are real HTTP requests to the service endpoint but:
        - No payment is deducted (zero cost)
        - No usage record is created
        - Response body is truncated to a preview (max 512 bytes)
        - Returns a standardized compatibility report

        This allows agents to verify a service meets their needs before
        committing to paid calls — solving the semantic matching gap where
        schemas look compatible but actual outputs may differ.
        """
        if service.get("status") != "active":
            raise ProxyError("Service is not active")

        endpoint = service["endpoint"].rstrip("/")
        target_url = f"{endpoint}{path}" if path else endpoint
        provider_id = service["provider_id"]

        # Circuit breaker still applies to probes
        if service.get("service_type") != "prompt":
            if not self._circuit_breaker.allow(provider_id):
                raise ProxyError(
                    f"Provider {provider_id} circuit open — try again shortly."
                )

        start_time = time.monotonic()
        status_code = 0
        response_body = b""
        error_msg = None

        timeout_override = service.get("timeout_override")
        effective_timeout = (
            min(int(timeout_override), MAX_TIMEOUT)
            if timeout_override else min(self.timeout, 10)  # Probes capped at 10s
        )

        try:
            async with httpx.AsyncClient(timeout=effective_timeout) as client:
                req_headers = dict(headers or {})
                for h in ("host", "connection", "transfer-encoding",
                          "authorization", "cookie", "x-api-key", "x-probe"):
                    req_headers.pop(h, None)

                response = await client.request(
                    method=method.upper(),
                    url=target_url,
                    headers=req_headers,
                    content=body,
                    params=query_params,
                )
                status_code = response.status_code
                response_body = response.content

        except httpx.TimeoutException:
            status_code = 504
            error_msg = "Provider timeout during probe"
            self._circuit_breaker.record_failure(provider_id)
        except httpx.ConnectError:
            status_code = 502
            error_msg = "Provider unreachable during probe"
            self._circuit_breaker.record_failure(provider_id)
        except Exception as e:
            status_code = 500
            error_msg = "Probe error"
            logger.error("Probe forward error: %s", e)

        if status_code < 500 and error_msg is None:
            self._circuit_breaker.record_success(provider_id)

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        # Truncate response for preview (max 512 bytes)
        preview = response_body[:512].decode("utf-8", errors="replace")

        return {
            "compatible": status_code < 400,
            "latency_ms": elapsed_ms,
            "status_code": status_code,
            "response_preview": preview if not error_msg else error_msg,
        }


class ProxyResult:
    """Result of a proxied request."""

    __slots__ = ("status_code", "body", "headers", "latency_ms", "billing", "error", "velocity_flagged")

    def __init__(
        self,
        status_code: int,
        body: bytes,
        headers: dict,
        latency_ms: int,
        billing: "BillingInfo",
        error: Optional[str],
        velocity_flagged: bool = False,
    ):
        self.status_code = status_code
        self.body = body
        self.headers = headers
        self.latency_ms = latency_ms
        self.billing = billing
        self.error = error
        self.velocity_flagged = velocity_flagged

    @property
    def success(self) -> bool:
        return self.status_code < 400


class BillingInfo:
    """Billing information for a proxied request."""

    __slots__ = ("amount", "platform_fee", "provider_amount", "usage_id", "free_tier")

    def __init__(
        self,
        amount: Decimal,
        platform_fee: Decimal,
        provider_amount: Decimal,
        usage_id: str,
        free_tier: bool = False,
    ):
        self.amount = amount
        self.platform_fee = platform_fee
        self.provider_amount = provider_amount
        self.usage_id = usage_id
        self.free_tier = free_tier
