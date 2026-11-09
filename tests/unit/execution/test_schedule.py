"""Schedulers (F3): known answers for Almgren-Chriss and properties every schedule must keep."""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ceap.execution.schedule import POV, TWAP, VWAP, AlmgrenChriss, Schedule, allocate, cumulative_targets

AC = AlmgrenChriss(sigma=0.95, eta=2.5e-6, gamma=2.5e-7, epsilon=0.0625, horizon=5.0, risk_aversion=1e-6)


# ------------------------------------------------------------------ properties
@settings(max_examples=200, deadline=None)
@given(
    total=st.integers(0, 5_000_000),
    n=st.integers(1, 80),
    which=st.sampled_from(["twap", "vwap", "pov", "ac"]),
)
def test_every_schedule_sums_exactly_to_the_parent(total: int, n: int, which: str) -> None:
    sched: Schedule = {"twap": TWAP(), "vwap": VWAP(0.2, 0.7), "pov": POV(), "ac": AC}[which]
    w = sched.weights(n)
    assert w.shape == (n,) and np.all(w >= -1e-15) and math.isclose(w.sum(), 1.0, rel_tol=1e-12)
    q = allocate(total, w)
    assert q.sum() == total and np.all(q >= 0)
    assert cumulative_targets(sched, total, n)[-1] == total
    # largest remainder: no slice is more than one share from its exact pro-rata size
    assert np.all(np.abs(q - total * w) < 1.0 + 1e-9)


@given(
    st.lists(st.floats(0.0, 10.0), min_size=1, max_size=30).filter(lambda w: sum(w) > 0),
    st.integers(0, 10**6),
)
def test_allocate_is_deterministic_and_exact(w: list[float], total: int) -> None:
    a, b = allocate(total, w), allocate(total, w)
    assert np.array_equal(a, b) and a.sum() == total


def test_allocate_rejects_bad_weights() -> None:
    for bad in ([], [0.0, 0.0], [1.0, -1.0], [float("nan")]):
        with pytest.raises(ValueError):
            allocate(10, bad)


# ---------------------------------------------------------------- known answers
def test_almgren_chriss_matches_the_closed_form() -> None:
    """Their section 3 example parameters (X = 1e6 shares, T = 5 days, N = 5)."""
    n, x0 = 5, 1_000_000.0
    tau = AC.horizon / n
    eta_t = AC.eta - AC.gamma * tau / 2
    kappa = math.acosh(1 + 0.5 * tau**2 * AC.risk_aversion * AC.sigma**2 / eta_t) / tau
    expected = [
        x0 * math.sinh(kappa * (AC.horizon - j * tau)) / math.sinh(kappa * AC.horizon) for j in range(n + 1)
    ]
    assert AC.holdings(x0, n) == pytest.approx(expected, rel=1e-12)
    assert AC.kappa(n) == pytest.approx(kappa, rel=1e-12)
    # front-loaded: each slice smaller than the one before
    assert np.all(np.diff(AC.weights(n)) < 0)


def test_almgren_chriss_cost_and_variance_formulas() -> None:
    n, x0 = 5, 1_000_000.0
    tau = AC.horizon / n
    nk = -np.diff(AC.holdings(x0, n))
    xk = AC.holdings(x0, n)[1:]
    e = 0.5 * AC.gamma * x0**2 + AC.epsilon * x0 + AC.eta_tilde(n) / tau * float((nk**2).sum())
    v = AC.sigma**2 * tau * float((xk**2).sum())
    assert AC.expected_cost(x0, n) == pytest.approx(e, rel=1e-12)
    assert AC.variance(x0, n) == pytest.approx(v, rel=1e-12)


def test_zero_risk_aversion_is_twap_with_the_textbook_cost() -> None:
    from dataclasses import replace

    ac0 = replace(AC, risk_aversion=0.0)
    n, x0 = 10, 1_000_000.0
    assert ac0.weights(n) == pytest.approx(TWAP().weights(n), abs=1e-15)
    # E = gamma X^2 / 2 + epsilon X + eta_tilde X^2 / T for the straight line
    assert ac0.expected_cost(x0, n) == pytest.approx(
        0.5 * AC.gamma * x0**2 + AC.epsilon * x0 + ac0.eta_tilde(n) * x0**2 / AC.horizon, rel=1e-12
    )


def test_frontier_trades_cost_for_risk() -> None:
    pts = AC.frontier(1_000_000, 20, [0.0, 1e-7, 1e-6, 1e-5])
    costs = [e for _, e, _ in pts]
    risks = [s for _, _, s in pts]
    assert costs == sorted(costs) and risks == sorted(risks, reverse=True)


def test_vwap_u_profile_is_symmetric_and_heavier_at_the_edges() -> None:
    w = VWAP().weights(13)
    assert w == pytest.approx(w[::-1], rel=1e-12)
    assert w[0] > w[6] and w[-1] > w[6]


def test_pov_keeps_inside_the_band_and_caps_at_the_parent() -> None:
    pov = POV(rate=0.10, band=0.02, total=5_000)
    assert pov.adjust(0, 999, 0, 20_000) == 2_000
    assert pov.adjust(0, 999, 0, 10**9) == 5_000
    lo, hi = pov.bounds(20_000)
    assert lo == pytest.approx(1_600) and hi == pytest.approx(2_400)
    with pytest.raises(ValueError):
        POV(rate=0.1, band=0.2)


def test_static_schedules_keep_the_plan() -> None:
    for s in (TWAP(), VWAP(), AC):
        assert s.adjust(3, 1234, 10, 1e9) == 1234
