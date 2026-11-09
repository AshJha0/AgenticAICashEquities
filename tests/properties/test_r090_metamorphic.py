"""I5 (0.9.0): metamorphic tests for the deterministic analytics.

Plain idea: we often cannot say what the right answer is for a random input, but we can say how
the answer must change when the input changes in a known way. Formal statement, for any finite
fills and any scale ``k > 0``:

* VWAP is order-invariant, scales with price (``vwap(k p, q) = k vwap(p, q)``) and is invariant to
  scaling quantity (``vwap(p, k q) = vwap(p, q)``);
* implementation shortfall in bps is invariant to scaling every price by ``k`` and to scaling every
  quantity by ``k`` (it is a ratio of notionals);
* flipping BUY to SELL negates the shortfall;
* realised volatility of log returns is invariant to scaling the price path by ``k``.

Worked example: buy 100 at 10.10 against decision 10.00 is 100 bps; the same order at 1010 against
1000 is still 100 bps, and the same order as a sell is -100 bps.
"""

from __future__ import annotations

import math

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from ceap.analytics.implementation_shortfall import implementation_shortfall_bps
from ceap.analytics.volatility import realised_volatility_bps
from ceap.analytics.vwap import vwap
from ceap.domain.execution import Side

price = st.floats(min_value=1e-2, max_value=1e5, allow_nan=False, allow_infinity=False)
qty = st.floats(min_value=1e-3, max_value=1e8, allow_nan=False, allow_infinity=False)
scale = st.floats(min_value=1e-3, max_value=1e3, allow_nan=False, allow_infinity=False)
fills = st.lists(st.tuples(price, qty), min_size=1, max_size=30)
side = st.sampled_from([Side.BUY, Side.SELL])


def close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


@given(fills, scale, st.randoms(use_true_random=False))
def test_vwap_metamorphic_relations(pq, k, rnd):
    p, q = (list(x) for x in zip(*pq, strict=True))
    base = vwap(p, q)
    order = list(range(len(p)))
    rnd.shuffle(order)
    assert close(vwap([p[i] for i in order], [q[i] for i in order]), base)
    assert math.isclose(vwap([k * x for x in p], q), k * base, rel_tol=1e-9)
    assert math.isclose(vwap(p, [k * x for x in q]), base, rel_tol=1e-9)


@given(side, price, fills, st.floats(min_value=0.0, max_value=2.0), price, scale)
def test_is_bps_scale_invariance_and_side_flip(s, decision, pq, extra, final, k):
    p, q = (list(x) for x in zip(*pq, strict=True))
    target = sum(q) * (1.0 + extra)
    base = implementation_shortfall_bps(s, decision, p, q, target, final_price=final)
    scaled_px = implementation_shortfall_bps(s, k * decision, [k * x for x in p], q, target, final_price=k * final)
    scaled_q = implementation_shortfall_bps(s, decision, p, [k * x for x in q], k * target, final_price=final)
    other = Side.SELL if s is Side.BUY else Side.BUY
    flipped = implementation_shortfall_bps(other, decision, p, q, target, final_price=final)
    tol = 1e-7 * (1.0 + abs(base))
    assert abs(scaled_px - base) <= tol
    assert abs(scaled_q - base) <= tol
    assert abs(flipped + base) <= tol


def test_is_worked_example():
    assert math.isclose(implementation_shortfall_bps(Side.BUY, 10.0, [10.10], [100.0], 100.0), 100.0)
    assert math.isclose(implementation_shortfall_bps(Side.BUY, 1000.0, [1010.0], [100.0], 100.0), 100.0)
    assert math.isclose(implementation_shortfall_bps(Side.SELL, 10.0, [10.10], [100.0], 100.0), -100.0)


@given(st.lists(price, min_size=3, max_size=60), scale)
def test_realised_vol_is_price_scale_invariant(path, k):
    base = realised_volatility_bps(np.asarray(path))
    scaled = realised_volatility_bps(np.asarray(path) * k)
    assert math.isclose(scaled, base, rel_tol=1e-6, abs_tol=1e-6)
