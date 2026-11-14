"""Run one parent order through the simulator: schedule -> child orders -> fills -> cost (F1-F4).

The cost is measured with the same Perold decomposition the post-trade TCA uses
(:func:`ceap.analytics.implementation_shortfall.shortfall_components` and its fee schedule), so a
simulated execution and a real one are scored by one piece of code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ceap.analytics.implementation_shortfall import (
    DEFAULT_FEES,
    FeeSchedule,
    ShortfallComponents,
    shortfall_components,
)
from ceap.domain.execution import Side
from ceap.execution.child import ChildPolicy, decide
from ceap.execution.lob import AGENT, Fill, LOBSimulator
from ceap.execution.schedule import Schedule, allocate


@dataclass(frozen=True)
class ParentOrder:
    side: int
    quantity: int
    start_ms: int
    end_ms: int
    n_slices: int = 10

    def __post_init__(self) -> None:
        if (
            self.quantity <= 0
            or self.end_ms <= self.start_ms
            or self.n_slices < 1
            or self.side not in (1, -1)
        ):
            raise ValueError("invalid parent order")


@dataclass
class ExecutionResult:
    parent: ParentOrder
    schedule: str
    fills: list[Fill]
    arrival_price: float
    final_price: float
    fill_mids: list[float]
    planned: list[int]
    executed_by_slice: list[int]
    market_volume: int
    components: ShortfallComponents
    child_orders: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def executed(self) -> int:
        return sum(f.quantity for f in self.fills)

    @property
    def fill_rate(self) -> float:
        return self.executed / self.parent.quantity

    @property
    def passive_share(self) -> float:
        q = self.executed
        return sum(f.quantity for f in self.fills if f.liquidity == "ADD") / q if q else float("nan")

    @property
    def participation(self) -> float:
        return self.executed / self.market_volume if self.market_volume else float("nan")


def _mid_at(sim: LOBSimulator, ts_ms: int) -> float:
    import bisect

    t = [x for x, _ in sim.mid_history]
    i = bisect.bisect_left(t, ts_ms) - 1  # the mid prevailing just before the fill
    return sim.mid_history[max(i, 0)][1]


def execute(
    sim: LOBSimulator,
    parent: ParentOrder,
    schedule: Schedule,
    policy: ChildPolicy | None = None,
    seed: int = 0,
    fees: FeeSchedule = DEFAULT_FEES,
) -> ExecutionResult:
    """Trade ``parent`` in ``sim`` and score it. ``sim`` must not have passed ``parent.start_ms``."""
    if sim.now_ms > parent.start_ms:
        raise ValueError("the simulator is already past the parent's start")
    policy = policy or ChildPolicy()
    rng = np.random.default_rng(seed)
    sim.run_until(parent.start_ms)
    arrival_ticks = sim.mid_ticks()
    slice_qty = allocate(parent.quantity, schedule.weights(parent.n_slices))
    planned = np.cumsum(slice_qty)
    slice_ms = (parent.end_ms - parent.start_ms) / parent.n_slices
    fills: list[Fill] = []
    sent = 0
    for k in range(parent.n_slices):
        t_k = parent.start_ms + int(round(k * slice_ms))
        fills.extend(sim.run_until(t_k))
        executed = sum(f.quantity for f in fills)
        mkt = sim.market_volume(parent.start_ms, t_k)
        target = min(parent.quantity, schedule.adjust(k, int(planned[k]), executed, mkt))
        resting = sim.open_agent_orders()
        working = sum(o.visible for o in resting)
        final = False  # the last slice is worked like the others; completion happens below (X-08)
        actions = decide(
            policy,
            parent.side,
            gap=target - executed - working,
            slice_qty=int(slice_qty[k]),
            remaining_parent=parent.quantity - executed,
            view=sim.view(),
            resting=resting,
            final=final,
            rng=rng,
        )
        for a in actions:
            if a.kind == "CANCEL" and a.order_id is not None:
                sim.cancel(a.order_id)
            elif a.kind in ("AGGRESSIVE", "PASSIVE"):
                sim.submit(parent.side, a.quantity, a.limit, ioc=a.kind == "AGGRESSIVE")
                sent += 1
    # Completion (0.10.0 X5, finding X-08): shortly before the end, cancel what rests and take the
    # remainder with a market order, so a schedule finishes its parent instead of leaving passive
    # orders to expire. The cost of that sweep lands in the score like any other fill.
    t_c = max(parent.end_ms - 4 * sim.p.entry_latency_ms, parent.start_ms)
    fills.extend(sim.run_until(t_c))
    for o in sim.open_agent_orders():
        sim.cancel(o.order_id)
    remaining = parent.quantity - sum(f.quantity for f in fills if f.owner == AGENT)
    if remaining > 0:
        sim.submit(parent.side, remaining, None, ioc=True)  # market order: completion over price
        sent += 1
    fills.extend(sim.run_until(parent.end_ms))
    for o in sim.open_agent_orders():
        sim.cancel(o.order_id)
    fills.extend(sim.run_until(parent.end_ms + sim.p.entry_latency_ms))
    fills = [f for f in fills if f.owner == AGENT]
    executed_by_slice = [0] * parent.n_slices
    for f in fills:  # a fill belongs to the slice in which it happened; late ones to the last
        k = min(int((f.ts_ms - parent.start_ms) // slice_ms), parent.n_slices - 1)
        executed_by_slice[max(k, 0)] += f.quantity
    final_ticks = sim.mid_ticks()
    tick = sim.p.tick
    mids = [_mid_at(sim, f.ts_ms) * tick for f in fills]
    comps = shortfall_components(
        Side.BUY if parent.side > 0 else Side.SELL,
        decision_price=arrival_ticks * tick,
        arrival_price=arrival_ticks * tick,
        exec_prices=[f.price * tick for f in fills],
        exec_quantities=[f.quantity for f in fills],
        fill_mids=mids,
        target_quantity=parent.quantity,
        final_price=final_ticks * tick,
        fees_per_share=[fees.per_share(f.liquidity) for f in fills],
    )
    return ExecutionResult(
        parent=parent,
        schedule=schedule.name,
        fills=fills,
        arrival_price=arrival_ticks * tick,
        final_price=final_ticks * tick,
        fill_mids=mids,
        planned=[int(x) for x in planned],
        executed_by_slice=executed_by_slice,
        market_volume=sim.market_volume(parent.start_ms, parent.end_ms),
        components=comps,
        child_orders=sent,
    )
