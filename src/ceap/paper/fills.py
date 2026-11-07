"""Simulated fills and the pure one-session step of a paper book.

:func:`simulate_day` takes a :class:`BookState` (shares, cash, marks, open orders) and one panel row
and returns the ledger events, the new state and a snapshot. It touches no database and no clock, so
the ledger, the daily job and the backtest-equivalence test all run the same arithmetic.

Order of one session ``i`` (every step is a ledger event):

1. **Splits** effective at the open of ``i``: shares and open order quantities times the ratio, marks
   divided by it. Cash and equity unchanged.
2. **Financing** on the previous close's book (the backtest's ``hold`` recursion, 2.3):
   ``interest = cash * (rf_i + s)`` with ``s`` the financing spread when cash is negative, and
   ``borrow = sum_j max(-q_j, 0) * mark_j * b_j`` with the tier borrow rate ``b_j``.
3. **Mark** to the close of ``i``; a missing print keeps the last mark (the backtest's NaN-return-is-0).
   A held name that left the universe is cashed out at its last mark (merger convention).
4. **Fills** of open orders at the close of ``i`` (orders staged on ``i-1``: the t+1 convention):
   not a member -> cancelled; no price -> rejected, quantity carried; SSR day
   (``low_i / close_{i-1} - 1 <= -10 %``) -> the short-increasing part is rejected; then at most
   ``max_participation * adv_20`` shares fill and the rest carries to the next session. Cost of a fill
   of ``x`` shares at ``p``: ``|x| p (h + Y sigma sqrt(|x| / ADV)) / 1e4 * multiplier + fees``, from the
   imported :class:`~ceap.analytics.backtest.CostModel`.
5. **Expiry**: an order older than ``order_ttl_days`` sessions is cancelled.

Identity (checked by a property test): ``equity_i - equity_{i-1} = mark_pnl + interest - borrow -
costs``, because fills and cash-outs happen at the mark.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ceap.analytics.backtest import (
    SSR_TRIGGER,
    TRADING_DAYS_PER_YEAR,
    CostModel,
    FinancingModel,
    PositionCaps,
)
from ceap.paper.feed import MarketPanel

EPS = 1e-9


@dataclass(frozen=True)
class FillParams:
    costs: CostModel = field(default_factory=CostModel)
    financing: FinancingModel = field(default_factory=FinancingModel)
    caps: PositionCaps = field(default_factory=PositionCaps)
    cost_multiplier: float = 1.0
    fee_per_share: float = 0.0  # the backtest charges no explicit fees; 0 by default, stated
    min_fee: float = 0.0
    order_ttl_days: int = 5

    def borrow_daily(self, tier: str) -> float:
        if not self.financing.enabled:
            return 0.0
        return self.financing.borrow_bps_annual.get(tier, 1000.0) / 10_000.0 / TRADING_DAYS_PER_YEAR

    @property
    def spread_daily(self) -> float:
        if not self.financing.enabled:
            return 0.0
        return self.financing.financing_spread_bps_annual / 10_000.0 / TRADING_DAYS_PER_YEAR


@dataclass
class OpenOrder:
    order_id: str
    symbol: str
    quantity: float  # signed remaining shares
    staged_date: str
    age: int = 0  # sessions it has been eligible to fill

    def to_json(self) -> dict[str, Any]:
        return {"order_id": self.order_id, "symbol": self.symbol, "quantity": self.quantity,
                "staged_date": self.staged_date, "age": self.age}


@dataclass
class BookState:
    capital: float
    cash: float
    positions: dict[str, float] = field(default_factory=dict)
    marks: dict[str, float] = field(default_factory=dict)
    orders: dict[str, OpenOrder] = field(default_factory=dict)  # one live order per symbol
    equity: float = 0.0
    day_index: int = -1
    first_index: int = -1
    last_date: str = ""

    @staticmethod
    def new(capital: float) -> BookState:
        return BookState(capital=capital, cash=capital, equity=capital)

    def copy(self) -> BookState:
        return BookState(
            capital=self.capital, cash=self.cash, positions=dict(self.positions), marks=dict(self.marks),
            orders={k: OpenOrder(**v.to_json()) for k, v in self.orders.items()}, equity=self.equity,
            day_index=self.day_index, first_index=self.first_index, last_date=self.last_date,
        )

    def market_value(self) -> float:
        return float(sum(q * self.marks[s] for s, q in self.positions.items()))

    def to_json(self) -> dict[str, Any]:
        return {
            "capital": self.capital, "cash": self.cash,
            "positions": dict(sorted(self.positions.items())), "marks": dict(sorted(self.marks.items())),
            "orders": [o.to_json() for _, o in sorted(self.orders.items())],
            "equity": self.equity, "day_index": self.day_index, "first_index": self.first_index,
            "last_date": self.last_date,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> BookState:
        return BookState(
            capital=float(d["capital"]), cash=float(d["cash"]),
            positions={k: float(v) for k, v in d["positions"].items()},
            marks={k: float(v) for k, v in d["marks"].items()},
            orders={o["symbol"]: OpenOrder(**o) for o in d["orders"]},
            equity=float(d["equity"]), day_index=int(d["day_index"]), first_index=int(d["first_index"]),
            last_date=str(d["last_date"]),
        )


@dataclass(frozen=True)
class DayResult:
    events: list[dict[str, Any]]
    state: BookState
    snapshot: dict[str, Any]


def _event(kind: str, symbol: str | None = None, quantity: float | None = None, price: float | None = None,
           cash_delta: float = 0.0, cost: float = 0.0, order_id: str | None = None, **detail: Any) -> dict[str, Any]:
    return {"kind": kind, "symbol": symbol, "quantity": quantity, "price": price, "cash_delta": cash_delta,
            "cost": cost, "order_id": order_id, "detail": detail}


def _finite(x: float) -> bool:
    return bool(np.isfinite(x)) and x > 0


def simulate_day(
    state: BookState,
    panel: MarketPanel,
    i: int,
    params: FillParams,
    rf: float,
    vol_bps: np.ndarray,
) -> DayResult:
    """One session of a paper book. ``vol_bps`` is the cost model's daily vol row for session ``i``."""
    if state.day_index >= 0 and i != state.day_index + 1:
        raise ValueError(f"session {panel.day(i)} does not follow {state.last_date}; sessions must be contiguous")
    st = state.copy()
    ev: list[dict[str, Any]] = []
    sym_col = {s: k for k, s in enumerate(panel.symbols)}

    # 1. splits
    for sp in panel.splits_on(i):
        q = st.positions.get(sp.symbol, 0.0)
        if q:
            st.positions[sp.symbol] = q * sp.ratio
            st.marks[sp.symbol] = st.marks[sp.symbol] / sp.ratio
        o = st.orders.get(sp.symbol)
        if o is not None:
            o.quantity *= sp.ratio
        ev.append(_event("split", sp.symbol, quantity=q * sp.ratio - q, ratio=sp.ratio))

    # 2. financing on the previous close's book
    interest = borrow = 0.0
    if st.day_index >= 0 and params.financing.enabled:
        s = params.spread_daily if st.cash < 0 else 0.0
        interest = st.cash * (rf + s)
        for sym, q in st.positions.items():
            if q < 0:
                borrow += -q * st.marks[sym] * params.borrow_daily(panel.tiers[sym_col[sym]])
        if interest:
            ev.append(_event("financing", cash_delta=interest, rf=rf, spread=s, cash_before=st.cash))
        if borrow:
            ev.append(_event("borrow", cash_delta=-borrow))
        st.cash += interest - borrow

    # 3. mark to today's close; cash out names that left the universe
    mark_pnl = 0.0
    for sym in sorted(st.positions):
        j = sym_col[sym]
        p = float(panel.close[i, j])
        if _finite(p):
            mark_pnl += st.positions[sym] * (p - st.marks[sym])
            st.marks[sym] = p
    if st.positions:
        ev.append(_event("mark", cash_delta=0.0, pnl=mark_pnl))
    for sym in sorted(st.positions):
        j = sym_col[sym]
        if not panel.members[i, j]:
            q = st.positions.pop(sym)
            ev.append(_event("delist", sym, quantity=-q, price=st.marks[sym], cash_delta=q * st.marks[sym],
                             convention="cash out at the last mark (merger)"))
            st.cash += q * st.marks.pop(sym)

    # 4. fills at today's close
    traded = costs = 0.0
    for sym in sorted(st.orders):
        o = st.orders[sym]
        if o.staged_date >= panel.day(i):  # staged at this close: fills next session (t+1)
            continue
        o.age += 1
        j = sym_col[sym]
        if not panel.members[i, j]:
            ev.append(_event("cancel", sym, quantity=o.quantity, order_id=o.order_id, reason="not_a_member"))
            del st.orders[sym]
            continue
        p = float(panel.close[i, j])
        if not _finite(p):
            ev.append(_event("reject", sym, quantity=o.quantity, order_id=o.order_id, reason="missing_price",
                             carried=o.quantity))
            continue
        q = st.positions.get(sym, 0.0)
        want = o.quantity
        if want < 0 and params.financing.enabled and params.financing.ssr and i > 0:
            prev = float(panel.close[i - 1, j])
            for sp in panel.splits_on(i):
                if sp.symbol == sym:
                    prev /= sp.ratio
            low = float(panel.low[i, j])
            if _finite(prev) and np.isfinite(low) and low / prev - 1.0 <= SSR_TRIGGER:
                floor = min(q, 0.0)
                if q + want < floor - EPS:
                    blocked = floor - (q + want)
                    ev.append(_event("reject", sym, quantity=-blocked, order_id=o.order_id, reason="ssr",
                                     low=low, prev_close=prev))
                    want = floor - q
                    o.quantity = want
        if abs(want) <= EPS:
            del st.orders[sym]
            continue
        adv = float(panel.adv_20[i, j])
        cap = params.caps.max_participation * adv if np.isfinite(adv) and adv > 0 else 0.0
        if cap <= 0:
            ev.append(_event("reject", sym, quantity=want, order_id=o.order_id, reason="no_adv", carried=want))
            continue
        x = math.copysign(min(abs(want), cap), want)
        part = abs(x) / adv
        hs = float(np.nan_to_num(panel.spread_bps[i, j] / 2.0, nan=0.0))
        bps = float(params.costs.trade_cost_bps(np.array([hs]), np.array([vol_bps[j]]), np.array([part]))[0])
        cost = abs(x) * p * bps / 10_000.0 * params.cost_multiplier
        if params.fee_per_share or params.min_fee:
            cost += max(abs(x) * params.fee_per_share, params.min_fee)
        carried = want - x
        ev.append(_event("fill", sym, quantity=x, price=p, cash_delta=-x * p - cost, cost=cost,
                         order_id=o.order_id, participation=part, cost_bps=bps, carried=carried))
        st.cash += -x * p - cost
        st.positions[sym] = q + x
        st.marks[sym] = p
        if abs(st.positions[sym]) <= EPS:
            del st.positions[sym]
            del st.marks[sym]
        traded += abs(x) * p
        costs += cost
        if abs(carried) <= EPS:
            del st.orders[sym]
        else:
            o.quantity = carried

    # 5. expiry
    for sym in sorted(st.orders):
        o = st.orders[sym]
        if o.age >= params.order_ttl_days:
            ev.append(_event("cancel", sym, quantity=o.quantity, order_id=o.order_id, reason="expired"))
            del st.orders[sym]

    prev_equity = state.equity
    long_mv = sum(q * st.marks[s] for s, q in st.positions.items() if q > 0)
    short_mv = sum(q * st.marks[s] for s, q in st.positions.items() if q < 0)
    st.equity = st.cash + long_mv + short_mv
    st.day_index = i
    st.last_date = panel.day(i)
    if st.first_index < 0:
        st.first_index = i
    eq = st.equity
    snapshot = {
        "date": panel.day(i),
        "equity": eq,
        "cash": st.cash,
        "long_mv": long_mv,
        "short_mv": short_mv,
        "gross_exposure": (long_mv - short_mv) / eq if eq > 0 else float("nan"),
        "net_exposure": (long_mv + short_mv) / eq if eq > 0 else float("nan"),
        "traded_notional": traded,
        "turnover": traded / prev_equity if prev_equity > 0 else 0.0,
        "costs": costs,
        "borrow": borrow,
        "financing": interest,
        "mark_pnl": mark_pnl,
        "rf": rf,
        "daily_return": eq / prev_equity - 1.0 if prev_equity > 0 else 0.0,
        "n_positions": len(st.positions),
        "open_orders": len(st.orders),
    }
    return DayResult(ev, st, snapshot)
