"""Backtest MCP server: daily backtests under t+1 execution, with costs, financing and a trial ledger.

0.10 (Q4, finding R-17): the trial record also carries the search diagnostics of the full 24-configuration
search on the same dataset and window (:mod:`ceap.analytics.search_diagnostics`): PBO by CSCV and the
effective trial count, written as ``n_trials_effective`` for the deflated Sharpe. They do not depend on
which configuration was asked for, so they are cached per dataset and window. The tool's description and
schema are unchanged (the reviewed catalogue hash stays valid).
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import replace
from typing import Any

from ceap.analytics.backtest import simulate_backtest
from ceap.analytics.profitability import capacity_sweep, json_safe, profitability_from_run
from ceap.analytics.trial_ledger import TrialLedger
from ceap.data.historical import HistoricalDataset, HistoricalStore
from ceap.domain.common import to_jsonable
from ceap.domain.research import (
    DEFAULT_EXECUTION_LAG_DAYS,
    DEFAULT_FILL,
    DEFAULT_GROSS_NOTIONAL,
    SEARCH_TOP_FRACTION,
    validate_choice,
)
from ceap.mcp.common import DEFAULT_RESEARCH_DATASET, select_history
from ceap.mcp.cpu_pool import run_on_history
from ceap.mcp.server import MCPServerDefinition

_SEARCH_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_SEARCH_KEYS = ("start", "end", "in_sample_end", "cost_multiplier", "execution_lag_days", "fill")


def _search(ds: HistoricalDataset, gross_notional: float, kwargs: dict[str, Any]) -> dict[str, Any]:
    from ceap.analytics.search_diagnostics import search_diagnostics

    window = {k: kwargs[k] for k in _SEARCH_KEYS if k in kwargs}
    key = (ds.dataset_hash, gross_notional, *sorted(window.items()))
    if key not in _SEARCH_CACHE:
        _SEARCH_CACHE[key] = search_diagnostics(ds, gross_notional=gross_notional, **window)
    return _SEARCH_CACHE[key]


def _per_day(sharpe_ann: float | None) -> float:
    return sharpe_ann / math.sqrt(252) if sharpe_ann is not None and math.isfinite(sharpe_ann) else float("nan")


def _backtest_compute(
    ds: HistoricalDataset, signal: str, gross_notional: float, kwargs: dict[str, Any], capacity: bool
) -> tuple[dict[str, Any], float, float, dict[str, Any]]:
    """The CPU-heavy part of ``run_backtest``: pure, picklable, safe to run in a worker process. Returns the
    payload, the in-sample per-day Sharpe before and after costs (the ledger deflates the gross one, Q2) and
    the search diagnostics (Q4)."""
    run = simulate_backtest(ds, signal, gross_notional=gross_notional, **kwargs)
    ins = run.result.period("in_sample")
    gross = _per_day(ins.gross_sharpe if ins is not None else None)
    net = _per_day(ins.sharpe if ins is not None else None)
    sweep = capacity_sweep(ds, signal, **kwargs) if capacity else None
    table = json_safe(profitability_from_run(run, ds, sweep))
    payload = to_jsonable(replace(run.result, profitability=table, trial={}))
    return payload, gross, net, _search(ds, gross_notional, kwargs)


def build_server(history: HistoricalStore, ledger: TrialLedger | None = None) -> MCPServerDefinition:
    trials = ledger if ledger is not None else TrialLedger()
    server = MCPServerDefinition(
        "backtest",
        "Daily backtest of a signal portfolio filled at the next close (t+1), with weight drift, a spread-plus-"
        "impact cost model, borrow and financing, Reg SHO and participation caps, delisting bounds, a capacity "
        "sweep and a server-side trial ledger; every statistic is deterministic.",
    )

    @server.tool(
        cpu_bound=True,
        description="Backtest the signal's quantile portfolio from the enumerated search space: decide at the "
        "close, fill execution_lag_days later at the close or open, drift weights between fills, charge half-spread plus "
        "square-root impact, borrow by liquidity tier and financing; report per-period Sharpe on excess returns, "
        "costs by line, capped and blocked trades, delisting bounds, the profitability table with a capacity "
        "sweep, and this run's trial-ledger record (the trial count is read from the ledger, never supplied).",
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
        ds = await asyncio.to_thread(select_history, history, dataset)  # a cold dataset takes seconds
        from ceap.analytics.signals import get_signal

        rebalance = int(get_signal(signal).default_rebalance_days if rebalance_days is None else rebalance_days)
        validate_choice(signal, rebalance, long_short, weighting)
        kwargs: dict[str, Any] = {
            "start": start, "end": end, "in_sample_end": in_sample_end, "rebalance_days": rebalance,
            "long_short": long_short, "cost_multiplier": cost_multiplier, "weighting": weighting,
            "execution_lag_days": execution_lag_days, "fill": fill, "top_fraction": SEARCH_TOP_FRACTION,
        }
        params = {"signal": signal, "gross_notional": gross_notional, **kwargs}
        payload, gross, net, search = await run_on_history(
            _backtest_compute, ds, signal, gross_notional, kwargs, capacity
        )
        summary = trials.record(ds.dataset_hash, params, gross, net)
        payload["trial"] = to_jsonable(
            {**summary.to_dict(), "n_trials_effective": search.get("n_trials_effective"), "search": search}
        )
        return payload

    @server.tool("Read the trial ledger for a dataset: how many distinct configurations have been backtested.")
    async def trial_count(dataset: str = DEFAULT_RESEARCH_DATASET) -> dict[str, Any]:
        ds = select_history(history, dataset)
        return to_jsonable(trials.summary(ds.dataset_hash))

    return server
