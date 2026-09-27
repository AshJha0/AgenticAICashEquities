"""Volatility / drift: known-answer tests."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ceap.analytics.volatility import (
    annualise_volatility,
    max_abs_move_bps,
    price_drift_bps,
    realised_volatility_bps,
)


def test_realised_volatility_scales_with_horizon():
    rng = np.random.default_rng(0)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 1e-4, 5000)))
    per_min = realised_volatility_bps(prices, 1.0, 60.0)
    per_sec = realised_volatility_bps(prices, 1.0, 1.0)
    assert per_min == pytest.approx(per_sec * math.sqrt(60), rel=1e-9)
    assert per_sec == pytest.approx(1.0, rel=0.1)  # 1e-4 = 1 bp per second


def test_annualised_volatility_reasonable():
    rng = np.random.default_rng(1)
    sigma = 0.20 / math.sqrt(252 * 23400)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, sigma, 20000)))
    assert annualise_volatility(prices, 1.0) == pytest.approx(0.20, rel=0.1)


def test_drift_and_jump_detector():
    prices = [100.0] * 60 + [101.0] * 60
    assert price_drift_bps(prices) == pytest.approx(100.0)
    assert max_abs_move_bps(prices, window=1) == pytest.approx(100.0)
    assert math.isnan(realised_volatility_bps([100.0]))


def test_drift_single_price_nan():
    assert math.isnan(price_drift_bps([100.0]))
