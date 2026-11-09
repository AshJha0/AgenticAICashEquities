"""Endogenous impact (F2): the propagator state, and recovery of the kernel against the truth."""

from __future__ import annotations

import numpy as np
import pytest

from ceap.execution.impact import (
    PropagatorKernel,
    PropagatorState,
    fit_markout_curve,
    fit_propagator,
    response_curve,
)
from ceap.execution.lob import BUY, SELL, LOBSimulator, SimulatorParams

TRUTH = PropagatorKernel(permanent_bps=0.4, temporary_bps=1.2, decay_s=30.0)


def _series(seed: int, n: int = 20_000) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    v = np.where(rng.random(n) < 0.2, rng.choice([-1, 1], n) * rng.integers(100, 3000, n), 0).astype(float)
    st = PropagatorState(TRUTH)
    mid = np.empty(n)
    noise = 0.0
    for i in range(n):
        st.trade(float(i), v[i])
        noise += rng.normal(0.0, 1.0)
        mid[i] = 100.0 * (1.0 + (st.impact_bps(float(i)) + noise) / 10_000.0)
    return mid, v


def test_state_matches_the_kernel_response() -> None:
    st = PropagatorState(TRUTH)
    st.trade(0.0, 2_000)
    assert st.impact_bps(45.0) == pytest.approx(2 * float(TRUTH.response(45.0)), rel=1e-12)
    st.trade(45.0, -1_000)
    assert st.impact_bps(45.0) == pytest.approx(
        2 * float(TRUTH.response(45.0)) - float(TRUTH.response(0.0)), rel=1e-12
    )


@pytest.mark.parametrize("seed", [1, 3])
def test_fit_recovers_the_truth_within_three_standard_errors(seed: int) -> None:
    mid, v = _series(seed)
    cmp = fit_propagator(mid, v, 1.0).compare(TRUTH)
    assert abs(cmp["permanent_bps"]["z"]) < 3 and abs(cmp["temporary_bps"]["z"]) < 3
    assert 15.0 < cmp["decay_s"]["estimate"] < 60.0


def test_markout_curve_fit_on_an_exact_curve() -> None:
    curve = {h: float(TRUTH.response(h)) for h in (0, 1, 2, 5, 10, 20, 40, 80, 160, 320)}
    fit = fit_markout_curve(curve)
    assert fit.permanent_bps == pytest.approx(0.4, abs=0.02)
    assert fit.temporary_bps == pytest.approx(1.2, abs=0.02)
    assert fit.decay_s == pytest.approx(30.0, rel=0.1)


def test_plain_markouts_decay_towards_the_permanent_level() -> None:
    mid, v = _series(1)
    curve = response_curve(mid, v, [0, 30, 300])
    assert curve[0] > curve[30] > curve[300] > 0


def test_bot_flow_moves_the_simulated_price() -> None:
    """Endogenous: buying pushes the efficient price up by the kernel, and the book follows."""
    sim = LOBSimulator(
        SimulatorParams(sigma_bps_per_sqrt_s=0.0, kernel=PropagatorKernel(2.0, 6.0, 30.0)), seed=2
    )
    sim.run_until(1_000)
    e0, m0 = sim.efficient_ticks, sim.mid_ticks()
    for t in range(1_000, 31_000, 1_000):
        sim.run_until(t)
        sim.submit(BUY, 200)
    sim.run_until(40_000)
    assert sim.efficient_ticks > e0 and sim.mid_ticks() > m0
    assert sim.impact.cum_units > 0
    assert not any(f.side == SELL for f in sim.agent_fills)


@pytest.mark.nightly
def test_lob_kernel_recovered_on_the_efficient_path() -> None:
    """Two simulated hours (about a minute of CPU): the deconvolved fit on the efficient price."""
    kern = PropagatorKernel(2.0, 6.0, 30.0)
    rng = np.random.default_rng(0)
    sim = LOBSimulator(SimulatorParams(kernel=kern, stale_ticks=1.0), seed=3)
    t_end = 7_200_000
    for s in range(0, t_end, 1000):
        sim.run_until(s)
        if rng.random() < 0.5:
            v = sim.view()
            side = int(rng.choice([1, -1]))
            top = v.ask_size if side == 1 else v.bid_size
            sim.submit(
                side,
                int(max(1, min(top // 2, rng.integers(100, 400)))),
                limit=v.ask if side == 1 else v.bid,
                ioc=True,
            )
    sim.run_until(t_end)
    flow = sim.agent_flow_grid(1000, 0, t_end)
    cmp = fit_propagator(sim.mid_grid(1000, 0, t_end, efficient=True), flow, 1.0).compare(kern)
    assert abs(cmp["permanent_bps"]["z"]) < 3 and abs(cmp["temporary_bps"]["z"]) < 3
