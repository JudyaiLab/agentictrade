"""
Request correlation middleware.

Generates a UUID4 request_id for every incoming request and propagates it:
- Stored in ``request.state.request_id``
- Returned as ``X-Request-Id`` response header
- Validates client-supplied ``X-Request-Id`` (alphanumeric/dashes/underscores, max 64 chars).
  Valid client IDs are prefixed with ``ext-`` to distinguish from server-generated ones.
  Invalid or missing IDs result in a fresh UUID4.
"""
from __future__ import annotations

import re
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

_REQUEST_ID_PATTERN = re.compile(r'^[a-zA-Z0-9\-_]{1,64}$')


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a correlation ID to every request/response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get("x-request-id")
        if incoming and _REQUEST_ID_PATTERN.match(incoming):
            request_id = f"ext-{incoming}"
        else:
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
