"""Synthetic but realistic cash-equity data generator.

The generator is seeded and fully deterministic. Every scenario effect is
localised to the *investigation window* so a baseline window immediately
before it is always available for comparison.

Timeline (Europe/London):

    12:00        13:00          14:00          15:00        16:00
    |-- warm-up --|-- baseline --|--- window ---|-- post ----|
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from ceap.data.scenarios import BASE_PRICES, VENUE_WEIGHTS, VENUES, ScenarioSpec
from ceap.domain.engineering import Deployment, LogEntry, MetricPoint
from ceap.domain.execution import Execution, Order, OrderStatus, Side
from ceap.domain.market import OrderBookLevel, OrderBookSnapshot, Quote, Trade
from ceap.domain.risk import Position, RiskLimit

LONDON = ZoneInfo("Europe/London")
TICK = 0.01
DEFAULT_SESSION_DATE = date(2026, 9, 18)

ANNUAL_VOL = 0.10
SECONDS_PER_YEAR_TRADING = 252 * 23_400
BASE_SPREAD_BPS = {"AAPL": 1.8, "MSFT": 1.6, "NVDA": 2.4, "AMZN": 2.0, "META": 2.2, "GOOGL": 2.0}
BASE_TOB_SIZE = {"AAPL": 450, "MSFT": 350, "NVDA": 900, "AMZN": 400, "META": 200, "GOOGL": 500}
TRADES_PER_SECOND = 1.5
MEAN_TRADE_SIZE = 150
BASE_PARENT_QTY = 60_000
CHILD_INTERVAL_SECONDS = 30
BASE_FILL_RATE = 0.92
BASE_LATENCY_US = 800.0
IMPACT_COEFFICIENT_BPS = 80.0
NORMAL_PARTICIPATION = 0.08


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
    return datetime(session_date.year, session_date.month, session_date.day, hh, mm, tzinfo=LONDON)


def _round_tick(x: np.ndarray | float) -> np.ndarray:
    return np.round(np.asarray(x) / TICK) * TICK


class SyntheticMarketGenerator:
    """Generates a :class:`SyntheticDataset` for a :class:`ScenarioSpec`."""

    def __init__(
        self, session_date: date = DEFAULT_SESSION_DATE, sim_hours: tuple[int, int] = (12, 16)
    ) -> None:
        self.session_date = session_date
        self.sim_hours = sim_hours

    # ------------------------------------------------------------------ public
    def generate(self, spec: ScenarioSpec) -> SyntheticDataset:
        rng = np.random.default_rng(spec.seed)
        sym = spec.symbol
        sim_start = london(self.session_date, self.sim_hours[0])
        sim_end = london(self.session_date, self.sim_hours[1])
        baseline_start, baseline_end = london(self.session_date, 13), london(self.session_date, 14)
        window_start, window_end = london(self.session_date, 14), london(self.session_date, 15)

        n = int((sim_end - sim_start).total_seconds())
        t0 = sim_start.timestamp()
        ts = t0 + np.arange(n)
        in_window = (ts >= window_start.timestamp()) & (ts < window_end.timestamp())

        parent_qty_window = int(BASE_PARENT_QTY * spec.order_size_multiplier)
        expected_hour_volume = 3600 * TRADES_PER_SECOND * MEAN_TRADE_SIZE
        participation = parent_qty_window / (expected_hour_volume + parent_qty_window)

        mids = self._mid_path(rng, spec, n, ts, in_window, participation, window_start)
        bids, asks, bid_sizes, ask_sizes, spread_bps_series = self._quotes(rng, spec, sym, mids, in_window)
        clean_bids, clean_asks = bids.copy(), asks.copy()
        bids, asks, bid_sizes, ask_sizes = self._inject_md_anomalies(
            rng, spec, in_window, bids, asks, bid_sizes, ask_sizes
        )

        dataset = SyntheticDataset(
            scenario=spec,
            symbol=sym,
            session_date=self.session_date,
            sim_start=sim_start,
            sim_end=sim_end,
            baseline_start=baseline_start,
            baseline_end=baseline_end,
            window_start=window_start,
            window_end=window_end,
        )

        trades = self._trades(rng, sym, ts, clean_bids, clean_asks)
        orders, executions, own_trades = self._orders_and_fills(
            rng,
            spec,
            sym,
            ts,
            mids,
            clean_asks,
            ask_sizes,
            in_window,
            parent_qty_window,
            baseline_start,
            window_start,
        )
        trades.extend(own_trades)
        trades.sort(key=lambda t: t.timestamp)

        quotes = self._quote_objects(sym, ts, bids, asks, bid_sizes, ask_sizes, trades)
        books = self._order_books(rng, spec, sym, ts, bids, asks, bid_sizes, ask_sizes, in_window)

        dataset.quotes = quotes
        dataset.trades = trades
        dataset.order_books = books
        dataset.orders = orders
        dataset.executions = executions
        dataset.metrics, dataset.logs, dataset.deployments = self._engineering(
            rng, spec, sym, sim_start, n, in_window, orders
        )
        dataset.positions, dataset.limits = self._risk(spec, sym, mids[0], sim_start)
        return dataset

    # ------------------------------------------------------------ price path
    def _mid_path(
        self,
        rng: np.random.Generator,
        spec: ScenarioSpec,
        n: int,
        ts: np.ndarray,
        in_window: np.ndarray,
        participation: float,
        window_start: datetime,
    ) -> np.ndarray:
        per_sec_vol = ANNUAL_VOL / math.sqrt(SECONDS_PER_YEAR_TRADING)
        vol = np.where(in_window, per_sec_vol * spec.volatility_multiplier, per_sec_vol)
        returns = rng.normal(0.0, 1.0, n) * vol

        # exogenous adverse trend (buy-side adverse = up)
        if spec.price_move_bps:
            start = window_start.timestamp() + 15 * 60
            secs = spec.price_move_minutes * 60
            mask = (ts >= start) & (ts < start + secs)
            returns[mask] += (spec.price_move_bps / 10_000.0) / secs

        # permanent impact of our own trading when participation is elevated
        if participation > NORMAL_PARTICIPATION:
            drift_bps = IMPACT_COEFFICIENT_BPS * (participation - NORMAL_PARTICIPATION)
            returns[in_window] += (drift_bps / 10_000.0) / in_window.sum()

        log_mid = math.log(BASE_PRICES[spec.symbol]) + np.cumsum(returns)
        return np.exp(log_mid)

    # ---------------------------------------------------------------- quotes
    def _quotes(
        self, rng: np.random.Generator, spec: ScenarioSpec, sym: str, mids: np.ndarray, in_window: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        n = len(mids)
        base_bps = BASE_SPREAD_BPS[sym]
        vol_coupling = 1.0 + 0.15 * (spec.volatility_multiplier - 1.0)
        mult = np.where(in_window, spec.spread_multiplier * vol_coupling, 1.0)
        noise = rng.lognormal(0.0, 0.25, n)
        spread_bps = base_bps * mult * noise
        half = mids * spread_bps / 10_000.0 / 2.0
        bids = _round_tick(mids - half)
        asks = _round_tick(mids + half)
        asks = np.maximum(asks, bids + TICK)

        tob = BASE_TOB_SIZE[sym]
        depth_mult = np.where(in_window, spec.depth_multiplier, 1.0)
        bid_sizes = np.maximum(
            100, (rng.lognormal(math.log(tob), 0.45, n) * depth_mult / 100).round() * 100
        ).astype(int)
        ask_sizes = np.maximum(
            100, (rng.lognormal(math.log(tob), 0.45, n) * depth_mult / 100).round() * 100
        ).astype(int)
        return bids, asks, bid_sizes, ask_sizes, spread_bps

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
        trade_ts = np.array([t.timestamp.timestamp() for t in trades])
        trade_px = np.array([t.price for t in trades])
        trade_qty = np.array([t.quantity for t in trades])
        cum_vol = np.cumsum(trade_qty) if trade_qty.size else np.array([])
        quotes: list[Quote] = []
        for i in range(len(ts)):
            when = datetime.fromtimestamp(float(ts[i]), tz=LONDON)
            j = int(np.searchsorted(trade_ts, ts[i], side="right")) - 1 if trade_ts.size else -1
            quotes.append(
                Quote(
                    symbol=sym,
                    timestamp=when,
                    bid=round(float(bids[i]), 2),
                    bid_size=int(bid_sizes[i]),
                    ask=round(float(asks[i]), 2),
                    ask_size=int(ask_sizes[i]),
                    last_price=round(float(trade_px[j]), 2) if j >= 0 else None,
                    last_size=int(trade_qty[j]) if j >= 0 else None,
                    volume=int(cum_vol[j]) if j >= 0 else 0,
                )
            )
        return quotes

    # ---------------------------------------------------------------- trades
    def _trades(
        self, rng: np.random.Generator, sym: str, ts: np.ndarray, bids: np.ndarray, asks: np.ndarray
    ) -> list[Trade]:
        counts = rng.poisson(TRADES_PER_SECOND, len(ts))
        trades: list[Trade] = []
        seq = 0
        for i, c in enumerate(counts):
            if c == 0:
                continue
            sides = rng.random(c) < 0.5
            qty = np.maximum(10, (rng.lognormal(math.log(MEAN_TRADE_SIZE), 0.6, c) / 10).round() * 10).astype(
                int
            )
            venues = rng.choice(VENUES, size=c, p=VENUE_WEIGHTS)
            offsets = np.sort(rng.random(c))
            for k in range(c):
                seq += 1
                px = asks[i] if sides[k] else bids[i]
                trades.append(
                    Trade(
                        symbol=sym,
                        timestamp=datetime.fromtimestamp(float(ts[i] + offsets[k]), tz=LONDON),
                        price=round(float(px), 2),
                        quantity=int(qty[k]),
                        venue=str(venues[k]),
                        trade_id=f"T-{sym}-{seq:07d}",
                    )
                )
        return trades

    # ----------------------------------------------------------- order books
    def _order_books(
        self,
        rng: np.random.Generator,
        spec: ScenarioSpec,
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
        books: list[OrderBookSnapshot] = []
        for i in range(0, len(ts), every):
            lv: list[OrderBookLevel] = []
            # top-of-book sizes already carry the scenario depth multiplier; deeper
            # levels scale from them so the whole book thins out together.
            for k in range(1, levels + 1):
                growth = 1.0 + 0.4 * (k - 1)
                bq = int(max(100, round(bid_sizes[i] * growth * rng.lognormal(0, 0.2) / 100) * 100))
                aq = int(max(100, round(ask_sizes[i] * growth * rng.lognormal(0, 0.2) / 100) * 100))
                lv.append(OrderBookLevel("BID", k, round(float(bids[i]) - (k - 1) * TICK, 2), bq))
                lv.append(OrderBookLevel("ASK", k, round(float(asks[i]) + (k - 1) * TICK, 2), aq))
            books.append(
                OrderBookSnapshot(
                    symbol=sym, timestamp=datetime.fromtimestamp(float(ts[i]), tz=LONDON), levels=tuple(lv)
                )
            )
        return books

    # ------------------------------------------------------ orders and fills
    def _orders_and_fills(
        self,
        rng: np.random.Generator,
        spec: ScenarioSpec,
        sym: str,
        ts: np.ndarray,
        mids: np.ndarray,
        asks: np.ndarray,
        ask_sizes: np.ndarray,
        in_window: np.ndarray,
        parent_qty_window: int,
        baseline_start: datetime,
        window_start: datetime,
    ) -> tuple[list[Order], list[Execution], list[Trade]]:
        orders: list[Order] = []
        executions: list[Execution] = []
        own_trades: list[Trade] = []
        t0 = ts[0]

        parents = [
            ("baseline", baseline_start, BASE_PARENT_QTY),
            ("window", window_start, parent_qty_window),
        ]
        exec_seq = 0
        for _tag, start, qty in parents:
            pid = f"P-{sym}-{start.strftime('%H%M')}"
            i0 = int(start.timestamp() - t0)
            parent = Order(
                order_id=pid,
                parent_order_id=None,
                symbol=sym,
                side=Side.BUY,
                quantity=qty,
                strategy="VWAP",
                venue=None,
                timestamp=start,
                limit_price=None,
                status=OrderStatus.NEW,
                trader="trader.a",
                account="CASH_EQ_1",
                end_time=start + timedelta(hours=1),
                decision_price=round(float(mids[i0]), 4),
            )
            orders.append(parent)
            slices = 3600 // CHILD_INTERVAL_SECONDS
            base_slice = qty / slices
            filled_total = 0
            for s in range(slices):
                i = i0 + s * CHILD_INTERVAL_SECONDS + int(rng.integers(0, 5))
                if i >= len(ts):
                    break
                windowed = bool(in_window[i])
                child_qty = int(max(100, round(base_slice * rng.lognormal(0, 0.2) / 100) * 100))
                venue = str(rng.choice(VENUES, p=VENUE_WEIGHTS))
                cid = f"{pid}-C{s + 1:03d}"
                when = datetime.fromtimestamp(float(ts[i]), tz=LONDON)
                latency_mult = spec.latency_multiplier if windowed else 1.0
                latency_us = float(rng.lognormal(math.log(BASE_LATENCY_US * latency_mult), 0.3))
                rejected = windowed and rng.random() < spec.reject_rate
                degraded = windowed and spec.degraded_venue == venue
                fill_rate = spec.degraded_fill_rate if degraded else BASE_FILL_RATE
                filled = (not rejected) and rng.random() < fill_rate
                status = (
                    OrderStatus.REJECTED
                    if rejected
                    else (OrderStatus.FILLED if filled else OrderStatus.CANCELLED)
                )
                orders.append(
                    Order(
                        order_id=cid,
                        parent_order_id=pid,
                        symbol=sym,
                        side=Side.BUY,
                        quantity=child_qty,
                        strategy="VWAP",
                        venue=venue,
                        timestamp=when,
                        limit_price=round(float(asks[i]) + 2 * TICK, 2),
                        status=status,
                        trader="trader.a",
                        account="CASH_EQ_1",
                    )
                )
                if not filled:
                    continue
                fill_qty = (
                    child_qty
                    if rng.random() > 0.3
                    else int(max(100, round(child_qty * rng.uniform(0.6, 0.95) / 100) * 100))
                )
                filled_total += fill_qty
                # execution price: cross the spread, then walk the book by an amount driven
                # by child size relative to displayed liquidity (+ venue-specific degradation)
                j = min(len(ts) - 1, i + max(1, int(latency_us / 1_000_000)))
                pressure = min(3.0, max(0.3, 0.3 * fill_qty / max(100, ask_sizes[j])))
                pressure *= math.sqrt(spec.volatility_multiplier) if windowed else 1.0
                extra_ticks = int(rng.poisson(pressure))
                if degraded:
                    extra_ticks += int(round(spec.degraded_extra_slippage_bps / 10_000.0 * mids[j] / TICK))
                if windowed and spec.latency_multiplier > 1.5:
                    # slow acks => price moved before we arrived; realised as extra ticks
                    extra_ticks += int(rng.poisson(0.5 * math.log(spec.latency_multiplier)))
                price = round(float(asks[j]) + extra_ticks * TICK, 2)
                pieces = int(rng.integers(1, 4))
                remaining = fill_qty
                for p in range(pieces):
                    part = (
                        remaining
                        if p == pieces - 1
                        else int(max(100, round(remaining / (pieces - p) / 100) * 100))
                    )
                    part = min(part, remaining)
                    if part <= 0:
                        break
                    remaining -= part
                    exec_seq += 1
                    ets = datetime.fromtimestamp(float(ts[j]) + latency_us / 1e6 + p * 0.05, tz=LONDON)
                    executions.append(
                        Execution(
                            execution_id=f"E-{sym}-{exec_seq:06d}",
                            order_id=cid,
                            symbol=sym,
                            side=Side.BUY,
                            quantity=part,
                            price=price,
                            venue=venue,
                            timestamp=ets,
                            parent_order_id=pid,
                            latency_us=round(latency_us, 1),
                            liquidity_flag="REMOVE",
                        )
                    )
                    own_trades.append(
                        Trade(sym, ets, price, part, venue, trade_id=f"T-{sym}-OWN-{exec_seq:06d}")
                    )
            status = OrderStatus.FILLED if filled_total >= qty else OrderStatus.PARTIALLY_FILLED
            orders[orders.index(parent)] = replace(parent, status=status)
        return orders, executions, own_trades

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
