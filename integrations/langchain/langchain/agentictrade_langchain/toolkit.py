"""
AgenticTradeToolkit -- bundles all AgenticTrade tools into a single toolkit.

Compatible with LangChain's ``BaseToolkit`` pattern and usable with LlamaIndex
via its LangChain tool adapter.
"""

from __future__ import annotations

from typing import List

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from agentictrade_langchain.tool import (
    AgenticTradeBalanceTool,
    AgenticTradeCallTool,
    AgenticTradeClient,
    AgenticTradeSearchTool,
    AgenticTradeServiceDetailTool,
)

_DEFAULT_BASE_URL = "https://agentictrade.io"


class AgenticTradeToolkit(BaseModel):
    """A toolkit that provides all AgenticTrade marketplace tools.

    Instantiate with your API key and optionally a base URL, then call
    ``get_tools()`` to get a list of LangChain-compatible tools ready
    for use in any agent.

    Example::

        from agentictrade_langchain import AgenticTradeToolkit

        toolkit = AgenticTradeToolkit(api_key="key_abc123:sec_xyz789")
        tools = toolkit.get_tools()

        # Use with a LangChain agent
        from langchain.agents import AgentExecutor, create_openai_functions_agent
        agent = create_openai_functions_agent(llm, tools, prompt)
        executor = AgentExecutor(agent=agent, tools=tools)
        executor.invoke({"input": "Find a sentiment analysis API"})
    """

    api_key: str = Field(
        ...,
        description="AgenticTrade API key in 'key_id:secret' format.",
    )
    base_url: str = Field(
        default=_DEFAULT_BASE_URL,
        description="AgenticTrade API base URL.",
    )
    timeout: float = Field(
        default=30.0,
        description="HTTP timeout in seconds for API calls.",
    )

    # Tools to include; set to False to exclude a tool from the toolkit.
    include_search: bool = Field(default=True, description="Include the search tool.")
    include_service_detail: bool = Field(
        default=True, description="Include the service detail tool."
    )
    include_call: bool = Field(default=True, description="Include the call tool.")
    include_balance: bool = Field(
        default=True, description="Include the balance tool."
    )

    model_config = {"arbitrary_types_allowed": True}

    def get_tools(self) -> List[BaseTool]:
        """Return a list of LangChain tools for the AgenticTrade marketplace.

        Each tool shares the same underlying HTTP client for connection
        pooling and consistent authentication.
        """
        client = AgenticTradeClient(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )

        tools: list[BaseTool] = []

        if self.include_search:
            tools.append(AgenticTradeSearchTool(client=client))
        if self.include_service_detail:
            tools.append(AgenticTradeServiceDetailTool(client=client))
        if self.include_call:
            tools.append(AgenticTradeCallTool(client=client))
        if self.include_balance:
            tools.append(AgenticTradeBalanceTool(client=client))

        return tools
