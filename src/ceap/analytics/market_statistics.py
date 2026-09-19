"""Market-window statistics (deterministic)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime

import numpy as np

from ceap.analytics.liquidity import (
    average_displayed_depth,
    average_spread_bps,
    average_top_of_book_size,
    crossed_quote_count,
    stale_quote_fraction,
)
from ceap.analytics.volatility import annualise_volatility, price_drift_bps, realised_volatility_bps
from ceap.analytics.vwap import vwap
from ceap.domain.market import MarketStatistics, OrderBookSnapshot, Quote, Trade


def _sample_seconds(quotes: Sequence[Quote]) -> float:
    if len(quotes) < 2:
        return 1.0
    ts = np.array([q.timestamp.timestamp() for q in quotes])
    gaps = np.diff(ts)
    gaps = gaps[gaps > 0]
    return float(np.median(gaps)) if gaps.size else 1.0


def calculate_market_statistics(
    symbol: str,
    start: datetime,
    end: datetime,
    quotes: Sequence[Quote],
    trades: Sequence[Trade],
    books: Sequence[OrderBookSnapshot] = (),
) -> MarketStatistics:
    quotes = sorted(quotes, key=lambda q: q.timestamp)
    trades = sorted(trades, key=lambda t: t.timestamp)
    mids = np.array([q.mid for q in quotes if not q.is_crossed], dtype=float)
    trade_prices = np.array([t.price for t in trades], dtype=float)
    trade_qty = np.array([t.quantity for t in trades], dtype=float)

    open_mid = float(mids[0]) if mids.size else float("nan")
    close_mid = float(mids[-1]) if mids.size else float("nan")
    high = (
        float(trade_prices.max()) if trade_prices.size else (float(mids.max()) if mids.size else float("nan"))
    )
    low = (
        float(trade_prices.min()) if trade_prices.size else (float(mids.min()) if mids.size else float("nan"))
    )
    sample = _sample_seconds(quotes)

    return MarketStatistics(
        symbol=symbol,
        start=start,
        end=end,
        open_mid=open_mid,
        close_mid=close_mid,
        high=high,
        low=low,
        vwap=vwap(trade_prices, trade_qty),
        traded_volume=int(trade_qty.sum()) if trade_qty.size else 0,
        trade_count=len(trades),
        average_spread_bps=average_spread_bps(quotes),
        realised_volatility_bps=realised_volatility_bps(mids, sample_seconds=sample, horizon_seconds=60.0),
        annualised_volatility=annualise_volatility(mids, sample_seconds=sample),
        average_displayed_depth=average_displayed_depth(books),
        average_top_of_book_size=average_top_of_book_size(quotes),
        price_drift_bps=price_drift_bps(mids),
        stale_quote_fraction=stale_quote_fraction(quotes),
        crossed_quote_count=crossed_quote_count(quotes),
        quote_count=len(quotes),
        venue_volume=_venue_volume(trades),
    )


def _venue_volume(trades: Sequence[Trade]) -> dict[str, int]:
    """Total traded quantity per venue (a dict comprehension would keep only the last print)."""
    totals: Counter[str] = Counter()
    for t in trades:
        totals[t.venue] += t.quantity
    return dict(totals)
