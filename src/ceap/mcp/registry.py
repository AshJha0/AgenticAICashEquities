"""Build the domain ``ToolRegistry`` from MCP servers (discovery -> adapters)."""

from __future__ import annotations

from ceap.data.repositories import DatasetStore
from ceap.domain.tools import ToolRegistry
from ceap.mcp.adapter import MCPToolAdapter
from ceap.mcp.client import InProcessMCPClient, MCPClient
from ceap.mcp.server import MCPServerDefinition
from ceap.rag.retrieval import KnowledgeBase, build_knowledge_base


def build_default_servers(
    store: DatasetStore | None = None, knowledge: KnowledgeBase | None = None
) -> dict[str, MCPServerDefinition]:
    from ceap.mcp.engineering.server import build_server as engineering
    from ceap.mcp.execution.server import build_server as execution
    from ceap.mcp.knowledge.server import build_server as knowledge_server
    from ceap.mcp.market_data.server import build_server as market_data
    from ceap.mcp.risk.server import build_server as risk

    store = store or DatasetStore()
    knowledge = knowledge or build_knowledge_base()
    servers = [
        market_data(store),
        execution(store),
        risk(store),
        engineering(store),
        knowledge_server(knowledge),
    ]
    return {s.name: s for s in servers}


def build_in_process_client(
    store: DatasetStore | None = None, knowledge: KnowledgeBase | None = None
) -> InProcessMCPClient:
    return InProcessMCPClient(build_default_servers(store, knowledge))


async def build_tool_registry(client: MCPClient, servers: list[str] | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    for server in servers or client.servers():
        for descriptor in await client.discover_tools(server):
            registry.register(MCPToolAdapter(client, server, descriptor))
    return registry
