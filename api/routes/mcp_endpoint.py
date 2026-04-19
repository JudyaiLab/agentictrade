"""MCP JSON-RPC 2.0 HTTP Endpoint.

Exposes marketplace tools via standard MCP protocol over HTTP.
ChatGPT Apps, Claude Desktop, and any MCP client can connect here.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mcp_bridge.server import TOOL_DEFINITIONS

router = APIRouter(tags=["mcp"])
log = logging.getLogger("mcp_endpoint")


def _jsonrpc_ok(req_id, result):
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _jsonrpc_error(req_id, code, message):
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
        status_code=200,
    )


@router.post("/mcp")
async def mcp_handler(request: Request):
    """MCP JSON-RPC 2.0 handler for marketplace tools.

    Supported methods:
    - initialize: Server capabilities handshake
    - tools/list: List available marketplace tools
    - tools/call: Execute a marketplace tool
    """
    try:
        body = await request.json()
    except Exception:
        return _jsonrpc_error(None, -32700, "Parse error")

    req_id = body.get("id", 1)
    method = body.get("method", "")
    params = body.get("params", {})

    if method == "initialize":
        return _jsonrpc_ok(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": "agentictrade",
                "version": "1.0.0",
            },
        })

    if method == "tools/list":
        return _jsonrpc_ok(req_id, {"tools": TOOL_DEFINITIONS})

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        mcp_server = getattr(request.app.state, "mcp_server", None)
        if mcp_server is None:
            return _jsonrpc_error(req_id, -32603, "MCP server not initialized")

        try:
            results = await mcp_server.call_tool(tool_name, arguments)
            return _jsonrpc_ok(req_id, {
                "content": [{"type": r.type, "text": r.text} for r in results],
            })
        except Exception as exc:
            log.exception("MCP tool call failed: %s", tool_name)
            return _jsonrpc_error(req_id, -32603, str(exc))

    if method == "notifications/initialized":
        return _jsonrpc_ok(req_id, {})

    return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")
