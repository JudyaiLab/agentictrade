"""
AgenticTrade Agent — register and sell services in 3 lines.

Usage:
    from sdk.agent import AgenticTradeAgent

    agent = AgenticTradeAgent(
        name="My Crypto Scanner",
        endpoint="https://my-service.com/api/scan",
        price_per_call="0.05",
    )
    agent.serve()  # Registers + starts accepting orders
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Any, Callable

import httpx

logger = logging.getLogger("agentictrade.agent")

_DEFAULT_BASE_URL = "https://agentictrade.io"
_STATE_FILE = ".agentictrade_state.json"


@dataclass(frozen=True)
class OnboardResult:
    """Result of agent onboarding."""

    agent_id: str
    owner_id: str
    api_key: str
    service_id: str
    dashboard_url: str


class AgenticTradeError(Exception):
    """AgenticTrade SDK error."""


class AgenticTradeAgent:
    """Agent-native commerce: register and sell services autonomously.

    Human configures (3 lines), agent operates (registration, listing,
    order fulfillment, earnings tracking).

    Args:
        name: Service name shown on the marketplace.
        endpoint: HTTPS URL where your service accepts requests.
        price_per_call: Price per API call in USDC (e.g., "0.05").
        description: Service description for marketplace listing.
        category: Service category (e.g., "crypto", "ai", "data").
        tags: Discovery tags.
        owner_email: Contact email (optional, not published).
        api_key: Existing API key (skip onboarding if provided).
        base_url: AgenticTrade platform URL.
    """

    def __init__(
        self,
        name: str,
        endpoint: str,
        price_per_call: str = "0.01",
        description: str = "",
        category: str = "",
        tags: list[str] | None = None,
        owner_email: str = "",
        api_key: str | None = None,
        base_url: str | None = None,
        handler: Callable[[dict], dict] | None = None,
    ) -> None:
        self.name = name
        self.endpoint = endpoint
        self.price_per_call = price_per_call
        self.description = description
        self.category = category
        self.tags = tags or []
        self.owner_email = owner_email
        self.base_url = (base_url or os.environ.get("AGENTICTRADE_URL") or _DEFAULT_BASE_URL).rstrip("/")
        self.handler = handler

        # Credentials — set after onboard or from saved state / param
        self._api_key = api_key or os.environ.get("AGENTICTRADE_API_KEY")
        self._agent_id: str | None = None
        self._owner_id: str | None = None
        self._service_id: str | None = None

        # Try to load saved state
        self._load_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def onboard(self) -> OnboardResult:
        """Register agent on AgenticTrade marketplace.

        Creates identity, API key, and service listing in one call.
        Credentials are saved to .agentictrade_state.json for reuse.

        Returns:
            OnboardResult with agent_id, api_key, service_id.

        Raises:
            AgenticTradeError: If registration fails.
        """
        if self._api_key and self._agent_id and self._service_id:
            logger.info("Already onboarded (agent_id=%s)", self._agent_id)
            return OnboardResult(
                agent_id=self._agent_id,
                owner_id=self._owner_id or "",
                api_key=self._api_key,
                service_id=self._service_id,
                dashboard_url=f"{self.base_url}/api/v1/agents/{self._agent_id}/dashboard",
            )

        payload = {
            "agent_name": self.name,
            "description": self.description,
            "endpoint": self.endpoint,
            "price_per_call": self.price_per_call,
            "category": self.category,
            "tags": self.tags,
            "owner_email": self.owner_email,
        }

        with httpx.Client(base_url=self.base_url, timeout=30.0) as client:
            resp = client.post(
                "/api/v1/agents/onboard",
                json=payload,
                headers={"Content-Type": "application/json"},
            )

        if resp.status_code >= 400:
            detail = resp.text[:500]
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            raise AgenticTradeError(f"Onboard failed ({resp.status_code}): {detail}")

        data = resp.json()
        self._agent_id = data["agent_id"]
        self._owner_id = data["owner_id"]
        self._api_key = data["api_key"]
        self._service_id = data["service_id"]

        self._save_state()

        logger.info(
            "Agent onboarded: agent_id=%s, service_id=%s",
            self._agent_id,
            self._service_id,
        )

        return OnboardResult(
            agent_id=self._agent_id,
            owner_id=self._owner_id,
            api_key=self._api_key,
            service_id=self._service_id,
            dashboard_url=data.get("dashboard_url", ""),
        )

    def dashboard(self) -> dict[str, Any]:
        """Fetch agent dashboard (earnings, usage, health).

        Returns:
            Dashboard data dict.

        Raises:
            AgenticTradeError: If not onboarded or request fails.
        """
        if not self._api_key or not self._agent_id:
            raise AgenticTradeError("Agent not onboarded. Call onboard() first.")

        with httpx.Client(base_url=self.base_url, timeout=30.0) as client:
            resp = client.get(
                f"/api/v1/agents/{self._agent_id}/dashboard",
                headers={"Authorization": f"Bearer {self._api_key}"},
            )

        if resp.status_code >= 400:
            detail = resp.text[:500]
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            raise AgenticTradeError(f"Dashboard failed ({resp.status_code}): {detail}")

        return resp.json()

    def update_pricing(self, new_price: str) -> dict[str, Any]:
        """Update service pricing on the marketplace.

        Args:
            new_price: New price per call in USDC.

        Returns:
            Updated service dict.
        """
        if not self._api_key or not self._service_id:
            raise AgenticTradeError("Agent not onboarded. Call onboard() first.")

        with httpx.Client(base_url=self.base_url, timeout=30.0) as client:
            resp = client.patch(
                f"/api/v1/services/{self._service_id}",
                json={"price_per_call": new_price},
                headers={"Authorization": f"Bearer {self._api_key}"},
            )

        if resp.status_code >= 400:
            raise AgenticTradeError(f"Update failed: {resp.text[:200]}")
        return resp.json()

    def serve(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """Register on AgenticTrade and start serving requests.

        This is the main entry point. It:
        1. Calls onboard() if not already registered
        2. Starts a local HTTP server for health checks
        3. Blocks until interrupted (Ctrl+C)

        Args:
            host: Bind address.
            port: Bind port.
        """
        result = self.onboard()

        print(f"\n{'=' * 52}")
        print(f"  AgenticTrade Agent — {self.name}")
        print(f"{'=' * 52}")
        print(f"  Agent ID:    {result.agent_id}")
        print(f"  Service ID:  {result.service_id}")
        print(f"  Endpoint:    {self.endpoint}")
        print(f"  Price:       ${self.price_per_call} USDC/call")
        print(f"  Dashboard:   {result.dashboard_url}")
        print(f"  Status:      LIVE on marketplace")
        print(f"{'=' * 52}")
        print(f"  Your agent is now accepting orders on AgenticTrade.")
        print(f"  Press Ctrl+C to stop.\n")

        if self.handler:
            # Start local handler server
            agent_ref = self

            class RequestHandler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self._respond(200, {"status": "ok", "agent": agent_ref.name})

                def do_POST(self):
                    try:
                        length = int(self.headers.get("Content-Length", 0))
                        body = json.loads(self.rfile.read(length)) if length else {}
                        result = agent_ref.handler(body)
                        self._respond(200, result)
                    except Exception as e:
                        self._respond(500, {"error": str(e)})

                def _respond(self, code, data):
                    body = json.dumps(data).encode()
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *args):
                    pass

            server = HTTPServer((host, port), RequestHandler)
            print(f"  Handler server: http://{host}:{port}")

            try:
                server.serve_forever()
            except KeyboardInterrupt:
                server.shutdown()
                print("\n  Agent stopped.")
        else:
            # No handler — just keep alive for health checks
            try:
                while True:
                    time.sleep(60)
            except KeyboardInterrupt:
                print("\n  Agent stopped.")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def agent_id(self) -> str | None:
        return self._agent_id

    @property
    def api_key(self) -> str | None:
        return self._api_key

    @property
    def service_id(self) -> str | None:
        return self._service_id

    @property
    def is_registered(self) -> bool:
        return bool(self._agent_id and self._api_key and self._service_id)

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Save credentials to local file for reuse across restarts."""
        state = {
            "agent_id": self._agent_id,
            "owner_id": self._owner_id,
            "api_key": self._api_key,
            "service_id": self._service_id,
            "base_url": self.base_url,
            "name": self.name,
        }
        try:
            Path(_STATE_FILE).write_text(json.dumps(state, indent=2))
            logger.debug("State saved to %s", _STATE_FILE)
        except Exception as e:
            logger.warning("Failed to save state: %s", e)

    def _load_state(self) -> None:
        """Load credentials from local file if available."""
        try:
            path = Path(_STATE_FILE)
            if not path.is_file():
                return
            state = json.loads(path.read_text())
            if state.get("name") != self.name:
                return  # Different agent, don't load
            self._agent_id = self._agent_id or state.get("agent_id")
            self._owner_id = self._owner_id or state.get("owner_id")
            self._api_key = self._api_key or state.get("api_key")
            self._service_id = self._service_id or state.get("service_id")
            if self._agent_id:
                logger.debug("State loaded from %s", _STATE_FILE)
        except Exception:
            pass  # State file corrupted or missing — fresh start


