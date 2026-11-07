"""Backtest MCP server: daily backtests under t+1 execution, with costs, financing and a trial ledger."""

from __future__ import annotations

import math
from typing import Any

from ceap.analytics.backtest import simulate_backtest
from ceap.analytics.profitability import capacity_sweep, json_safe, profitability_from_run
from ceap.analytics.trial_ledger import TrialLedger
from ceap.data.historical import HistoricalStore
from ceap.domain.common import to_jsonable
from ceap.domain.research import (
    DEFAULT_EXECUTION_LAG_DAYS,
    DEFAULT_FILL,
    DEFAULT_GROSS_NOTIONAL,
    SEARCH_TOP_FRACTION,
    validate_choice,
)
from ceap.mcp.common import DEFAULT_RESEARCH_DATASET, select_history
from ceap.mcp.server import MCPServerDefinition


def build_server(history: HistoricalStore, ledger: TrialLedger | None = None) -> MCPServerDefinition:
    trials = ledger if ledger is not None else TrialLedger()
    server = MCPServerDefinition(
        "backtest",
        "Daily backtest of a signal portfolio filled at the next close (t+1), with weight drift, a spread-plus-"
        "impact cost model, borrow and financing, Reg SHO and participation caps, delisting bounds, a capacity "
        "sweep and a server-side trial ledger; every statistic is deterministic.",
    )

    @server.tool(
        "Backtest the signal's quantile portfolio from the enumerated search space: decide at the close, fill "
        "execution_lag_days later at the close or open, drift weights between fills, charge half-spread plus "
        "square-root impact, borrow by liquidity tier and financing; report per-period Sharpe on excess returns, "
        "costs by line, capped and blocked trades, delisting bounds, the profitability table with a capacity "
        "sweep, and this run's trial-ledger record (the trial count is read from the ledger, never supplied)."
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
        execution_lag_days: int = DEFAULT_EXECUTION_LAG_DAYS,
        fill: str = DEFAULT_FILL,
        capacity: bool = True,
    ) -> dict[str, Any]:
        ds = select_history(history, dataset)
        from ceap.analytics.signals import get_signal

        rebalance = int(get_signal(signal).default_rebalance_days if rebalance_days is None else rebalance_days)
        validate_choice(signal, rebalance, long_short, weighting)
        kwargs: dict[str, Any] = {
            "start": start, "end": end, "in_sample_end": in_sample_end, "rebalance_days": rebalance,
            "long_short": long_short, "cost_multiplier": cost_multiplier, "weighting": weighting,
            "execution_lag_days": execution_lag_days, "fill": fill, "top_fraction": SEARCH_TOP_FRACTION,
        }
        run = simulate_backtest(ds, signal, gross_notional=gross_notional, **kwargs)
        ins = run.result.period("in_sample")
        sharpe_daily = ins.sharpe / math.sqrt(252) if ins is not None and math.isfinite(ins.sharpe) else float("nan")
        params = {"signal": signal, "gross_notional": gross_notional, **kwargs}
        summary = trials.record(ds.dataset_hash, params, sharpe_daily)
        sweep = capacity_sweep(ds, signal, **kwargs) if capacity else None
        table = json_safe(profitability_from_run(run, ds, sweep))
        from dataclasses import replace

        result = replace(run.result, profitability=table, trial=summary.to_dict())
        return to_jsonable(result)

    @server.tool("Read the trial ledger for a dataset: how many distinct configurations have been backtested.")
    async def trial_count(dataset: str = DEFAULT_RESEARCH_DATASET) -> dict[str, Any]:
        ds = select_history(history, dataset)
        return to_jsonable(trials.summary(ds.dataset_hash))

    return server
