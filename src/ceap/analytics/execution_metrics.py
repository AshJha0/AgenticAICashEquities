"""Standard execution-quality analytics (TCA).

Given orders, executions and market data for a window this produces the full
:class:`ExecutionMetrics` bundle plus per-venue statistics. Since R2T it also carries the Perold
components with delay and fees (2T.3), the pre-trade estimate with the analytic standard error
and the residual z (2T.4, 2T.8), size-weighted effective and realised spreads, price improvement,
markouts at 1 s / 10 s / 60 s / 5 min and venue statistics conditioned on order type and size (2T.5).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from ceap.analytics.implementation_shortfall import DEFAULT_FEES, FeeSchedule, shortfall_components
from ceap.analytics.liquidity import average_spread_bps, participation_rate
from ceap.analytics.slippage import (
    effective_spread_bps,
    markout_bps,
    per_fill_slippage_bps,
    price_improvement_bps,
    realised_spread_bps,
    slippage_bps,
)
from ceap.analytics.volatility import price_drift_bps, realised_volatility_bps
from ceap.analytics.vwap import vwap
from ceap.domain.analytics import ExecutionAnalytics
from ceap.domain.execution import Execution, ExecutionMetrics, Order, OrderStatus, Side, VenueStatistics
from ceap.domain.market import Quote, Trade

#: sale conditions excluded from interval VWAP and market volume by default (2T.1)
EXCLUDED_TRADE_CONDITIONS = frozenset({"AUCTION_OPEN", "AUCTION_CLOSE", "LATE"})
MARKOUT_HORIZONS_SECONDS: tuple[int, ...] = (1, 10, 60, 300)
SESSION_SECONDS = 23_400
PRETRADE_ALGOS = ("VWAP", "POV", "IS", "MOC")


def interval_vwap(
    trades: Sequence[Trade], exclude_conditions: frozenset[str] = EXCLUDED_TRADE_CONDITIONS
) -> tuple[float, float]:
    """Interval VWAP and volume of regular prints; auction and late prints are excluded by default."""
    kept = [t for t in trades if getattr(t, "condition", None) not in exclude_conditions]
    return vwap([t.price for t in kept], [t.quantity for t in kept]), float(sum(t.quantity for t in kept))


def size_bucket(quantity: float) -> str:
    """Fill size bucket for conditioned venue statistics (round lots)."""
    if quantity < 100:
        return "odd"
    if quantity < 500:
        return "small"
    if quantity < 2_000:
        return "medium"
    return "large"


class QuoteIndex:
    """Fast "prevailing quote at time t" lookup via ``searchsorted``."""

    def __init__(self, quotes: Sequence[Quote]) -> None:
        qs = sorted(quotes, key=lambda q: q.timestamp)
        self._quotes = qs
        self._ts = np.array([q.timestamp.timestamp() for q in qs], dtype=float)

    def __len__(self) -> int:
        return len(self._quotes)

    def at(self, ts: datetime) -> Quote | None:
        if not self._quotes:
            return None
        idx = int(np.searchsorted(self._ts, ts.timestamp(), side="right")) - 1
        if idx < 0:
            return None  # no quote yet: never look ahead
        return self._quotes[idx]

    def mid_at(self, ts: datetime) -> float:
        q = self.at(ts)
        return q.mid if q else float("nan")

    def mid_at_or_nan(self, ts: datetime) -> float:
        """Mid at ``ts`` only if the data reaches ``ts`` (never clamped to the last quote)."""
        if not self._quotes or ts.timestamp() > self._ts[-1]:
            return float("nan")
        return self.mid_at(ts)

    def mids_between(self, start: datetime, end: datetime) -> np.ndarray:
        lo = int(np.searchsorted(self._ts, start.timestamp(), side="left"))
        hi = int(np.searchsorted(self._ts, end.timestamp(), side="left"))  # [start, end)
        return np.array([q.mid for q in self._quotes[lo:hi] if not q.is_crossed], dtype=float)

    @property
    def last_timestamp(self) -> datetime | None:
        return self._quotes[-1].timestamp if self._quotes else None

    def sample_seconds(self) -> float:
        if len(self._ts) < 2:
            return 1.0
        gaps = np.diff(self._ts)
        gaps = gaps[gaps > 0]
        return float(np.median(gaps)) if gaps.size else 1.0


def _select_parent(orders: Sequence[Order], start: datetime, end: datetime) -> Order | None:
    parents = [o for o in orders if o.is_parent and start <= o.timestamp < end]
    if not parents:
        parents = [o for o in orders if o.is_parent]
    if not parents:
        return None
    return max(parents, key=lambda o: o.quantity)


def _nan() -> float:
    return float("nan")


class StandardExecutionAnalytics(ExecutionAnalytics):
    """Reference TCA implementation. Pure and deterministic."""

    def __init__(
        self,
        post_trade_horizon_seconds: int = 300,
        fees: FeeSchedule = DEFAULT_FEES,
        pretrade_model: Any = None,
    ) -> None:
        self.post_trade_horizon_seconds = post_trade_horizon_seconds
        self.fees = fees
        self._pretrade_model = pretrade_model

    @property
    def pretrade_model(self) -> Any:
        if self._pretrade_model is None:
            from ceap.ml.pretrade_cost import default_model

            self._pretrade_model = default_model()
        return self._pretrade_model

    def calculate(
        self,
        orders: Sequence[Order],
        executions: Sequence[Execution],
        quotes: Sequence[Quote],
        trades: Sequence[Trade],
        start: datetime,
        end: datetime,
    ) -> ExecutionMetrics:
        index = QuoteIndex(quotes)
        parent = _select_parent(orders, start, end)
        side = parent.side if parent else (executions[0].side if executions else Side.BUY)
        children = [
            o for o in orders if not o.is_parent and (parent is None or o.parent_order_id == parent.order_id)
        ]
        fills = sorted(
            [
                e
                for e in executions
                if start <= e.timestamp < end and (parent is None or e.parent_order_id == parent.order_id)
            ],
            key=lambda e: e.timestamp,
        )

        target_qty = parent.quantity if parent else sum(o.quantity for o in children)
        exec_prices = np.array([e.price for e in fills], dtype=float)
        exec_qty = np.array([e.quantity for e in fills], dtype=float)
        executed_qty = int(exec_qty.sum()) if exec_qty.size else 0

        arrival_ts = parent.timestamp if parent else start
        arrival_mid = index.mid_at(arrival_ts)
        decision_price = parent.decision_price if parent and parent.decision_price else arrival_mid
        arrival_price = arrival_mid if np.isfinite(arrival_mid) else decision_price
        exec_vwap = vwap(exec_prices, exec_qty)

        window_trades = [t for t in trades if start <= t.timestamp < end]
        mkt_vwap, market_volume = interval_vwap(window_trades)

        mids_window = index.mids_between(start, end)
        final_mid = index.mid_at(end)
        # a window shorter than the parent's horizon must not charge opportunity cost on the
        # whole parent: pro-rate the target to the fraction of the horizon covered
        opportunity_target = float(target_qty)
        if (
            parent is not None
            and parent.end_time is not None
            and end < parent.end_time
            and parent.end_time > parent.timestamp
        ):
            covered = (min(end, parent.end_time) - max(start, parent.timestamp)).total_seconds()
            horizon = (parent.end_time - parent.timestamp).total_seconds()
            opportunity_target = float(target_qty) * max(0.0, min(1.0, covered / horizon))

        fill_quotes = [index.at(e.timestamp) for e in fills]
        fill_mids = np.array([q.mid if q else np.nan for q in fill_quotes], dtype=float)
        far_touch = np.array(
            [(q.ask if side is Side.BUY else q.bid) if q else np.nan for q in fill_quotes], dtype=float
        )
        per_fill = per_fill_slippage_bps(side, exec_prices, fill_mids) if fills else np.array([])
        fee_ps = np.array([self.fees.per_share(e.liquidity_flag) for e in fills], dtype=float)
        comps = shortfall_components(
            side,
            float(decision_price),
            float(arrival_price),
            exec_prices,
            exec_qty,
            fill_mids,
            float(target_qty),
            final_price=final_mid,
            fees_per_share=fee_ps,
            opportunity_quantity=opportunity_target,
        )

        # markouts at fixed horizons: NaN where the data does not reach t + h (never clamped)
        markouts: dict[str, float] = {}
        laters: dict[int, np.ndarray] = {}
        for h in sorted({*MARKOUT_HORIZONS_SECONDS, self.post_trade_horizon_seconds}):
            laters[h] = np.array(
                [index.mid_at_or_nan(e.timestamp + timedelta(seconds=h)) for e in fills], dtype=float
            )
            if h in MARKOUT_HORIZONS_SECONDS:
                markouts[f"{h}s"] = markout_bps(side, exec_prices, laters[h], exec_qty) if fills else _nan()
        later_post = laters[self.post_trade_horizon_seconds]
        aggressive = np.array([(e.liquidity_flag or "REMOVE") == "REMOVE" for e in fills], dtype=bool)
        if aggressive.any():
            eff_spread = effective_spread_bps(
                exec_prices[aggressive], fill_mids[aggressive], side, exec_qty[aggressive]
            )
            real_spread = realised_spread_bps(
                side, exec_prices[aggressive], fill_mids[aggressive], later_post[aggressive], exec_qty[aggressive]
            )
        else:
            eff_spread = real_spread = _nan()
        improvement = price_improvement_bps(side, exec_prices, far_touch, exec_qty) if fills else _nan()
        # the pre-trade estimate describes the whole parent: compare only when the window covers it
        covers_parent = parent is not None and (
            start <= parent.timestamp and (parent.end_time is None or parent.end_time <= end)
        )
        order_level = self._pretrade(parent, index, arrival_ts)
        # the ex-ante impact estimate is an order-level number, reported for any window with a parent
        impact_estimate = order_level.get("impact_bps", _nan())
        pretrade = order_level if covers_parent else {}

        latencies = np.array([e.latency_us for e in fills if e.latency_us is not None], dtype=float)
        rejects = sum(1 for o in children if o.status is OrderStatus.REJECTED)

        window_quotes = [q for q in quotes if start <= q.timestamp < end]
        venue_stats = self._venue_statistics(side, children, fills, per_fill, market_volume)
        conditioned = self._conditioned_venue_statistics(fills, per_fill)
        arrival_is = comps.arrival_bps
        est = pretrade.get("estimate_bps", _nan())
        se = pretrade.get("scale_bps", _nan())
        resid = arrival_is - est if np.isfinite(arrival_is) and np.isfinite(est) else _nan()

        return ExecutionMetrics(
            symbol=parent.symbol if parent else (fills[0].symbol if fills else "?"),
            start=start,
            end=end,
            side=side,
            target_quantity=int(target_qty),
            executed_quantity=executed_qty,
            fill_rate=float(executed_qty / target_qty) if target_qty else _nan(),
            participation_rate=participation_rate(executed_qty, market_volume),
            vwap=exec_vwap,
            market_vwap=mkt_vwap,
            arrival_price=float(arrival_price),
            implementation_shortfall_bps=comps.total_bps,
            slippage_vs_arrival_bps=slippage_bps(side, exec_vwap, float(arrival_price)),
            slippage_vs_vwap_bps=slippage_bps(side, exec_vwap, mkt_vwap) if not np.isnan(mkt_vwap) else _nan(),
            average_spread_bps=average_spread_bps(window_quotes),
            effective_spread_bps=eff_spread,
            # expected square-root impact (pre-trade, units fixed in 2T.5); realised impact is in
            # the markouts. Temporary impact = the part of the effective spread that reverts within
            # the post-trade horizon: half the size-weighted realised spread.
            market_impact_bps=impact_estimate,
            temporary_impact_bps=real_spread / 2.0 if np.isfinite(real_spread) else _nan(),
            price_drift_bps=price_drift_bps(mids_window),
            realised_volatility_bps=realised_volatility_bps(mids_window, sample_seconds=index.sample_seconds()),
            average_latency_us=float(latencies.mean()) if latencies.size else _nan(),
            reject_rate=float(rejects / len(children)) if children else 0.0,
            execution_count=len(fills),
            child_order_count=len(children),
            venue_statistics=venue_stats,
            delay_cost_bps=comps.delay_bps,
            spread_cost_bps=comps.spread_bps,
            timing_cost_bps=comps.timing_bps,
            opportunity_cost_bps=comps.opportunity_bps,
            fees_bps=comps.fees_bps,
            arrival_shortfall_bps=arrival_is,
            pretrade_estimate_bps=est,
            pretrade_interval_bps=(pretrade.get("lower_bps", _nan()), pretrade.get("upper_bps", _nan())),
            is_standard_error_bps=se,
            is_price_risk_bps=pretrade.get("price_risk_bps", _nan()),
            is_residual_bps=resid,
            is_residual_z=resid / se if np.isfinite(resid) and np.isfinite(se) and se > 0 else _nan(),
            realised_spread_bps=real_spread,
            price_improvement_bps=improvement,
            markouts_bps=markouts,
            venue_statistics_conditioned=conditioned,
        )

    def _pretrade(self, parent: Order | None, index: QuoteIndex, arrival_ts: datetime) -> dict[str, float]:
        """Pre-trade estimate from arrival-time data only: the quoted spread at arrival, reference
        daily sigma, Q/ADV and the planned horizon (linear schedule: price risk sigma*sqrt(T/3))."""
        if parent is None or not parent.adv_shares or not parent.sigma_daily_bps:
            return {}
        q = index.at(arrival_ts)
        if q is None or q.is_crossed or q.mid <= 0:
            return {}
        from ceap.ml.pretrade_cost import PretradeFeatures

        horizon_s = (parent.end_time - parent.timestamp).total_seconds() if parent.end_time else 3600.0
        x = PretradeFeatures(
            spread_bps=(q.ask - q.bid) / q.mid * 10_000.0,
            sigma_daily_bps=float(parent.sigma_daily_bps),
            q_over_adv=parent.quantity / float(parent.adv_shares),
            algo=parent.strategy if parent.strategy in PRETRADE_ALGOS else "VWAP",
            schedule=((horizon_s / SESSION_SECONDS, 1.0, 0.0),),
        )
        out: dict[str, float] = self.pretrade_model.predict(x).as_dict()
        return out

    @staticmethod
    def _conditioned_venue_statistics(
        fills: Sequence[Execution], per_fill_slip: np.ndarray
    ) -> dict[str, dict[str, float]]:
        """Venue x order type (liquidity flag) x size bucket: fills, shares, size-weighted slippage."""
        cells: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for e, s in zip(fills, per_fill_slip, strict=False):
            key = f"{e.venue}|{e.liquidity_flag or 'UNKNOWN'}|{size_bucket(e.quantity)}"
            cells[key].append((float(s), float(e.quantity)))
        out: dict[str, dict[str, float]] = {}
        for key, vals in sorted(cells.items()):
            arr = np.array(vals)
            ok = np.isfinite(arr[:, 0])
            w = arr[ok, 1]
            out[key] = {
                "fills": float(len(vals)),
                "shares": float(arr[:, 1].sum()),
                "slippage_vs_mid_bps": float((arr[ok, 0] * w).sum() / w.sum()) if w.sum() > 0 else _nan(),
            }
        return out

    @staticmethod
    def _venue_statistics(
        side: Side,
        children: Sequence[Order],
        fills: Sequence[Execution],
        per_fill_slip: np.ndarray,
        market_volume: float,
    ) -> dict[str, VenueStatistics]:
        routed: dict[str, int] = defaultdict(
            int
        )  # accepted (non-rejected) quantity: rejects are a technology signal
        child_count: dict[str, int] = defaultdict(int)
        rejects: dict[str, int] = defaultdict(int)
        for o in children:
            v = o.venue or "UNKNOWN"
            child_count[v] += 1
            if o.status is OrderStatus.REJECTED:
                rejects[v] += 1
            else:
                routed[v] += o.quantity
        executed: dict[str, int] = defaultdict(int)
        exec_count: dict[str, int] = defaultdict(int)
        slip: dict[str, list[float]] = defaultdict(list)
        slip_w: dict[str, list[float]] = defaultdict(list)
        lat: dict[str, list[float]] = defaultdict(list)
        for e, s in zip(fills, per_fill_slip, strict=False):
            executed[e.venue] += e.quantity
            exec_count[e.venue] += 1
            if np.isfinite(s):
                slip[e.venue].append(float(s))
                slip_w[e.venue].append(float(e.quantity))
            if e.latency_us is not None:
                lat[e.venue].append(e.latency_us)
        venues = set(routed) | set(executed)
        total_exec = sum(executed.values())
        out: dict[str, VenueStatistics] = {}
        for v in sorted(venues):
            r, x = routed.get(v, 0), executed.get(v, 0)
            out[v] = VenueStatistics(
                venue=v,
                child_orders=child_count.get(v, 0),
                executions=exec_count.get(v, 0),
                routed_quantity=r,
                executed_quantity=x,
                fill_rate=float(x / r) if r else _nan(),
                # size-weighted fill-versus-mid slippage (v0.4 used an unweighted mean)
                average_slippage_bps=float(np.average(slip[v], weights=slip_w[v])) if slip[v] else _nan(),
                average_latency_us=float(np.mean(lat[v])) if lat[v] else _nan(),
                reject_count=rejects.get(v, 0),
                reject_rate=float(rejects.get(v, 0) / child_count[v]) if child_count.get(v) else 0.0,
                market_volume_participation=float(x / market_volume) if market_volume else _nan(),
                fill_share=float(x / total_exec) if total_exec else _nan(),
            )
        return out
