"""
LangChain-compatible tools for the AgenticTrade marketplace.

Each tool wraps a single AgenticTrade API endpoint, using ``langchain_core.tools.BaseTool``
so they work with any LangChain agent (OpenAI Functions, ReAct, plan-and-execute, etc.)
and with LlamaIndex via its LangChain tool adapter.

All tools communicate through an ``AgenticTradeClient`` that handles auth,
retries, and error normalisation.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, Type

import httpx
from langchain_core.callbacks import CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger("agentictrade_langchain")

# ---------------------------------------------------------------------------
# Shared HTTP client
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = "https://agentictrade.io"
_DEFAULT_TIMEOUT = 30.0
_MAX_RETRIES = 2


class AgenticTradeClient:
    """Thin HTTP wrapper that all tools share.

    Holds the base URL, API key, and a reusable ``httpx.Client`` so that
    connection pooling is used across tool invocations within the same
    agent run.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not api_key or ":" not in api_key:
            raise ValueError(
                "api_key must be in 'key_id:secret' format. "
                "Create one via POST /api/v1/keys on AgenticTrade."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "agentictrade-langchain/0.1.0",
                },
                timeout=self.timeout,
            )
        return self._client

    def get(self, path: str, params: dict | None = None) -> dict:
        """Issue a GET request and return parsed JSON."""
        resp = self.client.get(path, params=params)
        return self._handle(resp)

    def post(self, path: str, json_body: dict | None = None) -> dict:
        """Issue a POST request and return parsed JSON."""
        resp = self.client.post(path, json=json_body)
        return self._handle(resp)

    def request(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Issue an arbitrary HTTP request and return parsed JSON."""
        resp = self.client.request(method, path, json=json_body, params=params)
        return self._handle(resp)

    # ------------------------------------------------------------------

    @staticmethod
    def _handle(resp: httpx.Response) -> dict:
        """Parse response; raise informative errors on failure."""
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "60")
            raise RuntimeError(
                f"Rate limited by AgenticTrade. Retry after {retry_after}s."
            )
        if resp.status_code == 401:
            raise RuntimeError(
                "Authentication failed. Check your AgenticTrade API key "
                "(format: key_id:secret)."
            )
        if resp.status_code == 403:
            raise RuntimeError(
                "Permission denied. Your API key may lack the required role."
            )
        if resp.status_code == 404:
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                pass
            raise RuntimeError(f"Resource not found. {detail}".strip())

        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("detail", resp.text[:200])
            except Exception:
                detail = resp.text[:200]
            raise RuntimeError(
                f"AgenticTrade API error {resp.status_code}: {detail}"
            )

        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()


# ---------------------------------------------------------------------------
# Input schemas (Pydantic v2)
# ---------------------------------------------------------------------------


class SearchInput(BaseModel):
    """Input for searching the AgenticTrade marketplace."""

    query: str = Field(
        default="",
        description=(
            "Free-text search query. Examples: 'crypto price API', "
            "'sentiment analysis', 'image generation'."
        ),
    )
    category: str = Field(
        default="",
        description=(
            "Filter by service category. Leave empty to search all categories."
        ),
    )
    tags: str = Field(
        default="",
        description=(
            "Comma-separated tags to filter by. "
            "Example: 'nlp,sentiment,english'."
        ),
    )
    min_price: str = Field(
        default="",
        description="Minimum price per call in USD. Example: '0.001'.",
    )
    max_price: str = Field(
        default="",
        description="Maximum price per call in USD. Example: '1.00'.",
    )
    has_free_tier: Optional[bool] = Field(
        default=None,
        description="Set to true to only return services that offer free tier calls.",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of results to return (1-100).",
    )


class ServiceCallInput(BaseModel):
    """Input for calling a service through the AgenticTrade proxy."""

    service_id: str = Field(
        ...,
        description=(
            "The UUID of the service to call. "
            "Obtain this from the search tool or service detail tool."
        ),
    )
    path: str = Field(
        default="",
        description=(
            "The API path on the service. Example: 'analyze' or 'v1/predict'. "
            "Do NOT include a leading slash."
        ),
    )
    method: str = Field(
        default="POST",
        description="HTTP method: GET, POST, PUT, or DELETE.",
    )
    body: str = Field(
        default="{}",
        description=(
            "JSON string of the request body to send. "
            "Example: '{\"text\": \"Hello world\"}'. "
            "Only used for POST/PUT requests."
        ),
    )
    query_params: str = Field(
        default="{}",
        description=(
            "JSON string of query parameters. "
            "Example: '{\"format\": \"json\"}'. "
            "Appended as ?key=value to the request URL."
        ),
    )


class ServiceDetailInput(BaseModel):
    """Input for retrieving service details."""

    service_id: str = Field(
        ...,
        description="The UUID of the service to look up.",
    )


class BalanceInput(BaseModel):
    """Input for checking account balance."""

    buyer_id: str = Field(
        ...,
        description=(
            "Your buyer ID (same as the owner_id associated with your API key)."
        ),
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class AgenticTradeSearchTool(BaseTool):
    """Search the AgenticTrade AI-agent marketplace for services.

    Use this tool when you need to find an API or AI service to accomplish a task.
    Returns a list of services with their IDs, names, descriptions, pricing,
    categories, and quality scores.

    After finding a relevant service, use ``agentictrade_call`` to invoke it
    or ``agentictrade_service_detail`` to get full details first.
    """

    name: str = "agentictrade_search"
    description: str = (
        "Search the AgenticTrade marketplace for AI agent services and APIs. "
        "Returns service names, IDs, descriptions, pricing, and quality scores. "
        "Use this to discover services before calling them."
    )
    args_schema: Type[BaseModel] = SearchInput
    handle_tool_error: bool = True

    # Instance config -- not part of the schema
    client: Any = Field(exclude=True)

    model_config = {"arbitrary_types_allowed": True}

    def _run(
        self,
        query: str = "",
        category: str = "",
        tags: str = "",
        min_price: str = "",
        max_price: str = "",
        has_free_tier: Optional[bool] = None,
        limit: int = 10,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        params: dict[str, Any] = {"limit": limit}
        if query:
            params["q"] = query
        if category:
            params["category"] = category
        if tags:
            params["tags"] = tags
        if min_price:
            params["min_price"] = min_price
        if max_price:
            params["max_price"] = max_price
        if has_free_tier is not None:
            params["has_free_tier"] = str(has_free_tier).lower()

        try:
            data = self.client.get("/api/v1/discover", params=params)
        except Exception as exc:
            return f"Error searching AgenticTrade: {exc}"

        services = data.get("services", [])
        total = data.get("total", len(services))

        if not services:
            return (
                f"No services found matching your query. "
                f"Try broadening your search terms or removing filters."
            )

        lines = [f"Found {total} services (showing {len(services)}):"]
        for svc in services:
            pricing = svc.get("pricing", {})
            quality = svc.get("quality", {})
            price_str = pricing.get("price_per_call", "?")
            free_tier = pricing.get("free_tier_calls", 0)
            health = quality.get("health_score")
            tier = quality.get("quality_tier", "Standard")

            line_parts = [
                f"\n- **{svc.get('name', 'Unnamed')}**",
                f"  ID: {svc.get('id', '?')}",
                f"  Description: {svc.get('description', 'N/A')}",
                f"  Price: ${price_str}/call",
            ]
            if free_tier:
                line_parts.append(f"  Free tier: {free_tier} calls")
            line_parts.append(f"  Category: {svc.get('category', 'N/A')}")
            line_parts.append(f"  Tags: {', '.join(svc.get('tags', []))}")
            line_parts.append(f"  Quality: {tier}")
            if health is not None:
                line_parts.append(f"  Health score: {health}/100")
            lines.append("\n".join(line_parts))

        return "\n".join(lines)


class AgenticTradeServiceDetailTool(BaseTool):
    """Get full details of a specific service on AgenticTrade.

    Use this after searching to learn more about a service before calling it --
    for example, to understand its pricing, endpoint format, or status.
    """

    name: str = "agentictrade_service_detail"
    description: str = (
        "Get detailed information about a specific AgenticTrade service by its ID. "
        "Returns name, description, endpoint, pricing, status, category, and tags. "
        "Use the service ID from search results."
    )
    args_schema: Type[BaseModel] = ServiceDetailInput
    handle_tool_error: bool = True

    client: Any = Field(exclude=True)

    model_config = {"arbitrary_types_allowed": True}

    def _run(
        self,
        service_id: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        try:
            data = self.client.get(f"/api/v1/services/{service_id}")
        except Exception as exc:
            return f"Error fetching service details: {exc}"

        pricing = data.get("pricing", {})
        return (
            f"Service: {data.get('name', 'Unknown')}\n"
            f"ID: {data.get('id')}\n"
            f"Provider: {data.get('provider_id', 'N/A')}\n"
            f"Description: {data.get('description', 'N/A')}\n"
            f"Status: {data.get('status', 'unknown')}\n"
            f"Category: {data.get('category', 'N/A')}\n"
            f"Tags: {', '.join(data.get('tags', []))}\n"
            f"Price per call: ${pricing.get('price_per_call', '?')}\n"
            f"Currency: {pricing.get('currency', 'USD')}\n"
            f"Payment method: {pricing.get('payment_method', 'N/A')}\n"
            f"Free tier calls: {pricing.get('free_tier_calls', 0)}\n"
            f"Created: {data.get('created_at', 'N/A')}\n"
            f"Updated: {data.get('updated_at', 'N/A')}"
        )


class AgenticTradeCallTool(BaseTool):
    """Call a service through the AgenticTrade payment proxy.

    The marketplace handles authentication with the service provider,
    usage tracking, and automatic payment (deducted from your pre-paid balance).

    Response headers include billing info:
    - X-ACF-Usage-Id: unique usage record ID
    - X-ACF-Amount: amount charged for this call
    - X-ACF-Free-Tier: whether this call was free tier
    """

    name: str = "agentictrade_call"
    description: str = (
        "Call an AI service through the AgenticTrade marketplace proxy. "
        "The marketplace handles auth, billing, and payment automatically. "
        "You need the service_id (from search), an API path, HTTP method, "
        "and optionally a JSON body. Returns the service's response."
    )
    args_schema: Type[BaseModel] = ServiceCallInput
    handle_tool_error: bool = True

    client: Any = Field(exclude=True)

    model_config = {"arbitrary_types_allowed": True}

    def _run(
        self,
        service_id: str,
        path: str = "",
        method: str = "POST",
        body: str = "{}",
        query_params: str = "{}",
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        # Validate method
        method = method.upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            return f"Invalid HTTP method '{method}'. Use GET, POST, PUT, PATCH, or DELETE."

        # Parse body
        json_body: dict | None = None
        if method in ("POST", "PUT", "PATCH") and body and body != "{}":
            try:
                json_body = json.loads(body)
            except json.JSONDecodeError as exc:
                return f"Invalid JSON body: {exc}. Provide a valid JSON string."

        # Parse query params
        params: dict | None = None
        if query_params and query_params != "{}":
            try:
                params = json.loads(query_params)
            except json.JSONDecodeError:
                params = None

        # Sanitize path
        clean_path = path.lstrip("/")
        proxy_path = f"/api/v1/proxy/{service_id}"
        if clean_path:
            proxy_path = f"{proxy_path}/{clean_path}"

        try:
            data = self.client.request(
                method=method,
                path=proxy_path,
                json_body=json_body,
                params=params,
            )
        except Exception as exc:
            return f"Error calling service {service_id}: {exc}"

        # Return the response as formatted JSON
        try:
            return json.dumps(data, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(data)


class AgenticTradeBalanceTool(BaseTool):
    """Check your pre-paid balance on AgenticTrade.

    Use this to verify you have sufficient funds before calling paid services,
    or to monitor spending.
    """

    name: str = "agentictrade_balance"
    description: str = (
        "Check your AgenticTrade account balance. "
        "Returns current balance, total deposited, and total spent in USD. "
        "Use this to verify funds before calling paid services."
    )
    args_schema: Type[BaseModel] = BalanceInput
    handle_tool_error: bool = True

    client: Any = Field(exclude=True)

    model_config = {"arbitrary_types_allowed": True}

    def _run(
        self,
        buyer_id: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        try:
            data = self.client.get(f"/api/v1/balance/{buyer_id}")
        except Exception as exc:
            return f"Error checking balance: {exc}"

        return (
            f"AgenticTrade Balance:\n"
            f"  Buyer ID: {data.get('buyer_id', buyer_id)}\n"
            f"  Current balance: ${data.get('balance', 0):.4f}\n"
            f"  Total deposited: ${data.get('total_deposited', 0):.4f}\n"
            f"  Total spent: ${data.get('total_spent', 0):.4f}"
        )
