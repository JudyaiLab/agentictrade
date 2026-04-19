# agentictrade-langchain

LangChain & LlamaIndex tool integration for the [AgenticTrade](https://agentictrade.io) AI-agent marketplace.

Give your AI agents the ability to **discover, call, and pay for APIs** on AgenticTrade -- directly from LangChain or LlamaIndex workflows.

## Installation

```bash
pip install agentictrade-langchain
```

For LlamaIndex support:

```bash
pip install agentictrade-langchain[llamaindex]
```

## Quick Start

```python
from agentictrade_langchain import AgenticTradeToolkit

# Create toolkit with your API key (format: key_id:secret)
toolkit = AgenticTradeToolkit(api_key="key_abc123:sec_xyz789")
tools = toolkit.get_tools()
```

This gives you four tools:

| Tool | Description |
|------|-------------|
| `agentictrade_search` | Search the marketplace for services by query, category, tags, or price |
| `agentictrade_service_detail` | Get full details of a specific service by ID |
| `agentictrade_call` | Call a service through the payment proxy (handles auth + billing) |
| `agentictrade_balance` | Check your pre-paid account balance |

## LangChain Usage

### With OpenAI Functions Agent

```python
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from agentictrade_langchain import AgenticTradeToolkit

# Set up tools
toolkit = AgenticTradeToolkit(api_key="key_abc123:sec_xyz789")
tools = toolkit.get_tools()

# Set up agent
llm = ChatOpenAI(model="gpt-4o", temperature=0)
prompt = ChatPromptTemplate.from_messages([
    ("system", (
        "You are a helpful assistant with access to the AgenticTrade marketplace. "
        "You can search for AI services, check pricing, and call them on behalf "
        "of the user. Always check your balance before making paid calls."
    )),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])

agent = create_openai_functions_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

# Run
result = executor.invoke({
    "input": "Find a sentiment analysis API and analyze the text: 'AgenticTrade is amazing!'"
})
print(result["output"])
```

### With ReAct Agent

```python
from langchain.agents import create_react_agent
from langchain_core.prompts import PromptTemplate
from langchain_anthropic import ChatAnthropic
from agentictrade_langchain import AgenticTradeToolkit

toolkit = AgenticTradeToolkit(api_key="key_abc123:sec_xyz789")
tools = toolkit.get_tools()

llm = ChatAnthropic(model="claude-sonnet-4-20250514")

template = """Answer the following question using the available tools.

Tools: {tools}
Tool names: {tool_names}

Question: {input}

{agent_scratchpad}"""

prompt = PromptTemplate.from_template(template)
agent = create_react_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
result = executor.invoke({"input": "What crypto analysis services are available?"})
```

### Individual Tools

You can also use tools individually without the toolkit:

```python
from agentictrade_langchain.tool import (
    AgenticTradeClient,
    AgenticTradeSearchTool,
    AgenticTradeCallTool,
)

client = AgenticTradeClient(api_key="key_abc123:sec_xyz789")

# Search only
search = AgenticTradeSearchTool(client=client)
result = search.invoke({"query": "price data", "category": "crypto", "limit": 5})

# Call a service
call = AgenticTradeCallTool(client=client)
result = call.invoke({
    "service_id": "svc-uuid-here",
    "path": "analyze",
    "method": "POST",
    "body": '{"symbol": "BTC"}',
})
```

## LlamaIndex Usage

Use LlamaIndex's LangChain tool wrapper to integrate AgenticTrade tools:

```python
from llama_index.core.tools import FunctionTool
from llama_index.core.agent import ReActAgent
from llama_index.llms.openai import OpenAI
from agentictrade_langchain import AgenticTradeToolkit

# Create tools
toolkit = AgenticTradeToolkit(api_key="key_abc123:sec_xyz789")
lc_tools = toolkit.get_tools()

# Convert to LlamaIndex tools
li_tools = [FunctionTool.from_defaults(fn=t.invoke, name=t.name, description=t.description)
            for t in lc_tools]

# Create agent
llm = OpenAI(model="gpt-4o")
agent = ReActAgent.from_tools(li_tools, llm=llm, verbose=True)
response = agent.chat("Find an image generation service and check its pricing")
```

### With LlamaIndex LangchainToolSpec (alternative)

```python
from llama_index.core.tools.tool_spec.load_and_search import LoadAndSearchToolSpec

# Use the search tool directly
search_tool = lc_tools[0]  # agentictrade_search
spec = LoadAndSearchToolSpec.from_defaults(search_tool)
```

## Configuration

### Custom Base URL

For self-hosted or staging instances:

```python
toolkit = AgenticTradeToolkit(
    api_key="key_abc123:sec_xyz789",
    base_url="https://staging.agentictrade.io",
)
```

### Timeout

```python
toolkit = AgenticTradeToolkit(
    api_key="key_abc123:sec_xyz789",
    timeout=60.0,  # seconds
)
```

### Selective Tools

Include only the tools you need:

```python
toolkit = AgenticTradeToolkit(
    api_key="key_abc123:sec_xyz789",
    include_search=True,
    include_service_detail=True,
    include_call=True,
    include_balance=False,  # exclude balance tool
)
```

## API Reference

### Search Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | str | Free-text search query |
| `category` | str | Category filter |
| `tags` | str | Comma-separated tags |
| `min_price` | str | Minimum price per call (USD) |
| `max_price` | str | Maximum price per call (USD) |
| `has_free_tier` | bool | Filter for services with free tier |
| `limit` | int | Max results (1-100, default 10) |

### Service Call Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `service_id` | str | Service UUID (required) |
| `path` | str | API path on the service |
| `method` | str | HTTP method (GET/POST/PUT/DELETE) |
| `body` | str | JSON request body |
| `query_params` | str | JSON query parameters |

### Authentication

Get your API key from the [AgenticTrade Portal](https://agentictrade.io/portal/register):

1. Register as a buyer
2. Create an API key via `POST /api/v1/keys`
3. Use the returned `key_id:secret` as your `api_key`

## Error Handling

All tools return error messages as strings rather than raising exceptions,
so your agent can read the error and decide how to recover:

```
"Error searching AgenticTrade: Rate limited by AgenticTrade. Retry after 60s."
"Error calling service abc-123: Authentication failed. Check your AgenticTrade API key."
"Error checking balance: Resource not found."
```

## License

MIT
