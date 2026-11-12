"""H3 (0.9.0): close the two surviving-mutant gaps and widen the input ranges.

Plain idea: a mutation test changes one operator in the code (``>`` to ``>=``, ``and`` to ``or``)
and checks that some test fails. Two mutants survived in 0.8.0 because no test fed the boundary:

* a non-positive fill mid (``m > 0`` mutated to ``m >= 0`` or dropped) in the Perold split;
* a NaN ``final_price`` in ``implementation_shortfall_bps``, which priced the unfilled leg as NaN.

Formal statement: for every input in the widened domain (prices 1e-2 to 1e5, quantities 1e-3 to 1e9),
the Perold legs sum to the total, a mid ``<= 0`` is treated exactly like a missing mid, and a
non-finite ``final_price`` is treated exactly like ``final_price=None``.
"""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from ceap.analytics.implementation_shortfall import implementation_shortfall_bps, shortfall_components
from ceap.domain.execution import Side

wide_price = st.floats(min_value=1e-2, max_value=1e5, allow_nan=False, allow_infinity=False)
wide_qty = st.floats(min_value=1e-3, max_value=1e9, allow_nan=False, allow_infinity=False)
side = st.sampled_from([Side.BUY, Side.SELL])
fills = st.lists(st.tuples(wide_price, wide_qty), min_size=1, max_size=30)
bad_mid = st.sampled_from([0.0, -1.0, -1e-9, float("nan"), float("inf"), float("-inf")])
not_finite = st.sampled_from([float("nan"), float("inf"), float("-inf")])


def _legs(c) -> float:  # type: ignore[no-untyped-def]
    return c.delay_bps + c.spread_bps + c.timing_bps + c.opportunity_bps + c.fees_bps


@given(side, wide_price, wide_price, fills, st.floats(min_value=0.0, max_value=3.0), wide_price)
def test_perold_legs_are_finite_and_sum_on_the_wide_domain(s, decision, arrival, pq, extra, final):
    prices, qtys = zip(*pq, strict=True)
    target = sum(qtys) * (1.0 + extra)
    c = shortfall_components(s, decision, arrival, prices, qtys, prices, target, final_price=final)
    assert math.isfinite(_legs(c))
    assert math.isclose(_legs(c), c.total_bps, rel_tol=1e-9, abs_tol=1e-6)


@given(side, wide_price, wide_price, fills, bad_mid)
def test_non_positive_or_missing_mid_equals_arrival_mid(s, decision, arrival, pq, mid):
    """A mid <= 0 (or non-finite) is replaced by the arrival price, identical to passing the arrival."""
    prices, qtys = zip(*pq, strict=True)
    target = sum(qtys)
    bad = shortfall_components(s, decision, arrival, prices, qtys, [mid] * len(prices), target)
    ref = shortfall_components(s, decision, arrival, prices, qtys, [arrival] * len(prices), target)
    for a, b in ((bad.spread_bps, ref.spread_bps), (bad.timing_bps, ref.timing_bps)):
        assert math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-9)
    assert math.isfinite(bad.spread_bps)


def test_zero_mid_example():
    """Worked example: buy 100 at 10.10, arrival 10.00, fill mid reported as 0. The mid is unusable,
    so the whole 10 bps execution cost is spread and timing is 0."""
    c = shortfall_components(Side.BUY, 10.0, 10.0, [10.10], [100.0], [0.0], 100.0)
    assert math.isclose(c.spread_bps, 100.0, rel_tol=1e-12)
    assert c.timing_bps == 0.0


@given(side, wide_price, fills, st.floats(min_value=0.01, max_value=3.0), not_finite)
def test_non_finite_final_price_is_treated_as_missing(s, decision, pq, extra, final):
    prices, qtys = zip(*pq, strict=True)
    target = sum(qtys) * (1.0 + extra)
    got = implementation_shortfall_bps(s, decision, prices, qtys, target, final_price=final)
    assert math.isfinite(got)
    assert got == implementation_shortfall_bps(s, decision, prices, qtys, target)


@given(side, wide_price, wide_price, fills, st.floats(min_value=0.01, max_value=3.0), not_finite)
def test_components_flag_unpriced_opportunity_on_non_finite_final(s, decision, arrival, pq, extra, final):
    prices, qtys = zip(*pq, strict=True)
    target = sum(qtys) * (1.0 + extra)
    c = shortfall_components(s, decision, arrival, prices, qtys, prices, target, final_price=final)
    assert c.opportunity_bps == 0.0
    assert c.opportunity_priced is False