# ======================================================================
# Buyer Agent — discover and pay for services in one call
# ======================================================================


@dataclass(frozen=True)
class PayResult:
    """Result of a paid API call."""

    success: bool
    status_code: int
    data: Any = None
    service_id: str = ""
    amount_usdc: str = "0"
    error: str = ""


class BudgetExceeded(AgenticTradeError):
    """Raised when a payment would exceed the agent's budget limit."""


class AgenticTradeBuyer:
    """AI agent buyer — discover, pay, and consume services autonomously.

    One-line usage:
        buyer = AgenticTradeBuyer(wallet_address="0x...", budget_usdc="10.0")
        result = buyer.pay("crypto-scanner", {"symbol": "BTC"})

    Features:
    - Auto-onboard as buyer agent (no endpoint needed)
    - Discover services by name/category
    - Pay-and-call in one step via Nanopayments (x402, gas-free)
    - Built-in BudgetGuard: set max spend, auto-stops when exceeded
    - State persistence across restarts
    """

    _STATE_FILE = ".agentictrade_buyer_state.json"

    def __init__(
        self,
        wallet_address: str = "",
        budget_usdc: str = "",
        agent_name: str = "autonomous-buyer",
        owner_email: str = "",
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.wallet_address = wallet_address
        self.budget_usdc = float(budget_usdc) if budget_usdc else 0.0
        self.agent_name = agent_name
        self.owner_email = owner_email
        self.base_url = (
            base_url
            or os.environ.get("AGENTICTRADE_URL")
            or _DEFAULT_BASE_URL
        ).rstrip("/")

        self._api_key = api_key or os.environ.get("AGENTICTRADE_API_KEY")
        self._agent_id: str | None = None
        self._owner_id: str | None = None
        self._spent_usdc: float = 0.0

        self._load_state()

    # ------------------------------------------------------------------
    # Core API: pay() — the one-liner
    # ------------------------------------------------------------------

    def pay(
        self,
        service_name_or_id: str,
        payload: dict | None = None,
    ) -> PayResult:
        """Discover, pay, and call a service in one step.

        Args:
            service_name_or_id: Service name (fuzzy search) or exact ID.
            payload: Request body to send to the service.

        Returns:
            PayResult with response data, amount paid, etc.

        Raises:
            BudgetExceeded: If payment would exceed budget_usdc.
            AgenticTradeError: On API errors.
        """
        self._ensure_onboarded()

        # 1. Discover service
        service = self._find_service(service_name_or_id)
        if not service:
            return PayResult(
                success=False,
                status_code=404,
                error=f"Service '{service_name_or_id}' not found",
            )

        # 2. Budget check
        price = float(service.get("pricing", {}).get("price_per_call", "0.01"))
        if self.budget_usdc > 0 and (self._spent_usdc + price) > self.budget_usdc:
            raise BudgetExceeded(
                f"Budget exceeded: spent ${self._spent_usdc:.4f} + "
                f"${price:.4f} > limit ${self.budget_usdc:.2f}"
            )

        # 3. Call via proxy (payment handled by Nanopayments gateway)
        svc_id = service["id"]
        try:
            with httpx.Client(base_url=self.base_url, timeout=30.0) as client:
                resp = client.post(
                    f"/api/v1/proxy/{svc_id}/call",
                    json=payload or {},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )

            if 200 <= resp.status_code < 300:
                self._spent_usdc += price
                self._save_state()
                return PayResult(
                    success=True,
                    status_code=resp.status_code,
                    data=resp.json(),
                    service_id=svc_id,
                    amount_usdc=str(price),
                )
            else:
                return PayResult(
                    success=False,
                    status_code=resp.status_code,
                    service_id=svc_id,
                    error=resp.text[:200],
                )
        except Exception as e:
            return PayResult(
                success=False,
                status_code=0,
                service_id=svc_id,
                error=str(e),
            )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(
        self,
        query: str = "",
        category: str = "",
    ) -> list[dict]:
        """Search the marketplace for services."""
        params: dict[str, str] = {}
        if query:
            params["query"] = query
        if category:
            params["category"] = category

        with httpx.Client(base_url=self.base_url, timeout=15.0) as client:
            resp = client.get("/api/v1/services", params=params)
        if resp.status_code == 200:
            return resp.json()
        return []

    # ------------------------------------------------------------------
    # Budget
    # ------------------------------------------------------------------

    @property
    def budget_remaining(self) -> float:
        """Remaining budget in USDC (0 = unlimited)."""
        if self.budget_usdc <= 0:
            return float("inf")
        return max(0.0, self.budget_usdc - self._spent_usdc)

    @property
    def total_spent(self) -> float:
        return self._spent_usdc

    def reset_budget(self) -> None:
        """Reset spent counter to 0."""
        self._spent_usdc = 0.0
        self._save_state()

    # ------------------------------------------------------------------
    # Onboarding
    # ------------------------------------------------------------------

    def _ensure_onboarded(self) -> None:
        if self._api_key and self._agent_id:
            return

        payload = {
            "agent_name": self.agent_name,
            "role": "buyer",
            "wallet_address": self.wallet_address,
            "owner_email": self.owner_email,
            "budget_limit_usdc": str(self.budget_usdc) if self.budget_usdc > 0 else "",
        }

        with httpx.Client(base_url=self.base_url, timeout=30.0) as client:
            resp = client.post("/api/v1/agents/onboard", json=payload)

        if resp.status_code >= 400:
            detail = resp.text[:500]
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            raise AgenticTradeError(f"Buyer onboard failed: {detail}")

        data = resp.json()
        self._agent_id = data["agent_id"]
        self._owner_id = data["owner_id"]
        self._api_key = data["api_key"]
        self._save_state()
        logger.info("Buyer onboarded: agent_id=%s", self._agent_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_service(self, name_or_id: str) -> dict | None:
        """Find a service by name (fuzzy) or exact ID."""
        with httpx.Client(base_url=self.base_url, timeout=15.0) as client:
            # Try exact ID first
            resp = client.get(f"/api/v1/services/{name_or_id}")
            if resp.status_code == 200:
                return resp.json()

            # Fuzzy search by name
            resp = client.get("/api/v1/services", params={"query": name_or_id})
            if resp.status_code == 200:
                results = resp.json()
                if results:
                    return results[0]
        return None

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        state = {
            "agent_id": self._agent_id,
            "owner_id": self._owner_id,
            "api_key": self._api_key,
            "base_url": self.base_url,
            "agent_name": self.agent_name,
            "spent_usdc": self._spent_usdc,
            "budget_usdc": self.budget_usdc,
        }
        try:
            Path(self._STATE_FILE).write_text(json.dumps(state, indent=2))
        except Exception as e:
            logger.warning("Failed to save buyer state: %s", e)

    def _load_state(self) -> None:
        try:
            path = Path(self._STATE_FILE)
            if not path.is_file():
                return
            state = json.loads(path.read_text())
            if state.get("agent_name") != self.agent_name:
                return
            self._agent_id = self._agent_id or state.get("agent_id")
            self._owner_id = self._owner_id or state.get("owner_id")
            self._api_key = self._api_key or state.get("api_key")
            self._spent_usdc = state.get("spent_usdc", 0.0)
        except Exception:
            pass
