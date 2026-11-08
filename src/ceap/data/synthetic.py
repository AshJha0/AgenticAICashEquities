"""Synthetic but realistic cash-equity data generator (R2T: US session, both sides, real fills).

The generator is seeded and fully deterministic. Every scenario effect is localised to the
*investigation window* of the scenario's session; the same clock window on the previous
``REFERENCE_SESSIONS`` sessions is generated under the normal regime so a time-of-day-matched
baseline exists (2T.2), alongside the same-day baseline hour.

Timeline of the scenario session (America/New_York; regular session 09:30 to 16:00)::

    12:00        13:00          14:00          15:00        16:00 (closing auction print)
    |-- warm-up --|-- baseline --|--- window ---|-- post ----|

Previous sessions carry 13:58 to 15:06: the matched window plus the decision lead and markout tail.

Mechanics shared with the multi-day TCA universe (:mod:`ceap.data.tca_universe`) live in
:func:`simulate_parent` so the pre-trade model fitted on the universe describes these scenarios.
Ground truth for every cost component comes from the parameters here, never from analytics runs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from ceap.data.scenarios import BASE_PRICES, VENUE_WEIGHTS, VENUES, ScenarioSpec
from ceap.data.session import (
    REFERENCE_SESSIONS,
    SESSION_SECONDS,
    SESSION_TZ,
    previous_sessions,
    session_time,
)
from ceap.domain.engineering import Deployment, LogEntry, MetricPoint
from ceap.domain.execution import Execution, Order, OrderStatus, Side
from ceap.domain.market import OrderBookLevel, OrderBookSnapshot, Quote, Trade
from ceap.domain.risk import Position, RiskLimit

NY = SESSION_TZ
LONDON = SESSION_TZ  # legacy name kept for importers; the desk clock is America/New_York (2T.1)
TICK = 0.01
DEFAULT_SESSION_DATE = date(2026, 9, 18)

ANNUAL_VOL = 0.10
SIGMA_DAILY_BPS = ANNUAL_VOL / math.sqrt(252) * 10_000.0
SECONDS_PER_YEAR_TRADING = 252 * SESSION_SECONDS
BASE_SPREAD_BPS = {"AAPL": 1.8, "MSFT": 1.6, "NVDA": 2.4, "AMZN": 2.0, "META": 2.2, "GOOGL": 2.0}
BASE_TOB_SIZE = {"AAPL": 450, "MSFT": 350, "NVDA": 900, "AMZN": 400, "META": 200, "GOOGL": 500}
TRADES_PER_SECOND = 1.5  # session mean; the U-shaped profile redistributes it through the day
MEAN_TRADE_SIZE = 150
TRADE_SIZE_LOG_SD = 0.6
EXPECTED_TRADE_SIZE = MEAN_TRADE_SIZE * math.exp(TRADE_SIZE_LOG_SD**2 / 2.0)
ADV_SHARES = TRADES_PER_SECOND * EXPECTED_TRADE_SIZE * SESSION_SECONDS
BASE_PARENT_QTY = 60_000
CHILD_INTERVAL_SECONDS = 30
BASE_FILL_RATE = 0.92
PASSIVE_FILL_RATE = 0.80
AGGRESSIVE_PROB = 0.65
ODD_LOT_PROB = 0.08
BASE_LATENCY_US = 800.0
#: permanent impact, realised as a linear drift over the parent: gamma * sigma_daily * sqrt(Q/ADV)
IMPACT_GAMMA = 0.7
DECISION_DELAY_SECONDS = 90
U_SHAPE_A, U_SHAPE_B = 0.6, 1.2  # profile a + b (2x - 1)^2 on [0, 1]: mean 1, 1.8 at the edges
LATE_PRINT_PROB = 0.002
LATE_PRINT_LAG_SECONDS = 60
CLOSING_AUCTION_ADV_FRACTION = 0.06
#: sale conditions left out of interval VWAP and market volume by default (2T.1)
EXCLUDED_CONDITIONS = frozenset({"AUCTION_OPEN", "AUCTION_CLOSE", "LATE"})


def intraday_profile(seconds_since_open: np.ndarray | float, u_shape: bool = True) -> np.ndarray:
    """U-shaped intraday activity profile with mean 1 over the regular session."""
    x = np.clip(np.asarray(seconds_since_open, dtype=float) / SESSION_SECONDS, 0.0, 1.0)
    if not u_shape:
        return np.ones_like(x)
    return U_SHAPE_A + U_SHAPE_B * (2.0 * x - 1.0) ** 2


def expected_impact_drift_bps(
    quantity: float, adv: float = ADV_SHARES, sigma_bps: float = SIGMA_DAILY_BPS
) -> float:
    """Generator truth: total permanent drift a parent of ``quantity`` causes over its life."""
    return IMPACT_GAMMA * sigma_bps * math.sqrt(max(quantity, 0.0) / adv)


@dataclass
class SyntheticDataset:
    scenario: ScenarioSpec
    symbol: str
    session_date: date
    sim_start: datetime
    sim_end: datetime
    baseline_start: datetime
    baseline_end: datetime
    window_start: datetime
    window_end: datetime
    quotes: list[Quote] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    order_books: list[OrderBookSnapshot] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    executions: list[Execution] = field(default_factory=list)
    metrics: list[MetricPoint] = field(default_factory=list)
    logs: list[LogEntry] = field(default_factory=list)
    deployments: list[Deployment] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    limits: list[RiskLimit] = field(default_factory=list)
    reference_sessions: tuple[date, ...] = ()
    #: generator parameters behind each parent (ground truth; never derived from analytics)
    truth: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def parent_orders(self) -> list[Order]:
        return [o for o in self.orders if o.is_parent]

    def summary(self) -> dict[str, int | str]:
        return {
            "scenario": self.scenario.id,
            "symbol": self.symbol,
            "quotes": len(self.quotes),
            "trades": len(self.trades),
            "order_books": len(self.order_books),
            "orders": len(self.orders),
            "executions": len(self.executions),
            "metrics": len(self.metrics),
            "logs": len(self.logs),
            "deployments": len(self.deployments),
        }


def london(session_date: date, hh: int, mm: int = 0) -> datetime:
    """Legacy name: a wall-clock time on ``session_date`` in the desk timezone (America/New_York)."""
    return session_time(session_date, hh, mm)


def _round_tick(x: np.ndarray | float) -> np.ndarray:
    return np.round(np.asarray(x) / TICK) * TICK


# --------------------------------------------------------------------------- shared engine
@dataclass(frozen=True)
class ParentPlan:
    """One parent order to simulate on a session's one-second grid."""

    order_id: str
    side: Side
    quantity: int
    start_index: int
    duration_seconds: int
    algo: str = "VWAP"
    aggressive_prob: float = AGGRESSIVE_PROB
    broker: str = "BRK_A"
    broker_extra_bps: float = 0.0  # planted broker under-performance (2T.1 power test)
    child_interval: int = CHILD_INTERVAL_SECONDS
    front_load: float = 0.0  # 0 = linear schedule; >0 = IS-style exponential front-loading


