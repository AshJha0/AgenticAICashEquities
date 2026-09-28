"""Build the domain ``ToolRegistry`` from MCP servers (discovery -> adapters)."""

from __future__ import annotations

from ceap.data.historical import HistoricalStore
from ceap.data.repositories import DatasetStore
from ceap.domain.tools import ToolRegistry
from ceap.mcp.adapter import MCPToolAdapter
from ceap.mcp.client import InProcessMCPClient, MCPClient
from ceap.mcp.server import MCPServerDefinition
from ceap.rag.retrieval import KnowledgeBase, build_knowledge_base


def build_default_servers(
    store: DatasetStore | None = None,
    knowledge: KnowledgeBase | None = None,
    history: HistoricalStore | None = None,
) -> dict[str, MCPServerDefinition]:
    from ceap.mcp.alpha.server import build_server as alpha
    from ceap.mcp.backtest.server import build_server as backtest
    from ceap.mcp.engineering.server import build_server as engineering
    from ceap.mcp.execution.server import build_server as execution
    from ceap.mcp.knowledge.server import build_server as knowledge_server
    from ceap.mcp.market_data.server import build_server as market_data
    from ceap.mcp.research_data.server import build_server as research_data
    from ceap.mcp.risk.server import build_server as risk

    store = store or DatasetStore()
    knowledge = knowledge or build_knowledge_base()
    history = history or HistoricalStore()
    servers = [
        market_data(store),
        execution(store, history=history),
        risk(store, history),
        engineering(store),
        knowledge_server(knowledge),
        research_data(history),
        alpha(history),
        backtest(history),
    ]
    return {s.name: s for s in servers}


def build_in_process_client(
    store: DatasetStore | None = None,
    knowledge: KnowledgeBase | None = None,
    history: HistoricalStore | None = None,
) -> InProcessMCPClient:
    return InProcessMCPClient(build_default_servers(store, knowledge, history))


async def build_tool_registry(client: MCPClient, servers: list[str] | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    for server in servers or client.servers():
        for descriptor in await client.discover_tools(server):
            registry.register(MCPToolAdapter(client, server, descriptor))
    return registry
