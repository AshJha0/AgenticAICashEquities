"""Backtest MCP server: event-driven daily backtests with a cost model and a walk-forward split."""

from __future__ import annotations

from typing import Any

from ceap.analytics.backtest import run_backtest as _run_backtest
from ceap.data.historical import HistoricalStore
from ceap.domain.common import to_jsonable
from ceap.domain.research import DEFAULT_GROSS_NOTIONAL
from ceap.mcp.common import DEFAULT_RESEARCH_DATASET, select_history
from ceap.mcp.server import MCPServerDefinition


def build_server(history: HistoricalStore) -> MCPServerDefinition:
    server = MCPServerDefinition(
        "backtest",
        "Event-driven daily backtest of a signal portfolio with a spread-plus-impact transaction-cost model and "
        "a walk-forward in-sample / out-of-sample split; every statistic is deterministic.",
    )

    @server.tool(
        "Backtest the signal's quantile portfolio: rebalance every rebalance_days, charge half-spread plus "
        "square-root impact on ADV participation, and report CAGR, volatility, Sharpe, Sortino, drawdown, "
        "turnover, hit rate, gross-vs-net cost drag and P&L concentration per period."
    )
    async def run_backtest(
        signal: str,
        dataset: str = DEFAULT_RESEARCH_DATASET,
        start: str | None = None,
        end: str | None = None,
        in_sample_end: str | None = None,
        rebalance_days: int | None = None,
        long_short: bool = True,
        cost_multiplier: float = 1.0,
        gross_notional: float = DEFAULT_GROSS_NOTIONAL,
        weighting: str = "equal",
    ) -> dict[str, Any]:
        ds = select_history(history, dataset)
        result = _run_backtest(
            ds,
            signal,
            start,
            end,
            in_sample_end,
            rebalance_days,
            long_short,
            cost_multiplier,
            gross_notional,
            weighting,
        )
        return to_jsonable(result)

    return server
