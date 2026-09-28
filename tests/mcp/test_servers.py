"""MCP servers: discovery, invocation, error handling, FastMCP export, stdio transport."""

from __future__ import annotations

import shutil
import sys
from datetime import UTC, datetime, timedelta

import pytest

from ceap.domain.tools import ToolExecutionContext, ToolRequest, ToolStatus
from ceap.mcp.client import InProcessMCPClient, MCPError, StdioMCPClient, StdioServerSpec
from ceap.mcp.registry import build_default_servers, build_tool_registry
from ceap.mcp.server import MCPServerDefinition, schema_from_signature

WIN = {"symbol": "AAPL", "start": "2026-09-18T14:00:00", "end": "2026-09-18T15:00:00"}
BASE = {"symbol": "AAPL", "start": "2026-09-18T13:00:00", "end": "2026-09-18T14:00:00"}


async def test_discovery_exposes_expected_tools(mcp_client):
    assert mcp_client.servers() == [
        "alpha",
        "backtest",
        "engineering",
        "execution",
        "knowledge",
        "market_data",
        "research_data",
        "risk",
    ]
    market = {t["name"] for t in await mcp_client.discover_tools("market_data")}
    assert {"get_quote", "get_order_book", "get_trades", "get_market_statistics"} <= market
    execution = {t["name"] for t in await mcp_client.discover_tools("execution")}
    assert {
        "get_parent_orders",
        "get_child_orders",
        "get_executions",
        "get_execution_metrics",
        "get_venue_statistics",
    } <= execution
    risk = {t["name"] for t in await mcp_client.discover_tools("risk")}
    assert {"get_position", "get_exposure", "check_limit", "calculate_stress"} <= risk
    eng = {t["name"] for t in await mcp_client.discover_tools("engineering")}
    assert {"search_logs", "get_service_metrics", "get_deployments", "get_latency_metrics"} <= eng
    quote = next(t for t in await mcp_client.discover_tools("market_data") if t["name"] == "get_quote")
    assert quote["inputSchema"]["required"] == ["symbol", "timestamp"]
    assert quote["annotations"]["readOnlyHint"] is True


async def test_every_tool_is_read_only_and_low_or_medium_risk(registry):
    """Every analysis tool is read-only; the one execution hook is explicitly gated."""
    for meta in registry.list():
        if meta.id == "execution.stage_orders":
            assert not meta.read_only and meta.risk_level.value == "HIGH"
            assert meta.required_capabilities == frozenset({"trading:execute"})
            continue
        assert meta.read_only, meta.id
        assert meta.risk_level.value in ("LOW", "MEDIUM"), meta.id


async def test_market_data_tools(mcp_client):
    q = await mcp_client.invoke(
        "market_data", "get_quote", {"symbol": "AAPL", "timestamp": "2026-09-18T14:30:00"}
    )
    assert q["quote"]["bid"] < q["quote"]["ask"] and q["spread_bps"] > 0
    qs = await mcp_client.invoke("market_data", "get_quotes", {**WIN, "max_points": 100})
    assert qs["count"] == 3600 and qs["returned"] <= 100
    book = await mcp_client.invoke(
        "market_data", "get_order_book", {"symbol": "AAPL", "timestamp": "2026-09-18T14:30:05"}
    )
    assert len(book["bids"]) == 5 and book["displayed_depth"] > 0
    trades = await mcp_client.invoke("market_data", "get_trades", {**WIN, "limit": 10})
    assert trades["returned"] == 10 and trades["count"] > 1000 and trades["total_volume"] > 0
    stats = await mcp_client.invoke("market_data", "get_market_statistics", WIN)
    assert stats["quote_count"] == 3600 and stats["traded_volume"] > 0 and "XNAS" in stats["venue_volume"]
    ref = await mcp_client.invoke("market_data", "get_reference_data", {"symbol": "AAPL"})
    assert ref["found"] and ref["tick_size"] == 0.01
    assert (await mcp_client.invoke("market_data", "get_reference_data", {"symbol": "ZZZ"}))["found"] is False
    cov = await mcp_client.invoke("market_data", "get_coverage", {})
    assert cov["symbols"] == ["AAPL"]


async def test_execution_tools_consistent(mcp_client):
    parents = await mcp_client.invoke("execution", "get_parent_orders", WIN)
    assert parents["count"] == 1 and parents["items"][0]["side"] == "BUY"
    pid = parents["items"][0]["order_id"]
    children = await mcp_client.invoke("execution", "get_child_orders", {**WIN, "parent_order_id": pid})
    fills = await mcp_client.invoke(
        "execution", "get_executions", {**WIN, "parent_order_id": pid, "limit": 5000}
    )
    metrics = await mcp_client.invoke("execution", "get_execution_metrics", WIN)
    assert children["count"] == metrics["child_order_count"]
    assert fills["executed_quantity"] == metrics["executed_quantity"]
    venues = await mcp_client.invoke("execution", "get_venue_statistics", WIN)
    assert set(venues["venues"]) == set(metrics["venue_statistics"])
    cfg = await mcp_client.invoke("execution", "get_strategy_configuration", {"symbol": "AAPL"})
    assert cfg["max_participation_rate"] == 0.20


