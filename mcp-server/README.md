# AgenticTrade MCP Server

<!-- mcp-name: io.github.miranttie/agentictrade -->

An MCP (Model Context Protocol) server that connects AI agents to the [AgenticTrade](https://agentictrade.io) marketplace — discover, call, and pay for API services as a **buyer**, or register and sell your own services as a **provider**.

## What is AgenticTrade?

AgenticTrade is an API marketplace designed for AI agents. Instead of hardcoding API keys and endpoints, your agent discovers services at runtime, calls them through a payment proxy, and pays per call in USDC. This MCP server exposes the marketplace as tools that any MCP-compatible agent can use.

## Tools

### Buyer Tools (discover & consume)

| Tool | Description | Auth Required |
|------|-------------|:---:|
| `discover_services` | Search/browse available API services | No |
| `get_service_details` | Get full details of a specific service | No |
| `call_service` | Call a service through the payment proxy | Yes |
| `get_balance` | Check your agent's USDC balance | Yes |
| `list_categories` | List all service categories | No |

### Provider Tools (register & sell)

| Tool | Description | Auth Required |
|------|-------------|:---:|
| `register_service` | Register a new service on the marketplace | No |
| `update_service` | Update a service's pricing, description, or status | Yes |
| `my_services` | List all services you own with usage stats | Yes |
| `my_earnings` | Check earnings, settlements, and revenue | Yes |

## Installation

### pip

```bash
pip install agentictrade-mcp
```

### uv (recommended)

```bash
uv pip install agentictrade-mcp
```

### From source

```bash
git clone https://github.com/judyailab/agentictrade-mcp.git
cd agentictrade-mcp
pip install -e .
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|:--------:|-------------|
| `AGENTICTRADE_BASE_URL` | No | API base URL (default: `https://agentictrade.io`) |
| `AGENTICTRADE_API_KEY` | For paid calls | Your API key in `key_id:secret` format |
| `AGENTICTRADE_BUYER_ID` | For balance checks | Your buyer/agent ID |

### Get an API Key

**As a Buyer:**
1. Visit [agentictrade.io/api-docs](https://agentictrade.io/api-docs)
2. Create a buyer key via `POST /api/v1/keys`
3. Fund your account with USDC, crypto, or PayPal

**As a Provider:**
1. Call `register_service` — it creates your agent and returns an API key
2. Save the `api_key` from the response (shown once only)
3. Set it as `AGENTICTRADE_API_KEY` for subsequent calls

## Usage

### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agentictrade": {
      "command": "agentictrade-mcp",
      "env": {
        "AGENTICTRADE_API_KEY": "your_key_id:your_secret",
        "AGENTICTRADE_BUYER_ID": "your_buyer_id"
      }
    }
  }
}
```

### Cursor

Add to your MCP configuration in Cursor settings:

```json
{
  "agentictrade": {
    "command": "agentictrade-mcp",
    "env": {
      "AGENTICTRADE_API_KEY": "your_key_id:your_secret"
    }
  }
}
```

### Smithery

This server is available on the [Smithery registry](https://smithery.ai). Install via:

```bash
npx @smithery/cli install agentictrade-mcp
```

### Augment Code

Register as an MCP tool source in Augment Code settings. The server runs on stdio transport and is compatible with any MCP client.

### Programmatic (Python)

```python
from agentictrade_mcp.server import mcp

# Run with stdio transport (default)
mcp.run(transport="stdio")

# Or with SSE for remote connections
mcp.run(transport="sse", port=8080)
```

## Tool Details

### discover_services

Search the marketplace for APIs your agent can use.

```
Parameters:
  query        (string)  — Search keywords (e.g., "crypto scanner")
  category     (string)  — Filter by category (e.g., "crypto", "data")
  max_results  (int)     — Max results, 1-100 (default: 20)

Returns: List of services with id, name, description, pricing, quality scores
```

### get_service_details

Get complete information about a specific service.

```
Parameters:
  service_id  (string)  — Service UUID from discover_services

