"""Alpha MCP server: the signal library and deterministic cross-sectional signal statistics."""

from __future__ import annotations

from typing import Any

from ceap.analytics.ml_signals import DEFAULT_HORIZON
from ceap.analytics.ml_signals import combine_signals as _combine_signals
from ceap.analytics.signal_statistics import evaluate_signal as _evaluate_signal
from ceap.analytics.signals import SIGNALS
from ceap.analytics.trial_ledger import TrialLedger
from ceap.data.historical import HistoricalStore
from ceap.domain.common import to_jsonable
from ceap.mcp.common import DEFAULT_RESEARCH_DATASET, select_history
from ceap.mcp.server import MCPServerDefinition


def build_server(history: HistoricalStore, ledger: TrialLedger | None = None) -> MCPServerDefinition:
    trials = ledger if ledger is not None else TrialLedger()
    server = MCPServerDefinition(
        "alpha",
        "Deterministic alpha-signal analytics: the signal library and cross-sectional rank-IC statistics "
        "(t-stat, information ratio, turnover, decay, quantile spread) for in-sample and out-of-sample periods.",
    )

    @server.tool("List the signals available for research, with their look-back and default horizons.")
    async def list_signals() -> dict[str, Any]:
        return {"count": len(SIGNALS), "items": [spec.describe() for spec in SIGNALS.values()]}

    @server.tool(
        "Compute rank-IC statistics of a signal against forward returns over the in-sample, out-of-sample and "
        "full periods: mean IC, IC t-statistic, information ratio, hit rate, turnover, decay and quantile spread."
    )
    async def evaluate_signal(
        signal: str,
        dataset: str = DEFAULT_RESEARCH_DATASET,
        start: str | None = None,
        end: str | None = None,
        in_sample_end: str | None = None,
        horizon_days: int | None = None,
        rebalance_days: int | None = None,
    ) -> dict[str, Any]:
        ds = select_history(history, dataset)
        stats = _evaluate_signal(ds, signal, start, end, in_sample_end, horizon_days, rebalance_days)
        return to_jsonable(stats)

    @server.tool(
        "Combine the three library signals with ridge regression: penalty chosen by purged and embargoed "
        "walk-forward cross-validation on the in-sample period (every penalty tried is a trial-ledger entry), "
        "then the out-of-sample deflated Sharpe ratio of the combination next to each single signal. Gross of "
        "costs; fitted to synthetic data only."
    )
    async def combine_signals(
        dataset: str = DEFAULT_RESEARCH_DATASET, horizon_days: int = DEFAULT_HORIZON
    ) -> dict[str, Any]:
        ds = select_history(history, dataset)
        return to_jsonable(_combine_signals(ds, ledger=trials, horizon=int(horizon_days)).to_dict())

    return server
