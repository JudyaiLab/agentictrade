"""
agentictrade-langchain: LangChain & LlamaIndex tool integration for AgenticTrade.

Provides LangChain-compatible tools that let AI agents discover, call,
and pay for services on the AgenticTrade marketplace.

Usage:
    from agentictrade_langchain import AgenticTradeToolkit

    toolkit = AgenticTradeToolkit(api_key="your_key_id:your_secret")
    tools = toolkit.get_tools()
"""

from agentictrade_langchain.tool import (
    AgenticTradeBalanceTool,
    AgenticTradeCallTool,
    AgenticTradeSearchTool,
    AgenticTradeServiceDetailTool,
)
from agentictrade_langchain.toolkit import AgenticTradeToolkit

__all__ = [
    "AgenticTradeSearchTool",
    "AgenticTradeCallTool",
    "AgenticTradeBalanceTool",
    "AgenticTradeServiceDetailTool",
    "AgenticTradeToolkit",
]

__version__ = "0.1.0"
