"""R4 item 4.9: property tests for analytics invariants (hypothesis).

Example counts come from the hypothesis profile registered in ``tests/conftest.py``: ``pr`` (100
examples, derandomised) by default, ``nightly`` (1000, derandomised) when ``HYPOTHESIS_PROFILE=nightly``.
Derandomised means the same examples on every run: a failure here is reproducible, never flaky.

The invariants are properties of the definitions, not of today's numbers, so they must keep holding
after the R2T TCA re-baseline.
"""

from __future__ import annotations

import math

import numpy as np
from hypothesis import assume, given
from hypothesis import strategies as st

from ceap.analytics.backtest import target_weights
from ceap.analytics.implementation_shortfall import execution_cost_bps, implementation_shortfall_bps
from ceap.analytics.market_impact import square_root_impact_estimate_bps
from ceap.analytics.vwap import vwap
from ceap.domain.execution import Side

price = st.floats(min_value=1.0, max_value=5_000.0, allow_nan=False, allow_infinity=False)
qty = st.floats(min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False)
side = st.sampled_from([Side.BUY, Side.SELL])
fills = st.lists(st.tuples(price, qty), min_size=1, max_size=40)


@given(fills)
def test_vwap_lies_within_low_high(pq):
    prices, quantities = zip(*pq, strict=True)
    v = vwap(prices, quantities)
    assert min(prices) - 1e-9 * max(prices) <= v <= max(prices) + 1e-9 * max(prices)


@given(side, price, fills, st.floats(min_value=0.0, max_value=2.0), price)
def test_is_components_sum_to_total(s, decision, pq, extra, final):
    """IS = execution leg + opportunity leg, each computed independently, in bps of the target notional."""
    prices, quantities = zip(*pq, strict=True)
    filled = sum(quantities)
    target = filled * (1.0 + extra)
    total = implementation_shortfall_bps(s, decision, prices, quantities, target, final_price=final)
    execution_only = implementation_shortfall_bps(s, decision, prices, quantities, target)
    opportunity = s.sign * (final - decision) * max(target - filled, 0.0) / (decision * target) * 1e4
    assert math.isclose(total, execution_only + opportunity, rel_tol=1e-9, abs_tol=1e-6)


@given(side, price, fills)
def test_fully_filled_is_equals_execution_cost_at_vwap(s, decision, pq):
    prices, quantities = zip(*pq, strict=True)
    filled = sum(quantities)
    is_bps = implementation_shortfall_bps(s, decision, prices, quantities, filled, final_price=decision * 2)
    cost = execution_cost_bps(s, decision, vwap(prices, quantities))
    assert math.isclose(is_bps, cost, rel_tol=1e-9, abs_tol=1e-6)


@given(side, price, qty, st.floats(min_value=0.0, max_value=0.05))
def test_cost_is_non_negative_when_every_fill_is_adverse(s, decision, q, adverse):
    """Paying up (buy above / sell below the decision price) never shows as a negative cost."""
    fill = decision * (1.0 + s.sign * adverse)
    assert implementation_shortfall_bps(s, decision, [fill], [q], q) >= -1e-9
    assert execution_cost_bps(s, decision, fill) >= -1e-9


@given(
    st.floats(min_value=0.0, max_value=1e6),
    st.floats(min_value=0.0, max_value=1e6),
    st.floats(min_value=1e3, max_value=1e8),
    st.floats(min_value=1.0, max_value=500.0),
)
def test_impact_is_non_negative_and_monotone_in_participation(q1, q2, adv, vol):
    # 0.5.0 (2T.5): impact = c * sigma_daily * sqrt(Q / ADV); monotone in Q at fixed ADV.
    lo, hi = sorted((q1, q2))
    a = square_root_impact_estimate_bps(lo, adv, vol)
    b = square_root_impact_estimate_bps(hi, adv, vol)
    assert 0.0 <= a <= b + 1e-12


@given(
    st.lists(st.floats(min_value=-5, max_value=5, allow_nan=False), min_size=2, max_size=80),
    st.booleans(),
    st.sampled_from(["equal", "rank"]),
    st.floats(min_value=0.05, max_value=0.5),
)
def test_portfolio_weights_sum(z, long_short, weighting, top_fraction):
    w = target_weights(np.asarray(z), long_short, top_fraction, weighting)
    assume(np.count_nonzero(w) > 0)
    assert math.isclose(float(np.abs(w).sum()), 1.0, rel_tol=1e-9)
    if long_short:
        assert math.isclose(float(w.sum()), 0.0, abs_tol=1e-9)  # dollar neutral
    else:
        assert (w >= 0).all()
