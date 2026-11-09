"""Opening and closing auction model: uncross, imbalance, indicative price, auction impact (F5).

A call auction collects orders and then trades them all at one price. The rule used here is the
one the US listing exchanges publish for their opening and closing crosses (evidence class:
exchange rule summaries as recalled - NYSE Rule 7.35 and Nasdaq Rule 4752 - simplified):

1. choose the price that **maximises executed volume**;
2. among those, the price that **minimises the imbalance** left over;
3. among those, the price **closest to the reference** (the last sale or the continuous mid).

Market-on-close orders (``limit=None``) trade at any price. Allocation is by price priority, then
time priority (input order); the side with the imbalance is partially filled.

Auction impact is endogenous here: :func:`auction_impact_bps` uncrosses the book with and
without our order, so the cost of a large MOC comes from the orders that are actually there.

``CLOSING_AUCTION_ADV_FRACTION`` (0.06, the synthetic generator's parameter) is the default
auction share of daily volume. Public exchange commentary puts US closing auctions at roughly
7-10 % of consolidated volume in recent years (evidence class: public commentary, not verified
here). Deferred to the paper lane: capping paper MOC fills at an auction fraction and aligning
the pre-trade 25 % and fill 10 % limits (plan step F5, paper half).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from ceap.data.synthetic import CLOSING_AUCTION_ADV_FRACTION


@dataclass(frozen=True)
class AuctionOrder:
    side: int  # +1 buy, -1 sell
    quantity: int
    limit: float | None = None  # None = market-on-open/close
    owner: str = "bg"


@dataclass(frozen=True)
class AuctionResult:
    price: float
    matched: int
    imbalance: int  # signed: > 0 = unmatched buy interest at the price
    fills: tuple[tuple[int, int], ...]  # (order index, filled quantity)

    def filled(self, index: int) -> int:
        return sum(q for i, q in self.fills if i == index)


def _executable(orders: Sequence[AuctionOrder], price: float) -> tuple[int, int]:
    buy = sum(o.quantity for o in orders if o.side > 0 and (o.limit is None or o.limit >= price - 1e-9))
    sell = sum(o.quantity for o in orders if o.side < 0 and (o.limit is None or o.limit <= price + 1e-9))
    return buy, sell


def indicative(orders: Sequence[AuctionOrder], reference: float) -> tuple[float, int, int]:
    """``(price, matched, signed imbalance)`` the auction would print now (rules 1-3 above)."""
    candidates = sorted({o.limit for o in orders if o.limit is not None} | {reference})
    best: tuple[int, int, float, float] | None = None  # (-matched, |imbalance|, distance, price)
    for p in candidates:
        b, s = _executable(orders, p)
        key = (-min(b, s), abs(b - s), abs(p - reference), p)
        if best is None or key < best:
            best = key
    assert best is not None
    price = best[3]
    b, s = _executable(orders, price)
    return float(price), int(min(b, s)), int(b - s)


def uncross(orders: Sequence[AuctionOrder], reference: float) -> AuctionResult:
    """Run the auction: one price, price-then-time priority allocation."""
    price, matched, imbalance = indicative(orders, reference)
    fills: list[tuple[int, int]] = []
    for side in (1, -1):
        eligible = [
            (i, o)
            for i, o in enumerate(orders)
            if o.side == side and (o.limit is None or (o.limit - price) * side >= -1e-9)
        ]
        # market orders first, then the most aggressive limit, then time (index)
        eligible.sort(key=lambda x: (x[1].limit is not None, -side * (x[1].limit or 0.0), x[0]))
        left = matched
        for i, o in eligible:
            if left <= 0:
                break
            q = min(o.quantity, left)
            fills.append((i, q))
            left -= q
    return AuctionResult(price, matched, imbalance, tuple(fills))


def auction_impact_bps(orders: Sequence[AuctionOrder], ours: AuctionOrder, reference: float) -> float:
    """Signed price change our order causes in the auction print, in bps (positive = adverse)."""
    p0, _, _ = indicative(orders, reference)
    p1, _, _ = indicative([*orders, ours], reference)
    return float(ours.side * (p1 - p0) / p0 * 10_000.0)


def expected_auction_volume(adv_shares: float, auction_share: float = CLOSING_AUCTION_ADV_FRACTION) -> float:
    return float(adv_shares * auction_share)


def auction_capacity(
    adv_shares: float, max_fraction: float = 0.10, auction_share: float = CLOSING_AUCTION_ADV_FRACTION
) -> int:
    """Largest MOC that stays within ``max_fraction`` of the expected auction volume."""
    return int(max_fraction * expected_auction_volume(adv_shares, auction_share))


def synthetic_auction_book(
    rng: np.random.Generator,
    reference: float,
    expected_volume: float,
    n_orders: int = 60,
    market_share: float = 0.4,
    spread_bps: float = 30.0,
) -> list[AuctionOrder]:
    """A balanced random auction book around ``reference`` for experiments and tests.

    ``market_share`` of the volume is MOC; the rest are limits spread over ``+/- spread_bps``.
    """
    sizes = rng.dirichlet(np.ones(n_orders)) * expected_volume
    out: list[AuctionOrder] = []
    for k, q in enumerate(sizes):
        side = 1 if k % 2 == 0 else -1
        if rng.random() < market_share:
            out.append(AuctionOrder(side, max(1, int(q))))
        else:
            off = rng.uniform(-spread_bps, spread_bps) / 10_000.0
            out.append(AuctionOrder(side, max(1, int(q)), round(reference * (1.0 + off), 2)))
    return out