@dataclass
class ChildRecord:
    order_id: str
    index: int
    quantity: int
    venue: str
    status: OrderStatus
    limit_price: float
    latency_us: float


@dataclass
class FillRecord:
    order_id: str
    index: int
    quantity: int
    price: float
    venue: str
    latency_us: float
    liquidity_flag: str
    offset_s: float


@dataclass(frozen=True)
class Regime:
    """Execution-side regime switches applied inside a mask (a subset of the scenario fields)."""

    volatility_multiplier: float = 1.0
    degraded_venue: str | None = None
    degraded_fill_rate: float = 0.45
    degraded_extra_slippage_bps: float = 4.0
    latency_multiplier: float = 1.0
    reject_rate: float = 0.0

    @staticmethod
    def of(spec: ScenarioSpec) -> Regime:
        return Regime(
            spec.volatility_multiplier,
            spec.degraded_venue,
            spec.degraded_fill_rate,
            spec.degraded_extra_slippage_bps,
            spec.latency_multiplier,
            spec.reject_rate,
        )


def mid_path(
    rng: np.random.Generator,
    start_price: float,
    profile: np.ndarray,
    vol_mult: np.ndarray,
    drifts: list[tuple[int, int, float]],
    sigma_daily_bps: float = SIGMA_DAILY_BPS,
    market_returns: np.ndarray | None = None,
    beta: float = 1.0,
) -> np.ndarray:
    """Log mid on a 1 s grid; ``drifts`` are ``(i0, n, total_bps)`` linear drifts.

    With ``market_returns`` (an index's per-second log returns, already carrying any regime
    volatility multiplier, so the index and the stock see the same regime)
    the stock return is ``beta * r_m + idio`` with the idiosyncratic part scaled so the total
    variance stays ``sigma_daily^2`` per session when ``beta^2 <= 1``.
    """
    n = len(profile)
    per_sec = sigma_daily_bps / 10_000.0 / math.sqrt(SESSION_SECONDS)
    shocks = rng.normal(0.0, 1.0, n) * per_sec * np.sqrt(profile) * vol_mult
    if market_returns is not None:
        idio_share = math.sqrt(max(0.0, 1.0 - beta * beta))
        shocks = shocks * idio_share + beta * market_returns
    for i0, length, total_bps in drifts:
        lo, hi = max(0, i0), min(n, i0 + length)
        if hi > lo and length > 0:
            shocks[lo:hi] += (total_bps / 10_000.0) / length
    return np.exp(math.log(start_price) + np.cumsum(shocks))


