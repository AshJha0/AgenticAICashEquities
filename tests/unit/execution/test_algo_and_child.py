"""Child-order logic (F4) and the end-to-end parent runner, with a price-scale metamorphic test."""

from __future__ import annotations

import pytest

from ceap.execution.algo import ParentOrder, execute
from ceap.execution.child import ChildPolicy, decide
from ceap.execution.lob import BUY, SELL, BookView, LOBSimulator, RestingOrder, SimulatorParams
from ceap.execution.schedule import POV, TWAP, VWAP, AlmgrenChriss

VIEW = BookView(0, 100, 102, 500, 500)


def test_on_schedule_posts_passively_at_the_near_touch() -> None:
    acts = decide(
        ChildPolicy(urgency=1.0),
        BUY,
        gap=300,
        slice_qty=300,
        remaining_parent=3000,
        view=VIEW,
        resting=[],
        final=False,
    )
    assert [(a.kind, a.quantity, a.limit) for a in acts] == [("PASSIVE", 300, 100)]


def test_behind_schedule_takes_part_at_the_far_touch() -> None:
    acts = decide(
        ChildPolicy(urgency=1.0),
        SELL,
        gap=600,
        slice_qty=300,
        remaining_parent=3000,
        view=VIEW,
        resting=[],
        final=False,
    )
    assert ("AGGRESSIVE", 300, 100) in [(a.kind, a.quantity, a.limit) for a in acts]
    assert ("PASSIVE", 300, 102) in [(a.kind, a.quantity, a.limit) for a in acts]


def test_tight_spread_or_final_slice_crosses() -> None:
    tight = BookView(0, 100, 101, 500, 500)
    acts = decide(ChildPolicy(), BUY, 300, 300, 3000, tight, [], final=False)
    assert [a.kind for a in acts] == ["AGGRESSIVE"]
    acts = decide(ChildPolicy(), BUY, 300, 300, 300, VIEW, [], final=True)
    assert [(a.kind, a.limit) for a in acts] == [("AGGRESSIVE", 102)]


def test_iwould_takes_the_whole_remaining_parent() -> None:
    acts = decide(ChildPolicy(iwould_price=103), BUY, 10, 300, 2_500, VIEW, [], final=False)
    assert [(a.kind, a.quantity, a.limit) for a in acts] == [("AGGRESSIVE", 2_500, 103)]


def test_cancel_replace_when_the_touch_moves_and_min_qty() -> None:
    stale = RestingOrder(7, "agent", BUY, 99, 200)
    acts = decide(
        ChildPolicy(min_qty=100),
        BUY,
        gap=0,
        slice_qty=300,
        remaining_parent=3000,
        view=VIEW,
        resting=[stale],
        final=False,
    )
    assert acts[0].kind == "CANCEL" and acts[0].order_id == 7
    assert [(a.kind, a.quantity, a.limit) for a in acts[1:]] == [("PASSIVE", 200, 100)]
    small = decide(ChildPolicy(min_qty=100), BUY, 40, 300, 3000, VIEW, [], final=False)
    assert small == []
    finishing = decide(ChildPolicy(min_qty=100), BUY, 40, 300, 40, VIEW, [], final=False)
    assert finishing and finishing[0].quantity == 40


def test_randomisation_never_exceeds_the_gap() -> None:
    import numpy as np

    rng = np.random.default_rng(0)
    for _ in range(200):
        acts = decide(ChildPolicy(randomise=0.5, min_qty=1), BUY, 300, 300, 3000, VIEW, [], False, rng)
        assert sum(a.quantity for a in acts) <= 300


@pytest.mark.parametrize(
    "schedule",
    [
        TWAP(),
        VWAP(0.4, 0.5),
        POV(0.10, 0.03, total=2_000),
        AlmgrenChriss(sigma=0.3, eta=1e-4, gamma=1e-6, horizon=1.0, risk_aversion=1e-3),
    ],
    ids=lambda s: s.name,
)
def test_parent_runs_to_completion_and_scores_consistently(schedule) -> None:
    r = execute(LOBSimulator(seed=7), ParentOrder(BUY, 2_000, 30_000, 330_000, 6), schedule, ChildPolicy())
    assert r.executed <= 2_000 and sum(r.executed_by_slice) == r.executed
    assert r.planned[-1] == 2_000
    c = r.components.as_dict()
    assert c["total_bps"] == pytest.approx(
        c["delay_bps"] + c["spread_bps"] + c["timing_bps"] + c["opportunity_bps"] + c["fees_bps"]
    )
    assert c["delay_bps"] == 0.0  # decision = arrival in the simulator
    assert all(f.owner == "agent" for f in r.fills)


def test_bps_costs_are_invariant_to_price_scale() -> None:
    """Metamorphic: scale price, tick and per-share fees by 10 and every bps number is unchanged.

    (Per-share fees are dollars, so left unscaled they rightly cost fewer bps on a dearer stock.)
    """
    from ceap.analytics.implementation_shortfall import DEFAULT_FEES, FeeSchedule

    out = []
    for scale in (1.0, 10.0):
        p = SimulatorParams(start_price=100.0 * scale, tick=0.01 * scale)
        fees = FeeSchedule(*(getattr(DEFAULT_FEES, f) * scale for f in DEFAULT_FEES.__dataclass_fields__))
        r = execute(
            LOBSimulator(p, seed=11),
            ParentOrder(SELL, 1_500, 10_000, 190_000, 6),
            TWAP(),
            ChildPolicy(),
            fees=fees,
        )
        out.append(r.components.as_dict())
    for k in out[0]:
        assert out[1][k] == pytest.approx(out[0][k], rel=1e-9, abs=1e-9)


def test_runner_refuses_a_parent_in_the_past() -> None:
    sim = LOBSimulator(seed=0)
    sim.run_until(50_000)
    with pytest.raises(ValueError):
        execute(sim, ParentOrder(BUY, 100, 10_000, 20_000, 2), TWAP())


@pytest.mark.parametrize("schedule", [TWAP(), VWAP(0.4, 0.5)], ids=lambda s: s.name)
def test_parent_is_completed_over_40_seeds(schedule) -> None:
    """0.10.0 X5 (finding X-08): the completion step finishes the parent in every seed."""
    short = []
    for seed in range(40):
        r = execute(LOBSimulator(seed=seed), ParentOrder(BUY, 2_000, 30_000, 330_000, 6), schedule, ChildPolicy())
        if r.executed != 2_000:
            short.append((seed, r.executed))
    assert not short
