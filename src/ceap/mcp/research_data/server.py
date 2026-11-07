"""Research data MCP server: the daily universe history behind alpha research and backtests."""

from __future__ import annotations

from typing import Any

import numpy as np

from ceap.analytics.backtest import daily_returns
from ceap.data.historical import HistoricalDataset, HistoricalStore
from ceap.mcp.common import DEFAULT_RESEARCH_DATASET, downsample, select_history
from ceap.mcp.server import MCPServerDefinition

TRADING_DAYS_PER_YEAR = 252


def _period(ds: HistoricalDataset, market: np.ndarray, name: str, a: int, b: int) -> dict[str, Any]:
    r = market[a + 1 : b + 1] if b > a else np.array([])
    r = r[np.isfinite(r)]
    return {
        "name": name,
        "start": str(ds.dates[a]),
        "end": str(ds.dates[b]),
        "days": int(b - a + 1),
        "market_return_pct": round(float((np.prod(1.0 + r) - 1.0) * 100.0), 3) if r.size else None,
        "market_ann_vol_pct": round(float(r.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR) * 100.0), 3)
        if r.size > 1
        else None,
    }


def _first_finite(x: np.ndarray) -> float:
    v = x[np.isfinite(x)]
    return float(v[0]) if v.size else float("nan")


def build_server(history: HistoricalStore) -> MCPServerDefinition:

    server = MCPServerDefinition(
        "research_data",
        "Daily OHLCV history, liquidity and universe reference data for a research dataset "
        "(30-name synthetic universe, twenty years, locked walk-forward split, membership mask).",
    )

    @server.tool("Describe the research universe of a dataset: symbols, liquidity tiers, prices and average liquidity.")
    async def get_universe(dataset: str = DEFAULT_RESEARCH_DATASET) -> dict[str, Any]:
        ds = select_history(history, dataset)
        i0 = ds.start_index
        adv_notional = ds.adv_20 * ds.close
        return {
            "dataset": ds.scenario.id,
            "template": ds.scenario.template,
            "count": ds.n_symbols,
            "first_date": ds.first_date,
            "start": ds.start,
            "in_sample_end": ds.in_sample_end,
            "end": ds.end,
            "trading_days": ds.n_days,
            "items": [
                {
                    "symbol": sym,
                    "tier": ds.tiers[j],
                    # 0.4.0: names list late and delist (membership, R2 2.13) - prices are NaN outside membership
                    "base_price": round(_first_finite(ds.close[:, j]), 2),
                    "last_close": round(_first_finite(ds.close[::-1, j]), 2),
                    "listed": str(ds.dates[int(np.argmax(ds.member_mask[:, j]))]),
                    "delisted": bool(not ds.member_mask[-1, j]),
                    "mean_adv_shares": int(np.nan_to_num(np.nanmean(ds.adv_20[i0:, j]))),
                    "mean_adv_notional": round(float(np.nan_to_num(np.nanmean(adv_notional[i0:, j]))), 2),
                    "mean_spread_bps": round(float(np.nan_to_num(np.nanmean(ds.spread_bps[i0:, j]))), 3),
                }
                for j, sym in enumerate(ds.symbols)
            ],
        }

    @server.tool(
        "Return daily bars (open, high, low, close, volume, 20-day ADV, quoted spread) for a symbol between two "
        "dates, downsampled to max_points (full count reported)."
    )
    async def get_daily_bars(
        symbol: str, start: str, end: str, dataset: str = DEFAULT_RESEARCH_DATASET, max_points: int = 120
    ) -> dict[str, Any]:
        ds = select_history(history, dataset)
        i0, i1 = ds.index_range(start, end)
        bars = ds.bars(symbol, i0, i1)
        sample = downsample(bars, max_points)
        return {
            "symbol": symbol,
            "dataset": ds.scenario.id,
            "start": str(ds.dates[i0]),
            "end": str(ds.dates[i1]),
            "count": len(bars),
            "returned": len(sample),
            "items": sample,
        }

    @server.tool(
        "Summarise a dataset's coverage and regime: liquidity, spreads and the equal-weight market's return and "
        "volatility for the in-sample, out-of-sample and full periods."
    )
    async def get_universe_summary(
        dataset: str = DEFAULT_RESEARCH_DATASET, start: str | None = None, end: str | None = None
    ) -> dict[str, Any]:
        ds = select_history(history, dataset)
        i0, i1 = ds.index_range(start, end)
        split = min(max(ds.in_sample_end_index, i0), i1)
        market = np.full(ds.n_days, np.nan)
        market[1:] = np.nanmean(daily_returns(ds.close)[1:], axis=1)
        adv_notional = ds.adv_20 * ds.close
        tiers: dict[str, int] = {}
        for t in ds.tiers:
            tiers[t] = tiers.get(t, 0) + 1
        return {
            "dataset": ds.scenario.id,
            "template": ds.scenario.template,
            "symbols": ds.n_symbols,
            "coverage": {
                "first_date": ds.first_date,
                "start": str(ds.dates[i0]),
                "end": str(ds.dates[i1]),
                "in_sample_end": str(ds.dates[split]),
                "trading_days": int(i1 - i0 + 1),
                # complete = every member has a price on every day of its membership
                "complete": bool(np.isfinite(ds.close[i0 : i1 + 1][ds.member_mask[i0 : i1 + 1]]).all()),
            },
            "mean_adv_shares": int(np.nanmean(ds.adv_20[i0 : i1 + 1])),
            "mean_adv_notional": round(float(np.nanmean(adv_notional[i0 : i1 + 1])), 2),
            "mean_spread_bps": round(float(np.nanmean(ds.spread_bps[i0 : i1 + 1])), 3),
            "tiers": tiers,
            "periods": {
                "in_sample": _period(ds, market, "in_sample", i0, split),
                "out_of_sample": _period(ds, market, "out_of_sample", min(split + 1, i1), i1),
                "full": _period(ds, market, "full", i0, i1),
            },
        }

    return server
