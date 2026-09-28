"""Alpha MCP server: the signal library and deterministic cross-sectional signal statistics."""

from __future__ import annotations

from typing import Any

from ceap.analytics.signal_statistics import evaluate_signal as _evaluate_signal
from ceap.analytics.signals import SIGNALS
from ceap.data.historical import HistoricalStore
from ceap.domain.common import to_jsonable
from ceap.mcp.common import DEFAULT_RESEARCH_DATASET, select_history
from ceap.mcp.server import MCPServerDefinition


def build_server(history: HistoricalStore) -> MCPServerDefinition:
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

    return server