def quote_arrays(
    rng: np.random.Generator,
    mids: np.ndarray,
    base_spread_bps: float,
    tob: int,
    spread_mult: np.ndarray,
    depth_mult: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(mids)
    spread_bps = base_spread_bps * spread_mult * rng.lognormal(0.0, 0.25, n)
    half = mids * spread_bps / 10_000.0 / 2.0
    bids = _round_tick(mids - half)
    asks = np.maximum(_round_tick(mids + half), bids + TICK)
    bid_sizes = np.maximum(
        100, (rng.lognormal(math.log(tob), 0.45, n) * depth_mult / 100).round() * 100
    ).astype(int)
    ask_sizes = np.maximum(
        100, (rng.lognormal(math.log(tob), 0.45, n) * depth_mult / 100).round() * 100
    ).astype(int)
    return bids, asks, bid_sizes, ask_sizes


def _schedule_weights(n_slices: int, front_load: float) -> np.ndarray:
    if front_load <= 0:
        return np.full(n_slices, 1.0 / n_slices)
    w = np.exp(-front_load * np.arange(n_slices) / n_slices)
    return np.asarray(w / w.sum())


def simulate_parent(
    rng: np.random.Generator,
    plan: ParentPlan,
    mids: np.ndarray,
    bids: np.ndarray,
    asks: np.ndarray,
    bid_sizes: np.ndarray,
    ask_sizes: np.ndarray,
    regime_mask: np.ndarray,
    regime: Regime,
    halted: np.ndarray | None = None,
) -> tuple[list[ChildRecord], list[FillRecord]]:
    """Slice a parent into children and fill them (aggressive at the far touch, passive at the near).

    Aggressive children cross the spread and walk the book by a Poisson number of ticks driven by
    child size over displayed size (plus venue degradation or latency inside the regime mask) and
    pay the access fee; passive children rest at the near touch, fill less often and earn the
    rebate. Sells mirror buys. Children never trade while ``halted`` (LULD pause).
    """
    n = len(mids)
    s = plan.side.sign
    # never slice finer than one round lot per child: a small parent gets fewer, wider slices
    slices = max(1, min(plan.duration_seconds // plan.child_interval, plan.quantity // 100))
    interval = max(1, plan.duration_seconds // slices) if plan.duration_seconds else plan.child_interval
    weights = _schedule_weights(slices, plan.front_load)
    children: list[ChildRecord] = []
    fills: list[FillRecord] = []
    filled_so_far = 0
    for k in range(slices):
        i = plan.start_index + k * interval + int(rng.integers(0, 5))
        if i >= n:
            break
        if halted is not None and halted[i]:
            continue
        remaining_parent = plan.quantity - filled_so_far
        if remaining_parent <= 0:
            break
        windowed = bool(regime_mask[i])
        child_qty = int(max(100, round(plan.quantity * weights[k] * rng.lognormal(0, 0.2) / 100) * 100))
        if rng.random() < ODD_LOT_PROB:
            child_qty += int(rng.integers(1, 100))  # odd-lot remainder
        child_qty = min(child_qty, remaining_parent)  # never send more than is left to do
        venue = str(rng.choice(VENUES, p=VENUE_WEIGHTS))
        cid = f"{plan.order_id}-C{k + 1:03d}"
        lat_mult = regime.latency_multiplier if windowed else 1.0
        latency_us = float(rng.lognormal(math.log(BASE_LATENCY_US * lat_mult), 0.3))
        aggressive = bool(rng.random() < plan.aggressive_prob)
        rejected = windowed and rng.random() < regime.reject_rate
        degraded = windowed and regime.degraded_venue == venue
        base_rate = BASE_FILL_RATE if aggressive else PASSIVE_FILL_RATE
        fill_rate = regime.degraded_fill_rate if degraded else base_rate
        filled = (not rejected) and rng.random() < fill_rate
        status = (
            OrderStatus.REJECTED if rejected else (OrderStatus.FILLED if filled else OrderStatus.CANCELLED)
        )
        far = asks[i] if s > 0 else bids[i]
        children.append(
            ChildRecord(cid, i, child_qty, venue, status, round(float(far) + s * 2 * TICK, 2), latency_us)
        )
        if not filled:
            continue
        fill_qty = (
            child_qty if rng.random() > 0.3 else int(max(1, round(child_qty * rng.uniform(0.6, 0.95))))
        )
        j = min(n - 1, i + max(1, int(latency_us / 1_000_000)))
        if halted is not None and halted[j]:
            continue
        filled_so_far += fill_qty
        degraded_ticks = (
            int(round(regime.degraded_extra_slippage_bps / 10_000.0 * mids[j] / TICK)) if degraded else 0
        )
        if aggressive:
            displayed = ask_sizes[j] if s > 0 else bid_sizes[j]
            pressure = min(3.0, max(0.3, 0.3 * fill_qty / max(100, displayed)))
            pressure *= math.sqrt(regime.volatility_multiplier) if windowed else 1.0
            extra_ticks = int(rng.poisson(pressure)) + degraded_ticks
            if windowed and regime.latency_multiplier > 1.5:
                # slow acks: the price moved before we arrived, realised as extra ticks
                extra_ticks += int(rng.poisson(0.5 * math.log(regime.latency_multiplier)))
            touch = asks[j] if s > 0 else bids[j]
            flag = "REMOVE"
        else:
            extra_ticks = degraded_ticks
            touch = bids[j] if s > 0 else asks[j]
            flag = "ADD"
        broker_cost = plan.broker_extra_bps / 10_000.0 * mids[j]
        price = round(float(touch) + s * (extra_ticks * TICK + broker_cost), 4)
        pieces = int(rng.integers(1, 4))
        remaining = fill_qty
        for p in range(pieces):
            part = remaining if p == pieces - 1 else max(1, int(round(remaining / (pieces - p))))
            part = min(part, remaining)
            if part <= 0:
                break
            remaining -= part
            # about one fill in five reports in the same microsecond as its sibling (duplicate ts)
            offset = latency_us / 1e6 + (0.0 if rng.random() < 0.2 else p * 0.05)
            fills.append(FillRecord(cid, j, part, price, venue, latency_us, flag, offset))
    return children, fills


# --------------------------------------------------------------------------- scenario generator
@dataclass
class _Segment:
    session: date
    start: datetime
    end: datetime
    current: bool


class SyntheticMarketGenerator:
    """Generates a :class:`SyntheticDataset` for a :class:`ScenarioSpec`."""

    def __init__(
        self,
        session_date: date = DEFAULT_SESSION_DATE,
        sim_hours: tuple[int, int] = (12, 16),
        reference_sessions: int = REFERENCE_SESSIONS,
        u_shape: bool = True,
    ) -> None:
        self.session_date = session_date
        self.sim_hours = sim_hours
        self.reference_sessions = reference_sessions
        self.u_shape = u_shape

    # ------------------------------------------------------------------ public
    def generate(self, spec: ScenarioSpec) -> SyntheticDataset:
        sym = spec.symbol
        d = self.session_date
        sim_start, sim_end = session_time(d, self.sim_hours[0]), session_time(d, self.sim_hours[1])
        dataset = SyntheticDataset(
            scenario=spec,
            symbol=sym,
            session_date=d,
            sim_start=sim_start,
            sim_end=sim_end,
            baseline_start=session_time(d, 13),
            baseline_end=session_time(d, 14),
            window_start=session_time(d, 14),
            window_end=session_time(d, 15),
            reference_sessions=previous_sessions(d, self.reference_sessions),
        )
        segments = [
            _Segment(p, session_time(p, 13, 58), session_time(p, 15, 6), False)
            for p in reversed(dataset.reference_sessions)
        ]
        segments.append(_Segment(d, sim_start, sim_end, True))
        current: dict[str, Any] = {}
        current_rng = np.random.default_rng(spec.seed)
        for k, seg in enumerate(segments):
            # the scenario session keeps the scenario seed; reference sessions get their own streams
            rng = current_rng if seg.current else np.random.default_rng([spec.seed, 7919, k])
            out = self._segment(rng, spec, seg, dataset)
            dataset.quotes.extend(out["quotes"])
            dataset.trades.extend(out["trades"])
            dataset.order_books.extend(out["books"])
            dataset.orders.extend(out["orders"])
            dataset.executions.extend(out["executions"])
            if seg.current:
                current = out
        dataset.trades.sort(key=lambda t: t.timestamp)
        dataset.quotes.sort(key=lambda q: q.timestamp)
        dataset.order_books.sort(key=lambda b: b.timestamp)
        dataset.metrics, dataset.logs, dataset.deployments = self._engineering(
            current_rng, spec, sym, sim_start, current["n"], current["in_window"], current["orders"]
        )
        dataset.positions, dataset.limits = self._risk(spec, sym, current["mid0"], sim_start)
        return dataset

    # ---------------------------------------------------------------- segment
    def _segment(
        self, rng: np.random.Generator, spec: ScenarioSpec, seg: _Segment, ds: SyntheticDataset
    ) -> dict[str, Any]:
        sym = spec.symbol
        side = Side(spec.side)
        n = int((seg.end - seg.start).total_seconds())
        t0 = seg.start.timestamp()
        ts = t0 + np.arange(n)
        open_ts = session_time(seg.session, 9, 30).timestamp()
        profile = intraday_profile(ts - open_ts, self.u_shape)
        if seg.current:
            ws, we = ds.window_start.timestamp(), ds.window_end.timestamp()
            in_window = (ts >= ws) & (ts < we)
        else:
            in_window = np.zeros(n, dtype=bool)

        def idx(when: datetime) -> int:
            return int(when.timestamp() - t0)

        plans: list[tuple[ParentPlan, datetime]] = []
        if seg.current:
            plans.append(
                (
                    ParentPlan(f"P-{sym}-1300", side, BASE_PARENT_QTY, idx(ds.baseline_start), 3600),
                    ds.baseline_start,
                )
            )
            qty = int(BASE_PARENT_QTY * spec.order_size_multiplier)
            plans.append(
                (ParentPlan(f"P-{sym}-1400", side, qty, idx(ds.window_start), 3600), ds.window_start)
            )
        else:
            start = session_time(seg.session, 14)
            qty = int(round(BASE_PARENT_QTY * float(rng.lognormal(0.0, 0.15)) / 100) * 100)
            plans.append(
                (ParentPlan(f"P-{sym}-{seg.session:%Y%m%d}-1400", side, qty, idx(start), 3600), start)
            )

        drifts = [
            (p.start_index, p.duration_seconds, side.sign * expected_impact_drift_bps(p.quantity))
            for p, _ in plans
        ]
        if seg.current and spec.price_move_bps:
            start_i = idx(ds.window_start) + 15 * 60
            drifts.append((start_i, spec.price_move_minutes * 60, side.sign * spec.price_move_bps))
        vol_mult = np.where(in_window, spec.volatility_multiplier, 1.0)
        start_price = BASE_PRICES[sym] * (1.0 if seg.current else float(rng.lognormal(0.0, 0.01)))
        mids = mid_path(rng, start_price, profile, vol_mult, drifts)

        vol_coupling = 1.0 + 0.15 * (spec.volatility_multiplier - 1.0)
        spread_mult = np.where(in_window, spec.spread_multiplier * vol_coupling, 1.0)
        depth_mult = np.where(in_window, spec.depth_multiplier, 1.0)
        bids, asks, bid_sizes, ask_sizes = quote_arrays(
            rng, mids, BASE_SPREAD_BPS[sym], BASE_TOB_SIZE[sym], spread_mult, depth_mult
        )
        clean_bids, clean_asks = bids.copy(), asks.copy()
        if seg.current:
            bids, asks, bid_sizes, ask_sizes = self._inject_md_anomalies(
                rng, spec, in_window, bids, asks, bid_sizes, ask_sizes
            )

        trades = self._trades(rng, sym, ts, clean_bids, clean_asks, profile)
        regime = Regime.of(spec)
        orders: list[Order] = []
        executions: list[Execution] = []
        for plan, start in plans:
            children, fills = simulate_parent(
                rng, plan, mids, clean_bids, clean_asks, bid_sizes, ask_sizes, in_window, regime
            )
            o, e, own = self._materialise(sym, plan, start, children, fills, ts, mids)
            orders.extend(o)
            executions.extend(e)
            trades.extend(own)
            ds.truth[plan.order_id] = {
                "side": side.value,
                "quantity": plan.quantity,
                "impact_drift_bps": expected_impact_drift_bps(plan.quantity),
                "sigma_daily_bps": SIGMA_DAILY_BPS,
                "adv_shares": ADV_SHARES,
                "aggressive_prob": plan.aggressive_prob,
                "session": seg.session.isoformat(),
            }
        if seg.current and seg.end >= session_time(seg.session, 16):
            trades.append(
                Trade(
                    sym,
                    session_time(seg.session, 16),
                    round(float(mids[-1]), 2),
                    int(round(ADV_SHARES * CLOSING_AUCTION_ADV_FRACTION / 100) * 100),
                    "XNAS",
                    trade_id=f"T-{sym}-AUCTION-CLOSE",
                    condition="AUCTION_CLOSE",
                )
            )
        trades.sort(key=lambda t: t.timestamp)
        quotes = self._quote_objects(sym, ts, bids, asks, bid_sizes, ask_sizes, trades)
        books = self._order_books(rng, spec, sym, ts, bids, asks, bid_sizes, ask_sizes, in_window)
        return {
            "quotes": quotes,
            "trades": trades,
            "books": books,
            "orders": orders,
            "executions": executions,
            "n": n,
            "in_window": in_window,
            "mid0": float(mids[0]),
        }

    @staticmethod
    def _materialise(
        sym: str,
        plan: ParentPlan,
        start: datetime,
        children: list[ChildRecord],
        fills: list[FillRecord],
        ts: np.ndarray,
        mids: np.ndarray,
    ) -> tuple[list[Order], list[Execution], list[Trade]]:
        i_dec = max(0, plan.start_index - DECISION_DELAY_SECONDS)
        filled_total = sum(f.quantity for f in fills)
        parent = Order(
            order_id=plan.order_id,
            parent_order_id=None,
            symbol=sym,
            side=plan.side,
            quantity=plan.quantity,
            strategy=plan.algo,
            venue=None,
            timestamp=start,
            limit_price=None,
            status=OrderStatus.FILLED if filled_total >= plan.quantity else OrderStatus.PARTIALLY_FILLED,
            trader="trader.a",
            account="CASH_EQ_1",
            end_time=start + timedelta(seconds=plan.duration_seconds),
            decision_price=round(float(mids[i_dec]), 4),
            decision_time=datetime.fromtimestamp(float(ts[i_dec]), tz=NY),
            adv_shares=ADV_SHARES,
            sigma_daily_bps=SIGMA_DAILY_BPS,
            broker=plan.broker,
        )
        orders = [parent]
        for c in children:
            orders.append(
                Order(
                    order_id=c.order_id,
                    parent_order_id=plan.order_id,
                    symbol=sym,
                    side=plan.side,
                    quantity=c.quantity,
                    strategy=plan.algo,
                    venue=c.venue,
                    timestamp=datetime.fromtimestamp(float(ts[c.index]), tz=NY),
                    limit_price=c.limit_price,
                    status=c.status,
                    trader="trader.a",
                    account="CASH_EQ_1",
                    broker=plan.broker,
                )
            )
        executions: list[Execution] = []
        own: list[Trade] = []
        for k, f in enumerate(fills):
            eid = f"E-{plan.order_id}-{k + 1:04d}"
            ets = datetime.fromtimestamp(float(ts[f.index]) + f.offset_s, tz=NY)
            executions.append(
                Execution(
                    execution_id=eid,
                    order_id=f.order_id,
                    symbol=sym,
                    side=plan.side,
                    quantity=f.quantity,
                    price=f.price,
                    venue=f.venue,
                    timestamp=ets,
                    parent_order_id=plan.order_id,
                    latency_us=round(f.latency_us, 1),
                    liquidity_flag=f.liquidity_flag,
                )
            )
            own.append(
                Trade(
                    sym,
                    ets,
                    round(f.price, 2),
                    f.quantity,
                    f.venue,
                    trade_id=f"T-{eid}",
                    condition="ODD_LOT" if f.quantity < 100 else None,
                )
            )
        return orders, executions, own

    # ---------------------------------------------------------------- quotes
    def _inject_md_anomalies(
        self,
        rng: np.random.Generator,
        spec: ScenarioSpec,
        in_window: np.ndarray,
        bids: np.ndarray,
        asks: np.ndarray,
        bid_sizes: np.ndarray,
        ask_sizes: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        idx = np.flatnonzero(in_window)
        if spec.stale_quote_fraction > 0 and idx.size:
            stale_seconds = int(spec.stale_quote_fraction * idx.size)
            run = 6
            starts = rng.choice(idx[: -run - 1], size=max(1, stale_seconds // run), replace=False)
            for s in np.sort(starts):
                for k in range(1, run + 1):
                    i = s + k
                    bids[i], asks[i], bid_sizes[i], ask_sizes[i] = (
                        bids[s],
                        asks[s],
                        bid_sizes[s],
                        ask_sizes[s],
                    )
        if spec.crossed_quotes > 0 and idx.size:
            for i in rng.choice(idx, size=spec.crossed_quotes, replace=False):
                bids[i] = asks[i] + 2 * TICK
        return bids, asks, bid_sizes, ask_sizes

    def _quote_objects(
        self,
        sym: str,
        ts: np.ndarray,
        bids: np.ndarray,
        asks: np.ndarray,
        bid_sizes: np.ndarray,
        ask_sizes: np.ndarray,
        trades: list[Trade],
    ) -> list[Quote]:
        regular = [t for t in trades if t.condition not in EXCLUDED_CONDITIONS]
        trade_ts = np.array([t.timestamp.timestamp() for t in regular])
        trade_px = np.array([t.price for t in regular])
        trade_qty = np.array([t.quantity for t in regular])
        cum_vol = np.cumsum(trade_qty) if trade_qty.size else np.array([])
        js = (np.searchsorted(trade_ts, ts, side="right") - 1) if trade_ts.size else np.full(len(ts), -1)
        bid_l = np.round(bids, 2).tolist()
        ask_l = np.round(asks, 2).tolist()
        px_l = np.round(trade_px, 2).tolist() if trade_ts.size else []
        quotes: list[Quote] = []
        for i, j in enumerate(js.tolist()):
            quotes.append(
                Quote(
                    symbol=sym,
                    timestamp=datetime.fromtimestamp(float(ts[i]), tz=NY),
                    bid=bid_l[i],
                    bid_size=int(bid_sizes[i]),
                    ask=ask_l[i],
                    ask_size=int(ask_sizes[i]),
                    last_price=px_l[j] if j >= 0 else None,
                    last_size=int(trade_qty[j]) if j >= 0 else None,
                    volume=int(cum_vol[j]) if j >= 0 else 0,
                )
            )
        return quotes

    # ---------------------------------------------------------------- trades
    def _trades(
        self,
        rng: np.random.Generator,
        sym: str,
        ts: np.ndarray,
        bids: np.ndarray,
        asks: np.ndarray,
        profile: np.ndarray,
    ) -> list[Trade]:
        """Market prints: U-shaped Poisson arrivals, odd lots flagged, a few late prints.

        A late print carries the price of a quote ``LATE_PRINT_LAG_SECONDS`` earlier, as a
        trade reported out of sequence does; interval VWAP excludes it by default.
        """
        counts = rng.poisson(TRADES_PER_SECOND * profile)
        trades: list[Trade] = []
        seq = 0
        for i, c in enumerate(counts):
            if c == 0:
                continue
            sides = rng.random(c) < 0.5
            qty = np.maximum(
                10, (rng.lognormal(math.log(MEAN_TRADE_SIZE), TRADE_SIZE_LOG_SD, c) / 10).round() * 10
            ).astype(int)
            venues = rng.choice(VENUES, size=c, p=VENUE_WEIGHTS)
            offsets = np.sort(rng.random(c))
            late = rng.random(c) < LATE_PRINT_PROB
            for k in range(c):
                seq += 1
                src = max(0, i - LATE_PRINT_LAG_SECONDS) if late[k] else i
                px = asks[src] if sides[k] else bids[src]
                condition = "LATE" if late[k] else ("ODD_LOT" if qty[k] < 100 else None)
                trades.append(
                    Trade(
                        symbol=sym,
                        timestamp=datetime.fromtimestamp(float(ts[i] + offsets[k]), tz=NY),
                        price=round(float(px), 2),
                        quantity=int(qty[k]),
                        venue=str(venues[k]),
                        trade_id=f"T-{sym}-{ts[0]:.0f}-{seq:07d}",
                        condition=condition,
                    )
                )
        return trades

    # ----------------------------------------------------------- order books
    def _order_books(
        self,
        rng: np.random.Generator,
        spec: ScenarioSpec,  # noqa: ARG002 - kept for signature symmetry / future regimes
        sym: str,
        ts: np.ndarray,
        bids: np.ndarray,
        asks: np.ndarray,
        bid_sizes: np.ndarray,
        ask_sizes: np.ndarray,
        in_window: np.ndarray,  # noqa: ARG002 - kept for signature symmetry / future regimes
        every: int = 10,
        levels: int = 5,
    ) -> list[OrderBookSnapshot]:
        idx = np.arange(0, len(ts), every)
        growth = 1.0 + 0.4 * np.arange(levels)
        noise = rng.lognormal(0.0, 0.2, size=(idx.size, levels, 2))
        # top-of-book sizes already carry the scenario depth multiplier; deeper levels scale from
        # them so the whole book thins out together
        bq = np.maximum(100, np.round(bid_sizes[idx, None] * growth * noise[:, :, 0] / 100) * 100).astype(int)
        aq = np.maximum(100, np.round(ask_sizes[idx, None] * growth * noise[:, :, 1] / 100) * 100).astype(int)
        bpx = np.round(bids[idx, None] - np.arange(levels) * TICK, 2)
        apx = np.round(asks[idx, None] + np.arange(levels) * TICK, 2)
        books: list[OrderBookSnapshot] = []
        for r, i in enumerate(idx.tolist()):
            lv: list[OrderBookLevel] = []
            for k in range(levels):
                lv.append(OrderBookLevel("BID", k + 1, float(bpx[r, k]), int(bq[r, k])))
                lv.append(OrderBookLevel("ASK", k + 1, float(apx[r, k]), int(aq[r, k])))
            books.append(
                OrderBookSnapshot(
                    symbol=sym, timestamp=datetime.fromtimestamp(float(ts[i]), tz=NY), levels=tuple(lv)
                )
            )
        return books

    # ----------------------------------------------------------- engineering
    def _engineering(
        self,
        rng: np.random.Generator,
        spec: ScenarioSpec,
        sym: str,
        sim_start: datetime,
        n: int,
        in_window: np.ndarray,
        orders: list[Order],
    ) -> tuple[list[MetricPoint], list[LogEntry], list[Deployment]]:
        metrics: list[MetricPoint] = []
        logs: list[LogEntry] = []
        deployments: list[Deployment] = [
            Deployment(
                timestamp=london(self.session_date, 9, 30),
                service="risk-service",
                version="3.2.0",
                change_id="CHG-40311",
                description="Scheduled release: limit-check caching improvements",
                author="platform-bot",
            )
        ]
        if spec.deployment_in_window:
            deployments.append(
                Deployment(
                    timestamp=london(self.session_date, 13, 52),
                    service="smart-order-router",
                    version="2.14.1",
                    change_id="CHG-40388",
                    description="Routing latency budget and venue timeout changes",
                    author="sor-team",
                )
            )
        rejected_by_minute: dict[int, int] = {}
        for o in orders:
            if o.status is OrderStatus.REJECTED:
                m = int((o.timestamp.timestamp() - sim_start.timestamp()) // 60)
                rejected_by_minute[m] = rejected_by_minute.get(m, 0) + 1

        for m in range(n // 60):
            when = sim_start + timedelta(minutes=m)
            windowed = bool(in_window[m * 60])
            lat_mult = spec.latency_multiplier if windowed else 1.0
            p50 = float(rng.lognormal(math.log(BASE_LATENCY_US * lat_mult), 0.1))
            p99 = p50 * float(rng.uniform(2.5, 3.5))
            metrics.append(MetricPoint(when, "order-gateway", "ack_latency_p50_us", round(p50, 1)))
            metrics.append(MetricPoint(when, "order-gateway", "ack_latency_p99_us", round(p99, 1)))
            metrics.append(
                MetricPoint(when, "order-gateway", "reject_count", float(rejected_by_minute.get(m, 0)))
            )
            route = float(rng.lognormal(math.log(350.0 * lat_mult), 0.1))
            metrics.append(MetricPoint(when, "smart-order-router", "route_latency_us", round(route, 1)))
            metrics.append(
                MetricPoint(
                    when,
                    "smart-order-router",
                    "cpu_percent",
                    round(float(rng.uniform(25, 40)) * (1.6 if windowed and lat_mult > 1 else 1.0), 1),
                )
            )
            gaps = float(rng.poisson(3.0)) if (windowed and spec.stale_quote_fraction > 0) else 0.0
            metrics.append(MetricPoint(when, "market-data-adapter", "feed_gap_count", gaps, {"symbol": sym}))
            metrics.append(
                MetricPoint(
                    when,
                    "market-data-adapter",
                    "quote_rate_per_s",
                    round(float(rng.normal(60, 5)) * (0.85 if gaps else 1.0), 1),
                    {"symbol": sym},
                )
            )

            if m % 5 == 0:
                logs.append(
                    LogEntry(
                        when,
                        "order-gateway",
                        "INFO",
                        f"heartbeat ok sessions=4 symbol_count=6 p50_us={p50:.0f}",
                    )
                )
            if windowed and lat_mult > 1.5 and m % 2 == 0:
                logs.append(
                    LogEntry(
                        when,
                        "order-gateway",
                        "WARN",
                        f"ack latency SLO breached: p99={p99:.0f}us (slo=3000us)",
                        {"symbol": sym},
                    )
                )
                logs.append(
                    LogEntry(
                        when,
                        "smart-order-router",
                        "ERROR",
                        "venue timeout waiting for ack; retrying route",
                        {"symbol": sym, "venue": "XNAS"},
                    )
                )
            if windowed and spec.reject_rate > 0 and rejected_by_minute.get(m):
                logs.append(
                    LogEntry(
                        when,
                        "order-gateway",
                        "ERROR",
                        f"order rejected reason=TOO_LATE_TO_ENTER count={rejected_by_minute[m]}",
                        {"symbol": sym},
                    )
                )
            if windowed and spec.stale_quote_fraction > 0 and m % 3 == 0:
                logs.append(
                    LogEntry(
                        when,
                        "market-data-adapter",
                        "WARN",
                        f"feed gap detected for {sym}: sequence gap on consolidated feed",
                        {"symbol": sym},
                    )
                )
            if windowed and spec.crossed_quotes > 0 and m % 7 == 0:
                logs.append(
                    LogEntry(
                        when,
                        "market-data-adapter",
                        "WARN",
                        f"crossed NBBO observed for {sym}; quote suppressed",
                        {"symbol": sym},
                    )
                )
            if windowed and spec.degraded_venue and m % 4 == 0:
                logs.append(
                    LogEntry(
                        when,
                        "smart-order-router",
                        "WARN",
                        f"venue {spec.degraded_venue}: elevated cancel-on-timeout ratio",
                        {"symbol": sym, "venue": spec.degraded_venue},
                    )
                )
        return metrics, logs, deployments

    # ------------------------------------------------------------------ risk
    def _risk(
        self, spec: ScenarioSpec, sym: str, ref_price: float, as_of: datetime
    ) -> tuple[list[Position], list[RiskLimit]]:
        positions = [
            Position(
                symbol=sym, quantity=120_000, average_price=round(float(ref_price) * 0.985, 2), as_of=as_of
            )
        ]
        limits = [
            RiskLimit(
                symbol=sym,
                max_position=500_000,
                max_order_quantity=250_000,
                max_participation_rate=0.25,
                max_notional=150_000_000.0,
            )
        ]
        return positions, limits