async def test_risk_and_engineering_tools(mcp_client):
    pos = await mcp_client.invoke("risk", "get_position", {"symbol": "AAPL"})
    assert pos["found"] and pos["quantity_as_of"] > pos["start_of_day_quantity"]
    exp = await mcp_client.invoke("risk", "get_exposure", {"symbol": "AAPL"})
    assert exp["gross_exposure"] > 0 and 0 < exp["notional_utilisation"] < 1
    lim = await mcp_client.invoke(
        "risk", "check_limit", {"symbol": "AAPL", "order_quantity": 600_000, "participation_rate": 0.4}
    )
    assert lim["any_breached"] and {c["limit_name"] for c in lim["checks"] if c["breached"]} == {
        "max_order_quantity",
        "max_position",
        "max_participation_rate",
    }
    stress = await mcp_client.invoke("risk", "calculate_stress", {"symbol": "AAPL", "shock_bps": -200})
    assert stress["pnl_impact"] < 0
    lat = await mcp_client.invoke(
        "engineering",
        "get_latency_metrics",
        {"service": "order-gateway", **{k: v for k, v in WIN.items() if k != "symbol"}, "dataset": "T06"},
    )
    assert lat["latency_ratio"] > 5 and lat["slo_breached"]
    logs = await mcp_client.invoke(
        "engineering",
        "search_logs",
        {"start": WIN["start"], "end": WIN["end"], "level": "ERROR", "dataset": "T06"},
    )
    assert logs["count"] > 0 and "ERROR" in logs["level_counts"]
    deps = await mcp_client.invoke(
        "engineering", "get_deployments", {"start": BASE["start"], "end": WIN["end"], "dataset": "T06"}
    )
    assert deps["count"] == 1 and deps["items"][0]["service"] == "smart-order-router"
    svc = await mcp_client.invoke(
        "engineering",
        "get_service_metrics",
        {
            "service": "order-gateway",
            "start": WIN["start"],
            "end": WIN["end"],
            "metric": "reject_count",
            "dataset": "T06",
        },
    )
    assert svc["summary"]["reject_count"]["sum"] > 0
    assert len((await mcp_client.invoke("engineering", "get_services", {}))["services"]) == 4


async def test_knowledge_tools(mcp_client):
    docs = await mcp_client.invoke("knowledge", "list_documents", {})
    assert docs["count"] == 8
    hits = await mcp_client.invoke(
        "knowledge", "search_documents", {"query": "maximum participation rate VWAP", "k": 2}
    )
    assert hits["count"] == 2 and hits["items"][0]["score"] > 0
    doc = await mcp_client.invoke(
        "knowledge", "get_document", {"document_id": hits["items"][0]["document_id"]}
    )
    assert doc["found"] and len(doc["text"]) > 100


async def test_errors_are_mcp_errors(mcp_client):
    with pytest.raises(MCPError):
        await mcp_client.invoke("nope", "x", {})
    with pytest.raises(MCPError):
        await mcp_client.invoke("market_data", "no_such_tool", {})
    with pytest.raises(MCPError):
        await mcp_client.invoke("market_data", "get_quote", {"symbol": "AAPL", "bogus": 1})


async def test_adapter_wraps_results_and_errors(registry):
    ctx = ToolExecutionContext("t", "x", "s", datetime.now(UTC) + timedelta(minutes=1), "me")
    ok = await registry.get("execution.get_execution_metrics").execute(
        ToolRequest("execution.get_execution_metrics", WIN), ctx
    )
    assert ok.ok and ok.evidence[0].type.value == "CALCULATION" and ok.evidence[0].attributes["digest"]
    bad = await registry.get("market_data.get_quote").execute(
        ToolRequest("market_data.get_quote", {"symbol": "AAPL", "timestamp": "garbage"}), ctx
    )
    assert bad.status is ToolStatus.ERROR and "isoformat" in (bad.error or "")


def test_schema_from_signature():
    async def fn(symbol: str, k: int = 4, when: str | None = None) -> dict:
        return {}

    schema = schema_from_signature(fn)
    assert schema["required"] == ["symbol"] and schema["properties"]["k"] == {"type": "integer", "default": 4}
    server = MCPServerDefinition("t", "d")
    with pytest.raises(TypeError):
        server.tool("sync not allowed")(lambda: None)


async def test_fastmcp_export_lists_tools_with_annotations(store):
    server = build_default_servers(store)["risk"].to_fastmcp()
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert {"get_position", "check_limit", "calculate_stress"} <= names
    stress = next(t for t in tools if t.name == "calculate_stress")
    assert stress.annotations.readOnlyHint is True and stress.meta["riskLevel"] == "MEDIUM"
    result = await server.call_tool("get_position", {"symbol": "AAPL"})
    assert result


@pytest.mark.skipif(shutil.which(sys.executable) is None, reason="python executable not found")
async def test_stdio_transport_round_trip():
    """Spawn the real market-data server as a subprocess and talk MCP over stdio."""
    client = StdioMCPClient([StdioServerSpec("market_data", sys.executable, ["-m", "ceap.mcp.market_data"])])
    try:
        tools = await client.discover_tools("market_data")
        assert {t["name"] for t in tools} >= {"get_quote", "get_market_statistics"}
        ref = await client.invoke("market_data", "get_reference_data", {"symbol": "MSFT"})
        assert ref["found"] and ref["symbol"] == "MSFT"
    finally:
        await client.close()


async def test_in_process_client_register_and_registry_build():
    client = InProcessMCPClient()
    server = MCPServerDefinition("demo", "d")

    @server.tool("echo")
    async def echo(x: str) -> dict:
        return {"x": x}

    client.register(server)
    reg = await build_tool_registry(client)
    assert reg.ids() == ["demo.echo"]
