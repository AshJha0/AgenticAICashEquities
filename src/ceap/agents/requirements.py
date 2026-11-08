"""The data each specialist consumes, declared once (PLAN 3B.1).

Agents no longer fetch their own data: they read what the plan fetched (``BaseAgent.fetched``). The
harness reads this table after planning and appends any tool step a *chosen* agent needs that the
plan did not already schedule, so the plan's choice of agents decides which tools run, and every
fetch goes through the plan's validation and policy pre-check.

The arguments here must equal, key for key, the ones each agent passes to ``fetched``; a test runs
every agent after the harness has satisfied this table and asserts the agent made no tool call of
its own. Calls whose arguments depend on fetched data (the risk agent's limit check) stay as
``ensure`` and are not declared here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ceap.domain.research import research_tool_arguments
from ceap.domain.tools import ToolRequest

Requirement = tuple[str, dict[str, Any]]


def _win(i: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "symbol": i["symbol"],
        "start": i["window_start"],
        "end": i["window_end"],
        "dataset": i.get("dataset"),
    }


def _base(i: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "symbol": i["symbol"],
        "start": i["baseline_start"],
        "end": i["baseline_end"],
        "dataset": i.get("dataset"),
    }


def _market(i: Mapping[str, Any]) -> list[Requirement]:
    return [
        ("market_data.get_market_statistics", _win(i)),
        ("market_data.get_market_statistics", _base(i)),
        ("market_data.get_order_book_statistics", _win(i)),
        ("market_data.get_order_book_statistics", _base(i)),
    ]


def _execution(i: Mapping[str, Any]) -> list[Requirement]:
    return [
        ("execution.get_execution_metrics", _win(i)),
        ("execution.get_execution_metrics", _base(i)),
        ("execution.get_venue_statistics", _win(i)),
        ("execution.get_parent_orders", _win(i)),
    ]


def _quant(i: Mapping[str, Any]) -> list[Requirement]:
    return [
        ("execution.get_execution_metrics", _win(i)),
        ("execution.get_execution_metrics", _base(i)),
        ("market_data.get_market_statistics", _win(i)),
        ("market_data.get_market_statistics", _base(i)),
        ("market_data.get_order_book_statistics", _win(i)),
        ("market_data.get_order_book_statistics", _base(i)),
    ]


def _risk(i: Mapping[str, Any]) -> list[Requirement]:
    return [
        ("execution.get_execution_metrics", _win(i)),
        ("risk.get_exposure", {"symbol": i["symbol"], "as_of": i["window_end"], "dataset": i.get("dataset")}),
    ]


def _engineering(i: Mapping[str, Any]) -> list[Requirement]:
    d = i.get("dataset")
    return [
        (
            "engineering.get_latency_metrics",
            {
                "service": "order-gateway",
                "start": i["window_start"],
                "end": i["window_end"],
                "baseline_start": i["baseline_start"],
                "baseline_end": i["baseline_end"],
                "dataset": d,
            },
        ),
        ("engineering.get_deployments", {"start": i["baseline_start"], "end": i["window_end"], "dataset": d}),
        (
            "engineering.search_logs",
            {"start": i["window_start"], "end": i["window_end"], "level": "ERROR", "dataset": d},
        ),
        (
            "engineering.search_logs",
            {"start": i["window_start"], "end": i["window_end"], "level": "WARN", "dataset": d},
        ),
        (
            "engineering.get_service_metrics",
            {
                "service": "market-data-adapter",
                "start": i["window_start"],
                "end": i["window_end"],
                "metric": "feed_gap_count",
                "dataset": d,
            },
        ),
    ]


def _research(key: str, tool_id: str) -> Callable[[Mapping[str, Any]], list[Requirement]]:
    def build(i: Mapping[str, Any]) -> list[Requirement]:
        return [(tool_id, research_tool_arguments(dict(i))[key])]

    return build


def _portfolio(i: Mapping[str, Any]) -> list[Requirement]:
    args = research_tool_arguments(dict(i))["portfolio"]
    return [
        ("risk.get_portfolio_exposure", args),
        ("risk.check_portfolio_limits", args),
        ("risk.calculate_portfolio_stress", args),
    ]


REQUIREMENTS: dict[str, Callable[[Mapping[str, Any]], list[Requirement]]] = {
    "market": _market,
    "execution": _execution,
    "quant": _quant,
    "risk": _risk,
    "engineering": _engineering,
    "research": _research("universe_summary", "research_data.get_universe_summary"),
    "alpha": _research("evaluate_signal", "alpha.evaluate_signal"),
    "backtest": _research("run_backtest", "backtest.run_backtest"),
    "portfolio_risk": _portfolio,
}


def requirements_for(agent_id: str, task_input: Mapping[str, Any]) -> list[ToolRequest]:
    """The tool calls ``agent_id`` reads, for this task (empty for agents that read no tool data)."""
    build = REQUIREMENTS.get(agent_id)
    if build is None:
        return []
    try:
        return [ToolRequest(tool_id=t, arguments=a) for t, a in build(task_input)]
    except KeyError:  # a task without the inputs this agent needs: the agent will fail and say so
        return []
