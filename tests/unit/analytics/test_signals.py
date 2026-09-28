"""Signal library: known-answer tests."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ceap.analytics.signals import (
    SIGNALS,
    compute_signal,
    cross_sectional_zscore,
    get_signal,
    low_vol_60,
    momentum_12_1,
    reversal_5,
    zscore_matrix,
)


def test_momentum_uses_close_21_days_ago_over_close_252_days_ago():
    n = 300
    close = np.column_stack([np.arange(1.0, n + 1.0), np.full(n, 10.0)])
    out = momentum_12_1(close)
    assert np.isnan(out[:252]).all()
    assert out[299, 0] == pytest.approx(close[278, 0] / close[47, 0] - 1.0)
    assert out[252:, 1] == pytest.approx(0.0)


def test_reversal_is_minus_five_day_return_with_warm_up():
    close = np.arange(1.0, 12.0).reshape(-1, 1)
    out = reversal_5(close)
    assert np.isnan(out[:5]).all()
    assert out[5, 0] == pytest.approx(-(6.0 / 1.0 - 1.0))
    assert out[10, 0] == pytest.approx(-(11.0 / 6.0 - 1.0))


def test_low_vol_prefers_the_calmer_name():
    rng = np.random.default_rng(0)
    calm = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 200)))
    wild = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 200)))
    out = low_vol_60(np.column_stack([calm, wild]))
    assert np.isnan(out[:60]).all()
    assert out[199, 0] > out[199, 1]
    assert out[199, 1] < 0


def test_signals_only_use_past_closes():
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, (400, 4)), axis=0))
    for name in SIGNALS:
        full = compute_signal(name, close)
        truncated = compute_signal(name, close[:300])
        assert np.allclose(full[:300], truncated, equal_nan=True), name


def test_zscore_handles_nan_constant_and_clipping():
    z = cross_sectional_zscore(np.array([1.0, 2.0, 3.0, np.nan]))
    assert math.isnan(z[3]) and z[1] == pytest.approx(0.0) and z[0] == pytest.approx(-z[2])
    assert cross_sectional_zscore(np.array([5.0, 5.0, 5.0])).tolist() == [0.0, 0.0, 0.0]
    assert cross_sectional_zscore(np.array([7.0, np.nan])).tolist()[0] == 0.0
    extreme = cross_sectional_zscore(np.array([0.0] * 20 + [1000.0]), clip=3.0)
    assert extreme.max() == pytest.approx(3.0)
    assert zscore_matrix(np.array([[1.0, 3.0], [2.0, 2.0]])).shape == (2, 2)


def test_registry_and_unknown_signal():
    assert set(SIGNALS) == {"momentum_12_1", "reversal_5", "low_vol_60"}
    assert get_signal("momentum_12_1").lookback_days == 252
    assert get_signal("reversal_5").describe()["default_rebalance_days"] == 5
    with pytest.raises(KeyError):
        get_signal("nope")
