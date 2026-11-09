"""Event-driven limit order book simulator (F1).

What it models, and why each piece is there:

* **Price-time priority.** Prices are integer ticks. Each price level is a FIFO queue; an
  incoming marketable order fills the best level first and, within a level, the oldest order
  first. A new resting order joins the *back* of its level, so its queue position is the displayed
  size ahead of it when it arrives.
* **Passive fills only when traded through.** A resting order fills only when an aggressive order
  reaches it after everything ahead of it in the queue has traded. Touching the price is not
  enough. (The v0.7 generator handed out passive fills with a fixed probability.)
* **Hidden liquidity.** A share of background limit orders are icebergs: they show ``peak`` shares
  and refill from a hidden reserve, and each refill goes to the back of the queue, so hidden size
  trades after the displayed size at its price.
* **A millisecond event clock with latency.** Background arrivals are Poisson in continuous time,
  rounded to the millisecond. The bot's orders and cancels reach the book ``entry_latency_ms``
  after it sends them, and the book it sees is ``md_latency_ms`` old.
* **Adverse selection.** Background limit orders are placed around a latent efficient price that
  diffuses; aggressive background orders lean towards it (``P(buy)`` rises with ``eff - mid``), so a
  resting order is most likely to fill just before the price moves through it.
* **Endogenous impact.** Every bot fill feeds a :class:`~ceap.execution.impact.PropagatorState`
  that shifts the efficient price, so the book re-centres after the bot trades.

It is a stylised zero-intelligence book in the tradition of Smith, Farmer, Gillemot and
Krishnamurthy (2003), "Statistical theory of the continuous double auction", Quant. Finance 3(6),
plus an informed-flow tilt. Evidence class: published model family; parameters are illustrative,
not calibrated to any venue.
"""

from __future__ import annotations

import bisect
import heapq
import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from ceap.execution.impact import PropagatorKernel, PropagatorState

BUY, SELL = 1, -1
AGENT = "agent"
BACKGROUND = "bg"


@dataclass
class RestingOrder:
    order_id: int
    owner: str
    side: int
    price: int
    visible: int
    hidden: int = 0
    peak: int = 0
    entered_ms: int = 0


@dataclass(frozen=True)
class Fill:
    """One side of one trade. ``liquidity`` is ``ADD`` for the resting side, ``REMOVE`` for the taker."""

    ts_ms: int
    order_id: int
    owner: str
    side: int
    price: int
    quantity: int
    liquidity: str


@dataclass(frozen=True)
class TapePrint:
    ts_ms: int
    price: int
    quantity: int
    aggressor_side: int
    passive_owner: str
    aggressor_owner: str


