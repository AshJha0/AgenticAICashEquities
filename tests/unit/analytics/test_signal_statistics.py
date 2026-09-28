"""Signal statistics: ranks, Spearman IC, turnover, decay and period aggregation."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ceap.analytics.signal_statistics import (
    evaluate_signal,
    forward_returns,
    ic_series,
    quantile_spread_series,
    rank_average,
    spearman,
    turnover_series,
)


def test_average_ranks_share_ties_and_keep_nan():
    r = rank_average(np.array([10.0, 20.0, 20.0, 30.0, np.nan]))
    assert r[:4].tolist() == [1.0, 2.5, 2.5, 4.0] and math.isnan(r[4])


def test_spearman_known_answers():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert spearman(x, x) == pytest.approx(1.0)
    assert spearman(x, -x) == pytest.approx(-1.0)
    assert spearman(np.array([1, 2, 3, 4, 5]), np.array([5, 6, 7, 8, 7])) == pytest.approx(0.8207826816681233)
    assert math.isnan(spearman(x, np.full(5, 3.0)))
    assert math.isnan(spearman(np.array([1.0, 2.0]), np.array([1.0, 2.0])))
    assert spearman(np.array([1.0, 2.0, np.nan, 4.0]), np.array([1.0, 2.0, 3.0, 4.0])) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        spearman(x, x[:3])


def test_forward_returns_align_to_the_observation_date():
    close = np.array([[1.0], [2.0], [4.0], [8.0]])
    fwd = forward_returns(close, 1)
    assert fwd[:3, 0].tolist() == [1.0, 1.0, 1.0] and math.isnan(fwd[3, 0])
    assert forward_returns(close, 2)[0, 0] == pytest.approx(3.0)
    with pytest.raises(ValueError):
        forward_returns(close, 0)


def test_ic_is_one_when_the_signal_is_the_forward_return():
    rng = np.random.default_rng(2)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, (12, 10)), axis=0))
    signal = forward_returns(close, 1)
    ic = ic_series(signal, close, 1)
    assert ic[:11] == pytest.approx(np.ones(11)) and math.isnan(ic[11])
    assert np.isnan(ic_series(signal[:, :5], close[:, :5], 1)).all()  # fewer than MIN_NAMES


def test_turnover_and_quantile_spread():
    rng = np.random.default_rng(3)
    static = np.tile(rng.normal(size=10), (30, 1))
    assert turnover_series(static, 5)[5:] == pytest.approx(np.zeros(25))
    noisy = rng.normal(size=(30, 10))
    assert np.nanmean(turnover_series(noisy, 1)) > 0.5
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, (12, 10)), axis=0))
    spread = quantile_spread_series(forward_returns(close, 1), close, 1)
    assert (spread[:11] > 0).all() and math.isnan(spread[11])


def test_null_signal_has_no_significant_ic():
    rng = np.random.default_rng(4)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, (600, 20)), axis=0))
    signal = rng.normal(size=(600, 20))
    ic = ic_series(signal, close, 1)
    valid = ic[np.isfinite(ic)]
    t = valid.mean() / (valid.std(ddof=1) / np.sqrt(valid.size))
    assert abs(t) < 3.0


def test_evaluate_signal_periods_on_the_momentum_dataset(history):
    ds = history.get("R01")
    stats = evaluate_signal(ds, "momentum_12_1", ds.start, ds.end, ds.in_sample_end)
    assert set(stats.periods) == {"in_sample", "out_of_sample", "full"}
    ins, oos, full = stats.periods["in_sample"], stats.periods["out_of_sample"], stats.periods["full"]
    assert ins.start == ds.start and ins.end == ds.in_sample_end and full.end == ds.end
    assert oos.start > ins.end
    assert ins.n_dates + oos.n_dates == full.n_dates
    assert ins.ic_t_stat > 2.0 and 0 <= ins.hit_rate <= 1 and 0 <= ins.turnover <= 2
    assert set(ins.decay) == {"1", "5", "21"} and stats.horizon_days == 21 and stats.universe_size == 30
