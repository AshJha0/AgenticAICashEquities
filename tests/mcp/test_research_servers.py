"""Stage 2 MCP servers: research data, alpha, backtest, portfolio risk and paper order staging."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ceap.domain.tools import ToolExecutionContext, ToolRequest
from ceap.mcp.client import MCPError

R01 = {"signal": "momentum_12_1", "dataset": "R01"}


def _no_nan(obj: Any) -> bool:
    if isinstance(obj, float):
        return not (math.isnan(obj) or math.isinf(obj))
    if isinstance(obj, dict):
        return all(_no_nan(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_no_nan(v) for v in obj)
    return True


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext("task", "run", "step", datetime.now(UTC) + timedelta(minutes=1), "p")


async def test_discovery(mcp_client):
    research = {t["name"] for t in await mcp_client.discover_tools("research_data")}
    assert research == {"get_universe", "get_daily_bars", "get_universe_summary"}
    assert {t["name"] for t in await mcp_client.discover_tools("alpha")} == {"list_signals", "evaluate_signal"}
    assert {t["name"] for t in await mcp_client.discover_tools("backtest")} == {"run_backtest"}
    risk = {t["name"] for t in await mcp_client.discover_tools("risk")}
    assert {"get_portfolio_exposure", "check_portfolio_limits", "calculate_portfolio_stress"} <= risk
    execution = {t["name"]: t for t in await mcp_client.discover_tools("execution")}
    assert {"stage_orders", "get_staged_orders"} <= set(execution)
    ann = execution["stage_orders"]["annotations"]
    assert ann["readOnlyHint"] is False and ann["riskLevel"] == "HIGH"
    assert ann["requiredCapabilities"] == ["trading:execute"]
    assert execution["get_staged_orders"]["annotations"]["readOnlyHint"] is True


async def test_research_data_tools(mcp_client, history):
    ds = history.get("R01")
    universe = await mcp_client.invoke("research_data", "get_universe", {"dataset": "R01"})
    assert universe["count"] == 30 and {i["tier"] for i in universe["items"]} == {"A", "B", "C"}
    assert universe["start"] == ds.start and universe["in_sample_end"] == ds.in_sample_end
    bars = await mcp_client.invoke(
        "research_data",
        "get_daily_bars",
        {"symbol": "AAPL", "start": ds.start, "end": ds.end, "dataset": "R01", "max_points": 50},
    )
    assert bars["count"] == 756 and bars["returned"] <= 50 and bars["items"][0]["date"] == ds.start
    summary = await mcp_client.invoke("research_data", "get_universe_summary", {"dataset": "R01"})
    assert summary["coverage"]["complete"] and summary["coverage"]["trading_days"] == 756
    assert set(summary["periods"]) == {"in_sample", "out_of_sample", "full"}
    assert summary["periods"]["in_sample"]["end"] == ds.in_sample_end
    assert summary["tiers"] == {"A": 10, "B": 12, "C": 8} and _no_nan(summary)
    with pytest.raises(MCPError):
        await mcp_client.invoke("research_data", "get_daily_bars", {"symbol": "TSLA", "start": ds.start, "end": ds.end})


async def test_alpha_and_backtest_tools(mcp_client, history):
    ds = history.get("R01")
    signals = await mcp_client.invoke("alpha", "list_signals", {})
    assert signals["count"] == 3
    stats = await mcp_client.invoke(
        "alpha", "evaluate_signal", {**R01, "start": ds.start, "end": ds.end, "in_sample_end": ds.in_sample_end}
    )
    assert stats["periods"]["in_sample"]["ic_t_stat"] > 2.0 and stats["universe_size"] == 30 and _no_nan(stats)
    bt = await mcp_client.invoke(
        "backtest", "run_backtest", {**R01, "start": ds.start, "end": ds.end, "in_sample_end": ds.in_sample_end}
    )
    assert set(bt["periods"]) == {"in_sample", "out_of_sample", "full"}
    assert 2 <= len(bt["equity_curve"]) <= 24 and len(bt["top_contributors"]) == 5 and _no_nan(bt)
    assert bt["periods"]["out_of_sample"]["sharpe"] > 0.5
    with pytest.raises(MCPError):
        await mcp_client.invoke("alpha", "evaluate_signal", {"signal": "nope"})


async def test_portfolio_risk_tools(mcp_client, history):
    ds = history.get("R01")
    args = {**R01, "as_of": ds.end}
    exposure = await mcp_client.invoke("risk", "get_portfolio_exposure", args)
    assert len(exposure["positions"]) == 20 and exposure["gross"] == pytest.approx(1.0)
    assert exposure["as_of"] == ds.end and _no_nan(exposure)
    limits = await mcp_client.invoke("risk", "check_portfolio_limits", args)
    assert limits["any_breached"] is False and len(limits["checks"]) == 6
    stress = await mcp_client.invoke("risk", "calculate_portfolio_stress", {**args, "shock_bps": -500.0})
    assert stress["shock_bps"] == -500.0 and stress["worst_single_name"] < 0
    concentrated = await mcp_client.invoke(
        "risk", "check_portfolio_limits", {"signal": "momentum_12_1", "dataset": "R06", "as_of": history.get("R06").end}
    )
    assert concentrated["any_breached"] and len(concentrated["breached_symbols"]) >= 2


async def test_stage_orders_is_idempotent_and_recorded_as_order_evidence(mcp_client, registry, history):
    ds = history.get("R01")
    args = {**R01, "as_of": ds.end, "gross_notional": 50_000_000.0}
    first = await mcp_client.invoke("execution", "stage_orders", args)
    second = await mcp_client.invoke("execution", "stage_orders", args)
    assert first["staging_id"].startswith("STG-") and first["already_staged"] is False
    assert second["staging_id"] == first["staging_id"] and second["already_staged"] is True
    assert first["count"] == 20 and {o["side"] for o in first["orders"]} == {"BUY", "SELL"}
    assert all(o["account"] == "PAPER_RESEARCH" and o["strategy"] == "VWAP" for o in first["orders"])
    assert first["buy_notional"] > 0 and first["sell_notional"] > 0
    listed = await mcp_client.invoke("execution", "get_staged_orders", {"staging_id": first["staging_id"]})
    assert listed["found"] and listed["count"] == 20
    assert (await mcp_client.invoke("execution", "get_staged_orders", {"staging_id": "STG-nope"}))["found"] is False
    assert (await mcp_client.invoke("execution", "get_staged_orders", {}))["count"] >= 1

    tool = registry.get("execution.stage_orders")
    result = await tool.execute(ToolRequest("execution.stage_orders", args), _ctx())
    assert result.ok and result.evidence[0].type.value == "ORDER_DATA"
    assert result.evidence[0].id.startswith("ORDER-")
    alpha = await registry.get("alpha.evaluate_signal").execute(ToolRequest("alpha.evaluate_signal", R01), _ctx())
    assert alpha.evidence[0].id.startswith("SIG-")
    bt = await registry.get("backtest.run_backtest").execute(ToolRequest("backtest.run_backtest", R01), _ctx())
    assert bt.evidence[0].id.startswith("BT-")