class OrderBook:
    """Price-time-priority book over integer tick prices."""

    def __init__(self) -> None:
        self._levels: dict[int, dict[int, deque[RestingOrder]]] = {BUY: {}, SELL: {}}
        self._orders: dict[int, RestingOrder] = {}

    # ----------------------------------------------------------------- queries
    def best(self, side: int) -> int | None:
        lv = self._levels[side]
        if not lv:
            return None
        return max(lv) if side == BUY else min(lv)

    def displayed(self, side: int, price: int) -> int:
        q = self._levels[side].get(price)
        return sum(o.visible for o in q) if q else 0

    def get(self, order_id: int) -> RestingOrder | None:
        return self._orders.get(order_id)

    def queue_ahead(self, order_id: int) -> int:
        """Displayed shares ahead of ``order_id`` at its price (0 if it is first)."""
        o = self._orders[order_id]
        ahead = 0
        for x in self._levels[o.side][o.price]:
            if x.order_id == order_id:
                return ahead
            ahead += x.visible
        raise KeyError(order_id)

    def orders(self, owner: str | None = None) -> list[RestingOrder]:
        return [o for o in self._orders.values() if owner is None or o.owner == owner]

    def levels(self, side: int, depth: int = 5) -> list[tuple[int, int]]:
        prices = sorted(self._levels[side], reverse=side == BUY)[:depth]
        return [(p, self.displayed(side, p)) for p in prices]

    def prices_beyond(self, side: int, reference: float, ticks: float) -> list[int]:
        """Levels of ``side`` more than ``ticks`` through ``reference`` (bids above, asks below)."""
        return [p for p in self._levels[side] if (p - reference) * side > ticks]

    # ---------------------------------------------------------------- updates
    def cancel_owner_at(self, side: int, price: int, owner: str) -> int:
        """Cancel every order of ``owner`` at one level; returns the number cancelled."""
        q = self._levels[side].get(price)
        if not q:
            return 0
        ids = [o.order_id for o in q if o.owner == owner]
        for oid in ids:
            self.cancel(oid)
        return len(ids)

    def rest(self, order: RestingOrder) -> None:
        if order.order_id in self._orders:
            raise ValueError(f"duplicate order id {order.order_id}")
        opp = self.best(-order.side)
        if opp is not None and (order.price - opp) * order.side >= 0:
            raise ValueError("a resting order may not cross the book; match it first")
        self._levels[order.side].setdefault(order.price, deque()).append(order)
        self._orders[order.order_id] = order

    def cancel(self, order_id: int) -> RestingOrder | None:
        o = self._orders.pop(order_id, None)
        if o is None:
            return None
        q = self._levels[o.side][o.price]
        q.remove(o)
        if not q:
            del self._levels[o.side][o.price]
        return o

    def match(
        self, side: int, quantity: int, limit: int | None, ts_ms: int, order_id: int, owner: str
    ) -> tuple[list[Fill], list[TapePrint], int]:
        """Take liquidity: returns (fills for both sides, tape prints, unfilled quantity)."""
        fills: list[Fill] = []
        tape: list[TapePrint] = []
        remaining = quantity
        book = self._levels[-side]
        while remaining > 0:
            px = self.best(-side)
            if px is None or (limit is not None and (px - limit) * side > 0):
                break
            q = book[px]
            while remaining > 0 and q:
                o = q[0]
                take = min(remaining, o.visible)
                remaining -= take
                o.visible -= take
                fills.append(Fill(ts_ms, o.order_id, o.owner, o.side, px, take, "ADD"))
                fills.append(Fill(ts_ms, order_id, owner, side, px, take, "REMOVE"))
                tape.append(TapePrint(ts_ms, px, take, side, o.owner, owner))
                if o.visible == 0:
                    q.popleft()
                    if o.hidden > 0:  # iceberg refill loses time priority
                        o.visible = min(o.peak, o.hidden)
                        o.hidden -= o.visible
                        o.entered_ms = ts_ms
                        q.append(o)
                    else:
                        del self._orders[o.order_id]
            if not q:
                del book[px]
        return fills, tape, remaining


@dataclass(frozen=True)
class SimulatorParams:
    tick: float = 0.01
    start_price: float = 100.0
    #: efficient-price volatility in bps per sqrt(second); 1.0 is ~150 bps a day (illustrative)
    sigma_bps_per_sqrt_s: float = 1.0
    limit_rate_per_s: float = 6.0
    market_rate_per_s: float = 1.2
    cancel_rate_per_s: float = 4.0
    mean_size: int = 100
    #: P(next level out) for new limit orders: offset behind the touch is geometric
    depth_decay: float = 0.45
    hidden_probability: float = 0.15
    iceberg_peak: int = 100
    iceberg_multiple: int = 5
    #: adverse selection: P(buy) = 1 / (1 + exp(-kappa (eff - mid) / tick))
    informed_kappa: float = 1.0
    #: background orders further than this (ticks) on the wrong side of the efficient price are pulled
    stale_ticks: float = 3.0
    entry_latency_ms: int = 2
    md_latency_ms: int = 1
    initial_levels: int = 8
    initial_size: int = 300
    kernel: PropagatorKernel = field(default_factory=PropagatorKernel)


@dataclass(frozen=True)
class BookView:
    """Top of book as the bot sees it (``md_latency_ms`` old)."""

    ts_ms: int
    bid: int | None
    ask: int | None
    bid_size: int
    ask_size: int

    @property
    def spread(self) -> int | None:
        return None if self.bid is None or self.ask is None else self.ask - self.bid

    @property
    def mid(self) -> float:
        if self.bid is None or self.ask is None:
            return float("nan")
        return (self.bid + self.ask) / 2.0


