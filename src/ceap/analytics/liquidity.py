"""Liquidity measures from quotes and order-book snapshots."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ceap.domain.market import OrderBookSnapshot, Quote


def average_spread_bps(quotes: Sequence[Quote]) -> float:
    if not quotes:
        return float("nan")
    spreads = np.array([q.spread_bps for q in quotes if not q.is_crossed and q.mid > 0], dtype=float)
    return float(spreads.mean()) if spreads.size else float("nan")


def average_top_of_book_size(quotes: Sequence[Quote]) -> float:
    if not quotes:
        return float("nan")
    sizes = np.array([(q.bid_size + q.ask_size) / 2.0 for q in quotes], dtype=float)
    return float(sizes.mean())


def average_displayed_depth(books: Sequence[OrderBookSnapshot], levels: int = 5) -> float:
    if not books:
        return float("nan")
    depth = np.array([b.displayed_depth(levels=levels) for b in books], dtype=float)
    return float(depth.mean())


def average_imbalance(books: Sequence[OrderBookSnapshot], levels: int = 5) -> float:
    if not books:
        return float("nan")
    return float(np.mean([b.imbalance(levels) for b in books]))


def stale_quote_fraction(quotes: Sequence[Quote]) -> float:
    """Fraction of consecutive quotes that are byte-identical (bid/ask/sizes).

    A healthy 1-second feed on a liquid name updates almost every tick; long
    runs of identical quotes indicate a stale or gapped feed.
    """
    if len(quotes) < 2:
        return 0.0
    stale = 0
    for prev, cur in zip(quotes, quotes[1:], strict=False):
        if (prev.bid, prev.ask, prev.bid_size, prev.ask_size) == (
            cur.bid,
            cur.ask,
            cur.bid_size,
            cur.ask_size,
        ):
            stale += 1
    return stale / (len(quotes) - 1)


def crossed_quote_count(quotes: Sequence[Quote]) -> int:
    return sum(1 for q in quotes if q.is_crossed)


def participation_rate(executed_quantity: float, market_volume: float) -> float:
    if market_volume <= 0:
        return float("nan")
    return float(executed_quantity / market_volume)
