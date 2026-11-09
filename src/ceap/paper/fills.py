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
costs + dividends``, because fills and cash-outs happen at the mark.

0.9.0 additions, all off by default so the backtest equivalence is unchanged:

* **Dividends** (F13): a cash dividend going ex at the open of ``i`` credits ``q * d`` to a long and
  debits it from a short (the short dividend cost). The traded close drops by about ``d`` on the
  ex-date, so mark P&L plus the dividend is the total return.
* **Closing auction** (F5, ``moc_auction``): one session fills at most ``auction_max_fraction *
  auction_share * ADV`` (default 10 % of a 6 % auction: 0.6 % of ADV) and pays the impact of our
  order on a seeded synthetic auction book instead of the continuous cost model.
* **Lots** (F10, ``lot_size``): fills are whole lots; the fractional part stays on the order and is
  recorded as a ``lot_residual`` event; an order smaller than one lot is cancelled (``below_lot``).
* **LOB engine** (F11, ``engine="lob"``): the cost is measured on the event-driven simulator
  (:mod:`ceap.paper.lob_exec`).
* **Halts** (F12): a member name without a print is treated as halted (``halted`` on the reject).
"""

from __future__ import annotations

import math
import zlib
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
from ceap.data.synthetic import CLOSING_AUCTION_ADV_FRACTION
from ceap.execution.auction import AuctionOrder, auction_impact_bps, synthetic_auction_book
from ceap.paper.feed import MarketPanel
from ceap.paper.pretrade import RiskLimits

EPS = 1e-9


@dataclass(frozen=True)
class FillParams:
    costs: CostModel = field(default_factory=CostModel)
    financing: FinancingModel = field(default_factory=FinancingModel)
    caps: PositionCaps = field(default_factory=PositionCaps)
    cost_multiplier: float = 1.0
    fee_per_share: float = 0.0  # the backtest charges no explicit fees; 0 by default, stated
    min_fee: float = 0.0
    order_ttl_days: int = 0  # 0: work until filled or replaced (the backtest's working-order convention)
    risk: RiskLimits = field(default_factory=RiskLimits)
    # 0.9.0 (F5 paper half): fill at the closing auction, capped at a fraction of the expected
    # auction volume, paying the auction impact of our order instead of the continuous cost model
    moc_auction: bool = False
    auction_share: float = CLOSING_AUCTION_ADV_FRACTION
    auction_max_fraction: float = 0.10
    # 0.9.0 (F10): whole lots; 0 keeps fractional fills (the backtest-equivalent default)
    lot_size: int = 0
    # 0.9.0 (F11 paper part): "close" (cost model at the close) or "lob" (cost measured on the simulator)
    engine: str = "close"
    lob_window_s: int = 30

    def __post_init__(self) -> None:
        if self.engine not in ("close", "lob"):
            raise ValueError(f"engine must be 'close' or 'lob', got {self.engine!r}")
        if self.lot_size < 0:
            raise ValueError("lot_size must be >= 0")
        if not 0 < self.auction_share <= 1 or not 0 < self.auction_max_fraction <= 1:
            raise ValueError("auction_share and auction_max_fraction must be in (0, 1]")

    def session_cap_fraction(self) -> float:
        """Most of ADV one session can fill: the participation cap, or the auction cap when MOC."""
        cap = self.caps.max_participation
        if self.moc_auction:
            cap = min(cap, self.auction_max_fraction * self.auction_share)
        return cap

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
    staging_id: str = ""
    approval_id: str = ""
    decision_price: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {"order_id": self.order_id, "symbol": self.symbol, "quantity": self.quantity,
                "staged_date": self.staged_date, "age": self.age, "staging_id": self.staging_id,
                "approval_id": self.approval_id, "decision_price": self.decision_price}

    def trace(self) -> dict[str, Any]:
        return {"staging_id": self.staging_id, "approval_id": self.approval_id}


@dataclass
class BookState:
    capital: float
    cash: float
    positions: dict[str, float] = field(default_factory=dict)
    marks: dict[str, float] = field(default_factory=dict)
    orders: dict[str, OpenOrder] = field(default_factory=dict)  # one live order per symbol
    target: dict[str, float] = field(default_factory=dict)  # shares targeted at the last rebalance
    waived: dict[str, float] = field(default_factory=dict)  # target quantity knowingly given up since then
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
            target=dict(self.target), waived=dict(self.waived),
            day_index=self.day_index, first_index=self.first_index, last_date=self.last_date,
        )

    def market_value(self) -> float:
        return float(sum(q * self.marks[s] for s, q in self.positions.items()))

    def to_json(self) -> dict[str, Any]:
        return {
            "capital": self.capital, "cash": self.cash,
            "positions": dict(sorted(self.positions.items())), "marks": dict(sorted(self.marks.items())),
            "orders": [o.to_json() for _, o in sorted(self.orders.items())],
            "target": dict(sorted(self.target.items())), "waived": dict(sorted(self.waived.items())),
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
            target={k: float(v) for k, v in d.get("target", {}).items()},
            waived={k: float(v) for k, v in d.get("waived", {}).items()},
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


def _model_bps(params: FillParams, panel: MarketPanel, i: int, j: int, vol_bps: np.ndarray, part: float) -> float:
    hs = float(np.nan_to_num(panel.spread_bps[i, j] / 2.0, nan=0.0))
    return float(params.costs.trade_cost_bps(np.array([hs]), np.array([vol_bps[j]]), np.array([part]))[0])


def _auction_cost_bps(symbol: str, day: str, price: float, quantity: float, auction_volume: float) -> float:
    """Impact of our MOC on the closing print, in bps (>= 0), on a seeded synthetic auction book.

    The book is :func:`~ceap.execution.auction.synthetic_auction_book` around the close with the
    expected auction volume; the seed is a CRC-32 of ``symbol|date`` so a session replays exactly.
    The print is assumed to be the close we mark at, so the impact is charged as a cost.
    """
    rng = np.random.default_rng(zlib.crc32(f"auction|{symbol}|{day}".encode()))
    book = synthetic_auction_book(rng, price, max(auction_volume, 1.0))
    ours = AuctionOrder(1 if quantity > 0 else -1, max(1, int(round(abs(quantity)))))
    return max(0.0, auction_impact_bps(book, ours, price))


def limit_alignment(params: FillParams) -> dict[str, Any]:
    """How the pre-trade order limit and the per-session fill cap fit together (F5).

    The pre-trade check lets an order reach ``max_order_adv`` of ADV; one session fills at most
    ``session_cap_fraction`` of ADV, so the order needs ``ceil(max_order_adv / cap)`` sessions.
    With ``max_work_sessions`` set, the pre-trade check clips to that many sessions of fills; with
    an order TTL shorter than the sessions needed, the tail is cancelled by design.
    """
    cap = params.session_cap_fraction()
    need = math.ceil(params.risk.max_order_adv / cap - 1e-12) if cap > 0 else 0
    ttl = params.order_ttl_days
    return {"max_order_adv": params.risk.max_order_adv, "session_cap_adv": cap, "sessions_to_fill": need,
            "order_ttl_days": ttl, "max_work_sessions": params.risk.max_work_sessions,
            "consistent": ttl == 0 or ttl >= min(need, params.risk.max_work_sessions or need)}


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
            o.decision_price /= sp.ratio
        for book_map in (st.target, st.waived):
            if sp.symbol in book_map:
                book_map[sp.symbol] *= sp.ratio
        ev.append(_event("split", sp.symbol, quantity=q * sp.ratio - q, ratio=sp.ratio))

    # 1b. cash dividends going ex at the open of i (F13): a long receives, a short pays
    dividends = 0.0
    for dv in panel.dividends_on(i):
        q = st.positions.get(dv.symbol, 0.0)
        if q:
            amount = q * dv.amount
            ev.append(_event("dividend", dv.symbol, quantity=None, price=dv.amount, cash_delta=amount,
                             per_share=dv.amount, shares=q, short=q < 0))
            dividends += amount

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
    signal_pnl = 0.0  # P&L the targeted shares would have made over the session
    if i > 0:
        for sym, t in st.target.items():
            j = sym_col[sym]
            p_now, p_prev = float(panel.close[i, j]), float(panel.close[i - 1, j])
            for sp in panel.splits_on(i):
                if sp.symbol == sym:
                    p_prev /= sp.ratio
            if _finite(p_now) and _finite(p_prev):
                signal_pnl += t * (p_now - p_prev)
    if st.positions:
        ev.append(_event("mark", cash_delta=0.0, pnl=mark_pnl))
    for sym in sorted(st.positions):
        j = sym_col[sym]
        if not panel.members[i, j]:
            q = st.positions.pop(sym)
            ev.append(_event("delist", sym, quantity=-q, price=st.marks[sym], cash_delta=q * st.marks[sym],
                             convention="cash out at the last mark (merger)"))
            st.cash += q * st.marks.pop(sym)
            st.waived[sym] = st.waived.get(sym, 0.0) + q  # the target can no longer be held

    # 4. fills at today's close
    traded = costs = 0.0
    for sym in sorted(st.orders):
        o = st.orders[sym]
        if o.staged_date >= panel.day(i):  # staged at this close: fills next session (t+1)
            continue
        o.age += 1
        j = sym_col[sym]
        if not panel.members[i, j]:
            ev.append(_event("cancel", sym, quantity=o.quantity, order_id=o.order_id, reason="not_a_member",
                             **o.trace()))
            st.waived[sym] = st.waived.get(sym, 0.0) + o.quantity
            del st.orders[sym]
            continue
        p = float(panel.close[i, j])
        if not _finite(p):
            ev.append(_event("reject", sym, quantity=o.quantity, order_id=o.order_id, reason="missing_price",
                             carried=o.quantity, halted=True, **o.trace()))
            continue
        if o.decision_price > 0 and abs(p / o.decision_price - 1.0) > params.risk.fill_price_band:
            ev.append(_event("cancel", sym, quantity=o.quantity, order_id=o.order_id, reason="price_band",
                             price=p, decision_price=o.decision_price, **o.trace()))
            st.waived[sym] = st.waived.get(sym, 0.0) + o.quantity
            del st.orders[sym]
            continue
        q = st.positions.get(sym, 0.0)
        want = o.quantity
        if q + want < min(q, 0.0) - EPS and params.risk.is_htb(sym, panel.tiers[j]):
            blocked = min(q, 0.0) - (q + want)
            ev.append(_event("reject", sym, quantity=-blocked, order_id=o.order_id, reason="hard_to_borrow",
                             **o.trace()))
            st.waived[sym] = st.waived.get(sym, 0.0) - blocked
            want = min(q, 0.0) - q
            o.quantity = want
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
                                     low=low, prev_close=prev, **o.trace()))
                    st.waived[sym] = st.waived.get(sym, 0.0) - blocked
                    want = floor - q
                    o.quantity = want
        if abs(want) <= EPS:
            del st.orders[sym]
            continue
        adv = float(panel.adv_20[i, j])
        cap = params.session_cap_fraction() * adv if np.isfinite(adv) and adv > 0 else 0.0
        if cap <= 0:
            ev.append(_event("reject", sym, quantity=want, order_id=o.order_id, reason="no_adv", carried=want,
                             **o.trace()))
            continue
        x = math.copysign(min(abs(want), cap), want)
        if params.lot_size:
            lots = math.copysign(math.floor(abs(x) / params.lot_size) * params.lot_size, x)
            if lots == 0 and abs(want) < params.lot_size:
                # the whole order is below one lot: it can never fill, so give it up explicitly
                ev.append(_event("cancel", sym, quantity=want, order_id=o.order_id, reason="below_lot",
                                 lot_size=params.lot_size, **o.trace()))
                st.waived[sym] = st.waived.get(sym, 0.0) + want
                del st.orders[sym]
                continue
            if lots != x:
                ev.append(_event("lot_residual", sym, quantity=x - lots, order_id=o.order_id,
                                 lot_size=params.lot_size, carried=True, **o.trace()))
            x = lots
            if x == 0:
                continue
        part = abs(x) / adv
        detail: dict[str, Any] = {}
        if params.moc_auction:
            bps = _auction_cost_bps(sym, panel.day(i), p, x, params.auction_share * adv)
            detail["venue"] = "closing_auction"
        elif params.engine == "lob":
            from ceap.paper.lob_exec import lob_cost_bps

            lc = lob_cost_bps(sym, panel.day(i), p, x, part, window_s=params.lob_window_s)
            if lc is None:
                bps = _model_bps(params, panel, i, j, vol_bps, part)
                detail["engine"] = "close (lob filled nothing)"
            else:
                bps = lc.bps
                detail.update(engine="lob", sim_shares=lc.sim_shares, sim_filled=lc.filled)
        else:
            bps = _model_bps(params, panel, i, j, vol_bps, part)
        cost = abs(x) * p * bps / 10_000.0 * params.cost_multiplier
        if params.fee_per_share or params.min_fee:
            cost += max(abs(x) * params.fee_per_share, params.min_fee)
        carried = want - x
        ev.append(_event("fill", sym, quantity=x, price=p, cash_delta=-x * p - cost, cost=cost,
                         order_id=o.order_id, participation=part, cost_bps=bps, carried=carried, **detail,
                         **o.trace()))
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
        if params.order_ttl_days and o.age >= params.order_ttl_days:
            ev.append(_event("cancel", sym, quantity=o.quantity, order_id=o.order_id, reason="expired",
                             **o.trace()))
            st.waived[sym] = st.waived.get(sym, 0.0) + o.quantity
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
        "dividends": dividends,
        "rf": rf,
        "daily_return": eq / prev_equity - 1.0 if prev_equity > 0 else 0.0,
        "n_positions": len(st.positions),
        "open_orders": len(st.orders),
        "attribution": {  # signal + drift + financing - borrow - costs = equity change
            "signal": signal_pnl, "drift": mark_pnl - signal_pnl, "financing": interest, "borrow": -borrow,
            "costs": -costs, "dividends": dividends,
        },
    }
    return DayResult(ev, st, snapshot)