class LOBSimulator:
    """Seeded, deterministic event-driven simulator around one :class:`OrderBook`."""

    def __init__(self, params: SimulatorParams | None = None, seed: int = 0) -> None:
        self.p = params or SimulatorParams()
        self.rng = np.random.default_rng(seed)
        self.book = OrderBook()
        self.now_ms = 0
        self._ref_ticks = self.p.start_price / self.p.tick
        self._base_ticks = self._ref_ticks  # efficient price before impact
        self.impact = PropagatorState(self.p.kernel)
        self._next_id = 1
        self._seq = 0
        self._events: list[tuple[int, int, str, tuple]] = []
        self.tape: list[TapePrint] = []
        self.agent_fills: list[Fill] = []
        self._view_t: list[int] = []
        self._views: list[BookView] = []
        self.mid_history: list[tuple[int, float]] = []
        #: (ms, efficient price in ticks) after every event: the latent truth the book quotes around
        self.efficient_history: list[tuple[int, float]] = []
        self._total_rate = self.p.limit_rate_per_s + self.p.market_rate_per_s + self.p.cancel_rate_per_s
        self._seed_book()
        self._schedule_background()
        self._snapshot()

    # ------------------------------------------------------------ prices
    @property
    def efficient_ticks(self) -> float:
        return self._base_ticks * (1.0 + self.impact.impact_bps(self.now_ms / 1000.0) / 10_000.0)

    def to_price(self, ticks: float) -> float:
        return round(ticks * self.p.tick, 10)

    def mid_ticks(self) -> float:
        b, a = self.book.best(BUY), self.book.best(SELL)
        if b is None or a is None:
            return self.efficient_ticks
        return (a + b) / 2.0

    # ------------------------------------------------------------- set-up
    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id - 1

    def _seed_book(self) -> None:
        e = self._ref_ticks
        for k in range(self.p.initial_levels):
            for side in (BUY, SELL):
                px = (math.floor(e - 0.5) - k) if side == BUY else (math.ceil(e + 0.5) + k)
                self.book.rest(RestingOrder(self._new_id(), BACKGROUND, side, int(px), self.p.initial_size))

    def _push(self, t_ms: int, kind: str, payload: tuple) -> None:
        self._seq += 1
        heapq.heappush(self._events, (t_ms, self._seq, kind, payload))

    def _schedule_background(self) -> None:
        gap_ms = self.rng.exponential(1000.0 / self._total_rate)
        self._push(self.now_ms + max(1, int(round(gap_ms))), "bg", ())

    # ----------------------------------------------------------- bot API
    def submit(self, side: int, quantity: int, limit: int | None = None, ioc: bool = False) -> int:
        """Send an order; it reaches the book ``entry_latency_ms`` later. ``limit=None`` = market (IOC)."""
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        oid = self._new_id()
        self._push(
            self.now_ms + self.p.entry_latency_ms,
            "new",
            (oid, side, int(quantity), limit, ioc or limit is None),
        )
        return oid

    def cancel(self, order_id: int) -> None:
        self._push(self.now_ms + self.p.entry_latency_ms, "cancel", (order_id,))

    def view(self) -> BookView:
        """The latest book state that is at least ``md_latency_ms`` old."""
        t = self.now_ms - self.p.md_latency_ms
        i = bisect.bisect_right(self._view_t, t) - 1
        return self._views[max(i, 0)]

    def open_agent_orders(self) -> list[RestingOrder]:
        return self.book.orders(AGENT)

    def run_until(self, t_ms: int) -> list[Fill]:
        """Process every event up to and including ``t_ms``; returns the bot's fills in that span."""
        start = len(self.agent_fills)
        while self._events and self._events[0][0] <= t_ms:
            ts, _, kind, payload = heapq.heappop(self._events)
            self._diffuse(ts)
            self.now_ms = ts
            if kind == "bg":
                self._background_event()
                self._schedule_background()
            elif kind == "new":
                self._agent_new(*payload)
            elif kind == "cancel":
                self.book.cancel(payload[0])
            self._snapshot()
        self._diffuse(t_ms)
        self.now_ms = t_ms
        return self.agent_fills[start:]

    # ---------------------------------------------------------- internals
    def _diffuse(self, t_ms: int) -> None:
        dt = (t_ms - self.now_ms) / 1000.0
        if dt > 0:
            sd = self.p.sigma_bps_per_sqrt_s * math.sqrt(dt) / 10_000.0
            self._base_ticks *= math.exp(sd * float(self.rng.standard_normal()))

    def _snapshot(self) -> None:
        if self.efficient_history and self.efficient_history[-1][0] == self.now_ms:
            self.efficient_history[-1] = (self.now_ms, self.efficient_ticks)
        else:
            self.efficient_history.append((self.now_ms, self.efficient_ticks))
        b, a = self.book.best(BUY), self.book.best(SELL)
        v = BookView(
            self.now_ms,
            b,
            a,
            self.book.displayed(BUY, b) if b is not None else 0,
            self.book.displayed(SELL, a) if a is not None else 0,
        )
        if self._view_t and self._view_t[-1] == self.now_ms:
            self._views[-1] = v
            self.mid_history[-1] = (
                self.now_ms,
                v.mid if b is not None and a is not None else self.mid_ticks(),
            )
        else:
            self._view_t.append(self.now_ms)
            self._views.append(v)
            self.mid_history.append(
                (self.now_ms, v.mid if b is not None and a is not None else self.mid_ticks())
            )

    def _size(self) -> int:
        return int(max(1, round(self.rng.exponential(self.p.mean_size))))

    def _record(self, fills: list[Fill], tape: list[TapePrint]) -> None:
        self.tape.extend(tape)
        for f in fills:
            if f.owner == AGENT:
                self.agent_fills.append(f)
                self.impact.trade(f.ts_ms / 1000.0, f.side * f.quantity)

    def _pull_stale(self, eff: float) -> None:
        """Background quotes left on the wrong side of the efficient price are withdrawn."""
        for side in (BUY, SELL):
            for px in self.book.prices_beyond(side, eff, self.p.stale_ticks):
                self.book.cancel_owner_at(side, px, BACKGROUND)

    def _background_event(self) -> None:
        p = self.p
        eff = self.efficient_ticks
        self._pull_stale(eff)
        u = float(self.rng.random()) * self._total_rate
        if u < p.limit_rate_per_s:
            side = BUY if self.rng.random() < 0.5 else SELL
            offset = int(self.rng.geometric(1.0 - p.depth_decay)) - 1
            px = (math.floor(eff - 0.5) - offset) if side == BUY else (math.ceil(eff + 0.5) + offset)
            opp = self.book.best(-side)
            if opp is not None and (px - opp) * side >= 0:
                px = opp - side  # do not cross: improve to one tick inside
            size = self._size()
            hidden = 0
            peak = 0
            if self.rng.random() < p.hidden_probability:
                peak = p.iceberg_peak
                hidden = peak * (p.iceberg_multiple - 1)
                size = peak
            self.book.rest(
                RestingOrder(self._new_id(), BACKGROUND, side, int(px), size, hidden, peak, self.now_ms)
            )
        elif u < p.limit_rate_per_s + p.market_rate_per_s:
            gap = (eff - self.mid_ticks()) * p.informed_kappa
            prob_buy = 1.0 / (1.0 + math.exp(-max(min(gap, 50.0), -50.0)))
            side = BUY if self.rng.random() < prob_buy else SELL
            fills, tape, _ = self.book.match(
                side, self._size(), None, self.now_ms, self._new_id(), BACKGROUND
            )
            self._record(fills, tape)
        else:
            bg = self.book.orders(BACKGROUND)
            if len(bg) > 2 * p.initial_levels:  # keep a floor of depth
                self.book.cancel(bg[int(self.rng.integers(len(bg)))].order_id)

    def _agent_new(self, oid: int, side: int, qty: int, limit: int | None, ioc: bool) -> None:
        fills, tape, left = self.book.match(side, qty, limit, self.now_ms, oid, AGENT)
        self._record(fills, tape)
        if left > 0 and not ioc and limit is not None:
            self.book.rest(RestingOrder(oid, AGENT, side, int(limit), left, entered_ms=self.now_ms))

    # ------------------------------------------------------------ series
    def mid_grid(self, dt_ms: int, start_ms: int, end_ms: int, efficient: bool = False) -> np.ndarray:
        """Mid (ticks) at the end of each ``dt_ms`` bucket in ``[start_ms, end_ms)``.

        ``efficient=True`` returns the latent efficient price instead of the quoted mid.
        """
        hist = self.efficient_history if efficient else self.mid_history
        t = np.array([x for x, _ in hist])
        m = np.array([y for _, y in hist])
        edges = np.arange(start_ms + dt_ms, end_ms + 1, dt_ms)
        idx = np.searchsorted(t, edges, side="right") - 1
        return m[np.maximum(idx, 0)]

    def agent_flow_grid(self, dt_ms: int, start_ms: int, end_ms: int) -> np.ndarray:
        """The bot's signed filled shares per ``dt_ms`` bucket."""
        n = (end_ms - start_ms) // dt_ms
        out = np.zeros(n)
        for f in self.agent_fills:
            k = (f.ts_ms - start_ms - 1) // dt_ms  # an event at the bucket end belongs to that bucket
            if 0 <= k < n:
                out[k] += f.side * f.quantity
        return out

    def market_volume(self, start_ms: int, end_ms: int, exclude_agent: bool = True) -> int:
        return sum(
            p.quantity
            for p in self.tape
            if start_ms <= p.ts_ms < end_ms
            and not (exclude_agent and AGENT in (p.passive_owner, p.aggressor_owner))
        )