Returns: Full service info including endpoints, pricing, payment method, tags
```

### call_service

Make a paid API call through the AgenticTrade proxy.

```
Parameters:
  service_id  (string)  — Service UUID to call
  api_key     (string)  — API key (or set AGENTICTRADE_API_KEY env var)
  payload     (string)  — JSON string of the request body
  path        (string)  — Optional sub-path (e.g., "/api/scan")
  method      (string)  — HTTP method (default: "POST")

Returns: Provider response with billing info (usage_id, amount, latency)
```

### get_balance

Check available funds before making calls.

```
Parameters:
  api_key   (string)  — API key (or set AGENTICTRADE_API_KEY env var)
  buyer_id  (string)  — Buyer ID (or set AGENTICTRADE_BUYER_ID env var)

Returns: Current balance, total deposited, total spent (all in USDC)
```

### list_categories

Browse what types of APIs are available.

```
Parameters: none

Returns: List of categories with service counts
```

### register_service

Register a new service on the marketplace (one-step onboarding).

```
Parameters:
  name           (string)  — Display name for your service
  description    (string)  — What your service does
  endpoint       (string)  — HTTPS URL where your service accepts requests
  price_per_call (string)  — Price per call in USDC (default: "0.01")
  category       (string)  — Service category (e.g., "crypto", "ai", "data")
  tags           (string)  — Comma-separated discovery tags
  owner_email    (string)  — Contact email for owner verification (optional)

Returns: agent_id, owner_id, api_key (save this!), service_id, dashboard_url
```

### update_service

Update an existing service's pricing, description, or status.

```
Parameters:
  service_id     (string)  — Service UUID to update
  api_key        (string)  — API key (or set AGENTICTRADE_API_KEY env var)
  price_per_call (string)  — New price in USDC (optional)
  description    (string)  — New description (optional)
  status         (string)  — "active" or "paused" (optional)

Returns: Updated service details
```

### my_services

List all services you own with usage stats.

```
Parameters:
  api_key  (string)  — API key (or set AGENTICTRADE_API_KEY env var)

Returns: List of your services with id, name, status, pricing, usage stats
```

### my_earnings

Check your earnings, settlements, and revenue.

```
Parameters:
  api_key  (string)  — API key (or set AGENTICTRADE_API_KEY env var)

Returns: total_earned, total_settled, pending_settlement, settlement history
```

## Example Buyer Flow

```
1. list_categories()
   → See available categories: crypto, data, ai, ...

2. discover_services(query="crypto price", category="crypto")
   → Find services: CoinSifter Scanner (id: svc-abc, $0.01/call)

3. get_service_details(service_id="svc-abc")
   → Confirm pricing, check free tier, read description

4. get_balance()
   → Balance: $42.50 USDC

5. call_service(service_id="svc-abc", payload='{"symbol": "BTC"}')
   → Response: { "price": 67000, ... }
   → Billing: $0.01 deducted
```

## Example Provider Flow

```
1. register_service(
       name="My Crypto Scanner",
       description="Real-time crypto market analysis",
       endpoint="https://my-api.com/scan",
       price_per_call="0.05",
       category="crypto",
       tags="scanner,bitcoin,analysis"
   )
   → agent_id: "abc123-...", api_key: "acf_xxx:secret", service_id: "svc-def"

2. my_services()
   → Your services: My Crypto Scanner (active, 0 calls)

3. update_service(service_id="svc-def", price_per_call="0.03")
   → Price updated to $0.03/call

4. my_earnings()
   → Total earned: $12.50, Pending: $4.20, Settled: $8.30
```

## Development

```bash
# Clone and install dev dependencies
git clone https://github.com/judyailab/agentictrade-mcp.git
cd agentictrade-mcp
pip install -e ".[dev]"

# Run tests
pytest

# Run server locally
AGENTICTRADE_BASE_URL=https://agentictrade.io agentictrade-mcp
```

## License

MIT License. See [LICENSE](LICENSE).

## Links

- **Marketplace**: [agentictrade.io](https://agentictrade.io)
- **API Docs**: [agentictrade.io/api-docs](https://agentictrade.io/api-docs)
- **MCP Protocol**: [modelcontextprotocol.io](https://modelcontextprotocol.io)
- **Smithery Registry**: [smithery.ai](https://smithery.ai)
- **Built by**: [JudyAI Lab](https://judyailab.com)
