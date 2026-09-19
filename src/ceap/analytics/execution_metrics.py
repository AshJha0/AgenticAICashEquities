"""Standard execution-quality analytics (TCA).

Given orders, executions and market data for a window this produces the
full :class:`ExecutionMetrics` bundle plus per-venue statistics.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta

import numpy as np

from ceap.analytics.implementation_shortfall import implementation_shortfall_bps
from ceap.analytics.liquidity import average_spread_bps, participation_rate
from ceap.analytics.market_impact import market_impact_bps, temporary_impact_bps
from ceap.analytics.slippage import effective_spread_bps, per_fill_slippage_bps, slippage_bps
from ceap.analytics.volatility import price_drift_bps, realised_volatility_bps
from ceap.analytics.vwap import vwap
from ceap.domain.analytics import ExecutionAnalytics
from ceap.domain.execution import Execution, ExecutionMetrics, Order, OrderStatus, Side, VenueStatistics
from ceap.domain.market import Quote, Trade


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


class StandardExecutionAnalytics(ExecutionAnalytics):
    """Reference TCA implementation. Pure and deterministic."""

    def __init__(self, post_trade_horizon_seconds: int = 300) -> None:
        self.post_trade_horizon_seconds = post_trade_horizon_seconds

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
        arrival_price = (
            parent.decision_price if parent and parent.decision_price else index.mid_at(arrival_ts)
        )
        exec_vwap = vwap(exec_prices, exec_qty)

        window_trades = [t for t in trades if start <= t.timestamp < end]
        mkt_vwap = vwap([t.price for t in window_trades], [t.quantity for t in window_trades])
        market_volume = float(sum(t.quantity for t in window_trades))

        mids_window = index.mids_between(start, end)
        last_fill_ts = fills[-1].timestamp if fills else end
        # post-trade reference: the mid ``post_trade_horizon_seconds`` after the last fill, bounded
        # by the quotes available, so temporary impact measures reversion rather than the fill itself
        post_ts = last_fill_ts + timedelta(seconds=self.post_trade_horizon_seconds)
        if index.last_timestamp is not None and post_ts > index.last_timestamp:
            post_ts = index.last_timestamp
        post_mid = index.mid_at(post_ts)
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

        fill_mids = np.array([index.mid_at(e.timestamp) for e in fills], dtype=float)
        per_fill = per_fill_slippage_bps(side, exec_prices, fill_mids) if fills else np.array([])

        latencies = np.array([e.latency_us for e in fills if e.latency_us is not None], dtype=float)
        rejects = sum(1 for o in children if o.status is OrderStatus.REJECTED)

        window_quotes = [q for q in quotes if start <= q.timestamp < end]
        venue_stats = self._venue_statistics(side, children, fills, per_fill, market_volume)

        return ExecutionMetrics(
            symbol=parent.symbol if parent else (fills[0].symbol if fills else "?"),
            start=start,
            end=end,
            side=side,
            target_quantity=int(target_qty),
            executed_quantity=executed_qty,
            fill_rate=float(executed_qty / target_qty) if target_qty else float("nan"),
            participation_rate=participation_rate(executed_qty, market_volume),
            vwap=exec_vwap,
            market_vwap=mkt_vwap,
            arrival_price=float(arrival_price),
            implementation_shortfall_bps=implementation_shortfall_bps(
                side, float(arrival_price), exec_prices, exec_qty, opportunity_target, final_price=final_mid
            ),
            slippage_vs_arrival_bps=slippage_bps(side, exec_vwap, float(arrival_price)),
            slippage_vs_vwap_bps=slippage_bps(side, exec_vwap, mkt_vwap)
            if not np.isnan(mkt_vwap)
            else float("nan"),
            average_spread_bps=average_spread_bps(window_quotes),
            effective_spread_bps=effective_spread_bps(exec_prices, fill_mids, side)
            if fills
            else float("nan"),
            market_impact_bps=market_impact_bps(side, float(arrival_price), post_mid),
            temporary_impact_bps=temporary_impact_bps(side, exec_vwap, post_mid),
            price_drift_bps=price_drift_bps(mids_window),
            realised_volatility_bps=realised_volatility_bps(
                mids_window, sample_seconds=index.sample_seconds()
            ),
            average_latency_us=float(latencies.mean()) if latencies.size else float("nan"),
            reject_rate=float(rejects / len(children)) if children else 0.0,
            execution_count=len(fills),
            child_order_count=len(children),
            venue_statistics=venue_stats,
        )

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
        lat: dict[str, list[float]] = defaultdict(list)
        for e, s in zip(fills, per_fill_slip, strict=False):
            executed[e.venue] += e.quantity
            exec_count[e.venue] += 1
            if np.isfinite(s):
                slip[e.venue].append(float(s))
            if e.latency_us is not None:
                lat[e.venue].append(e.latency_us)
        venues = set(routed) | set(executed)
        out: dict[str, VenueStatistics] = {}
        for v in sorted(venues):
            r, x = routed.get(v, 0), executed.get(v, 0)
            out[v] = VenueStatistics(
                venue=v,
                child_orders=child_count.get(v, 0),
                executions=exec_count.get(v, 0),
                routed_quantity=r,
                executed_quantity=x,
                fill_rate=float(x / r) if r else float("nan"),
                average_slippage_bps=float(np.mean(slip[v])) if slip[v] else float("nan"),
                average_latency_us=float(np.mean(lat[v])) if lat[v] else float("nan"),
                reject_count=rejects.get(v, 0),
                reject_rate=float(rejects.get(v, 0) / child_count[v]) if child_count.get(v) else 0.0,
                volume_share=float(x / market_volume) if market_volume else float("nan"),
            )
        return out
