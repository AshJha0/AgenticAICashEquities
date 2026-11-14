"""Build the domain ``ToolRegistry`` from MCP servers (discovery -> adapters)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ceap.data.historical import HistoricalStore
from ceap.data.repositories import DatasetStore
from ceap.domain.tools import ToolRegistry
from ceap.mcp.adapter import MCPToolAdapter, catalogue_hash, check_catalogue
from ceap.mcp.client import InProcessMCPClient, MCPClient
from ceap.mcp.server import MCPServerDefinition
from ceap.rag.retrieval import KnowledgeBase, build_knowledge_base

if TYPE_CHECKING:
    from ceap.analytics.trial_ledger import TrialLedger
    from ceap.persistence.db import Database


def build_default_servers(
    store: DatasetStore | None = None,
    knowledge: KnowledgeBase | None = None,
    history: HistoricalStore | None = None,
    db: Database | None = None,
    trials: TrialLedger | None = None,
) -> dict[str, MCPServerDefinition]:
    """``db``: when given, staged paper order sets persist there and staging honours its kill switch.
    ``trials``: the trial ledger (default: the persistent one, Q7); evaluation passes an in-memory ledger."""
    from ceap.mcp.alpha.server import build_server as alpha
    from ceap.mcp.backtest.server import build_server as backtest
    from ceap.mcp.engineering.server import build_server as engineering
    from ceap.mcp.execution.server import build_server as execution
    from ceap.mcp.execution.staging import StagedOrderBook
    from ceap.mcp.knowledge.server import build_server as knowledge_server
    from ceap.mcp.market_data.server import build_server as market_data
    from ceap.mcp.research_data.server import build_server as research_data
    from ceap.mcp.risk.server import build_server as risk

    store = store or DatasetStore()
    knowledge = knowledge or build_knowledge_base()
    history = history or HistoricalStore()
    from ceap.analytics.trial_ledger import TrialLedger

    # one ledger: combination trials and backtest trials deflate together (2B.2)
    trials = trials if trials is not None else TrialLedger()
    servers = [
        market_data(store),
        execution(store, history=history, staged=StagedOrderBook(db=db) if db is not None else None),
        risk(store, history),
        engineering(store),
        knowledge_server(knowledge),
        research_data(history),
        alpha(history, ledger=trials),
        backtest(history, ledger=trials),
    ]
    return {s.name: s for s in servers}


def build_in_process_client(
    store: DatasetStore | None = None,
    knowledge: KnowledgeBase | None = None,
    history: HistoricalStore | None = None,
    db: Database | None = None,
    trials: TrialLedger | None = None,
) -> InProcessMCPClient:
    return InProcessMCPClient(build_default_servers(store, knowledge, history, db, trials))


#: ``server=<sha256>,...``: catalogue hashes a deployment has reviewed; a mismatch refuses start-up
CATALOGUE_PINS_ENV = "CEAP_MCP_CATALOGUE_PINS"


class PinnedToolRegistry(ToolRegistry):
    """A registry that remembers each server's catalogue hash from discovery (A5)."""

    def __init__(self) -> None:
        super().__init__()
        self.catalogue_hashes: dict[str, str] = {}

    async def verify_catalogue(self, client: MCPClient) -> None:
        """Re-discover every server and raise :class:`CatalogueDriftError` if any catalogue changed."""
        for server, recorded in self.catalogue_hashes.items():
            check_catalogue(server, catalogue_hash(await client.discover_tools(server)), recorded)


def catalogue_pins_from_env() -> dict[str, str]:
    raw = os.getenv(CATALOGUE_PINS_ENV) or ""
    pins: dict[str, str] = {}
    for entry in (e.strip() for e in raw.split(",")):
        if not entry:
            continue
        server, sep, digest = entry.partition("=")
        if not sep or len(digest.strip()) != 64:
            raise ValueError(f"{CATALOGUE_PINS_ENV} entries must be server=<64 hex sha256>")
        pins[server.strip()] = digest.strip().lower()
    return pins


async def build_tool_registry(
    client: MCPClient,
    servers: list[str] | None = None,
    *,
    pins: Mapping[str, str] | None = None,
) -> PinnedToolRegistry:
    """Discover tools and wrap them as adapters. Each server's catalogue is hashed at start-up and
    checked against ``pins`` (default: ``CEAP_MCP_CATALOGUE_PINS``); a mismatch refuses start-up."""
    pins = catalogue_pins_from_env() if pins is None else pins
    registry = PinnedToolRegistry()
    for server in servers or client.servers():
        descriptors = await client.discover_tools(server)
        digest = catalogue_hash(descriptors)
        check_catalogue(server, digest, pins.get(server))
        registry.catalogue_hashes[server] = digest
        for descriptor in descriptors:
            registry.register(MCPToolAdapter(client, server, descriptor))
    return registry
