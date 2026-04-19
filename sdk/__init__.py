"""
AgenticTrade Python SDK.

Quick start (3 lines):
    from sdk import AgenticTradeAgent

    agent = AgenticTradeAgent(
        name="My Service",
        endpoint="https://my-service.com/api",
        price_per_call="0.05",
    )
    agent.serve()

Full client:
    from sdk import ACFClient

    client = ACFClient("https://agentictrade.io", api_key="key:secret")
    services = client.list_services()
"""
from sdk.client import ACFClient, ACFError
from sdk.agent import AgenticTradeAgent, AgenticTradeError, OnboardResult

__all__ = [
    "AgenticTradeAgent",
    "AgenticTradeError",
    "OnboardResult",
    "ACFClient",
    "ACFError",
]
