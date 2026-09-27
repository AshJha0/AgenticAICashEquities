"""Implementation shortfall: known-answer tests."""

from __future__ import annotations

import math

import pytest

from ceap.analytics.implementation_shortfall import execution_cost_bps, implementation_shortfall_bps
from ceap.domain.execution import Side


def test_is_buy_positive_when_paying_up():
    # bought 100 @ 101 vs decision 100 -> +100 bps
    assert implementation_shortfall_bps(Side.BUY, 100.0, [101.0], [100], 100) == pytest.approx(100.0)


def test_is_sell_sign_flips():
    assert implementation_shortfall_bps(Side.SELL, 100.0, [99.0], [100], 100) == pytest.approx(100.0)
    assert implementation_shortfall_bps(Side.SELL, 100.0, [101.0], [100], 100) == pytest.approx(-100.0)


def test_is_includes_opportunity_cost_for_unfilled():
    # filled 50 at decision price; 50 unfilled while price rose 2% -> 0.5 * 200 bps = 100 bps
    assert implementation_shortfall_bps(
        Side.BUY, 100.0, [100.0], [50], 100, final_price=102.0
    ) == pytest.approx(100.0)
    assert implementation_shortfall_bps(Side.BUY, 100.0, [100.0], [50], 100) == pytest.approx(0.0)


def test_is_invalid_inputs_nan():
    assert math.isnan(implementation_shortfall_bps(Side.BUY, 0.0, [1.0], [1], 1))
    assert math.isnan(execution_cost_bps(Side.BUY, 100.0, float("nan")))


def test_is_zero_target_quantity_nan():
    assert math.isnan(implementation_shortfall_bps(Side.BUY, 100.0, [], [], 0))


def test_is_negative_decision_price_nan():
    assert math.isnan(implementation_shortfall_bps(Side.BUY, -1.0, [100.0], [100], 100))
