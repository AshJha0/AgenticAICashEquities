"""Slippage / effective spread: known-answer tests."""

from __future__ import annotations

import math

import pytest

from ceap.analytics.slippage import effective_spread_bps, per_fill_slippage_bps, slippage_bps
from ceap.domain.execution import Side


def test_slippage_signed_by_side():
    assert slippage_bps(Side.BUY, 100.1, 100.0) == pytest.approx(10.0)
    assert slippage_bps(Side.SELL, 100.1, 100.0) == pytest.approx(-10.0)


def test_slippage_zero_reference_nan():
    assert math.isnan(slippage_bps(Side.BUY, 100.0, 0.0))


def test_per_fill_slippage_vectorised_and_nan_safe():
    out = per_fill_slippage_bps(Side.BUY, [101.0, 99.0], [100.0, 0.0])
    assert out[0] == pytest.approx(100.0)
    assert math.isnan(out[1])


def test_effective_spread_is_twice_distance_to_mid():
    assert effective_spread_bps([100.05], [100.0], Side.BUY) == pytest.approx(10.0)
