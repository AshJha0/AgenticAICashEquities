"""Backtest engine: weights, cost model, period statistics and walk-forward split."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ceap.analytics.backtest import (
    CostModel,
    daily_returns,
    period_stats,
    rolling_vol_bps,
    run_backtest,
    target_weights,
)


def test_long_short_weights_are_dollar_neutral_and_unit_gross():
    z = np.array([3.0, 2.0, 1.0, 0.0, -1.0, -2.0])
    w = target_weights(z, long_short=True, top_fraction=1 / 3)
    assert w.tolist() == [0.25, 0.25, 0.0, 0.0, -0.25, -0.25]
    assert np.abs(w).sum() == pytest.approx(1.0) and w.sum() == pytest.approx(0.0)
    long_only = target_weights(z, long_short=False, top_fraction=1 / 3)
    assert long_only.tolist() == [0.5, 0.5, 0.0, 0.0, 0.0, 0.0]


def test_weights_ignore_nan_and_degenerate_inputs():
    w = target_weights(np.array([np.nan, 2.0, 1.0, np.nan, -1.0, -2.0]))
    assert w[0] == 0.0 and w[3] == 0.0 and np.abs(w).sum() == pytest.approx(1.0)
    assert target_weights(np.array([1.0, np.nan])).tolist() == [0.0, 0.0]
    ranked = target_weights(np.array([3.0, 2.0, 1.0, 0.0, -1.0, -2.0]), weighting="rank")
    assert ranked[0] > ranked[1] > 0 and ranked[5] < ranked[4] < 0
    assert np.abs(ranked).sum() == pytest.approx(1.0)


def test_cost_model_formula_and_fallbacks():
    model = CostModel(impact_coefficient=0.6)
    cost = model.trade_cost_bps(np.array([5.0]), np.array([100.0]), np.array([0.04]))
    assert cost[0] == pytest.approx(5.0 + 0.6 * 100.0 * 0.2)
    assert model.trade_cost_bps(np.array([5.0]), np.array([np.nan]), np.array([np.nan]))[0] == pytest.approx(5.0)


def test_daily_returns_and_rolling_vol_shapes():
    close = np.array([[100.0], [110.0], [99.0]])
    r = daily_returns(close)
    assert math.isnan(r[0, 0]) and r[1, 0] == pytest.approx(0.1) and r[2, 0] == pytest.approx(-0.1)
    rng = np.random.default_rng(5)
    r = daily_returns(100 * np.exp(np.cumsum(rng.normal(0, 0.01, (50, 2)), axis=0)))
    vol = rolling_vol_bps(r, 20)
    assert np.isnan(vol[:20]).all() and np.isfinite(vol[20:]).all() and vol[20:].mean() > 50


def test_period_stats_known_answer():
    dates = np.array(["2026-01-05", "2026-01-06", "2026-01-07"], dtype="datetime64[D]")
    net = np.array([0.01, -0.01, 0.02])
    gross = net + 0.001
    stats = period_stats("full", dates, 0, 2, net, gross, np.array([0.5, 0.0, 0.5]), 2)
    mean, std = net.mean(), net.std(ddof=1)
    assert stats.sharpe == pytest.approx(mean / std * math.sqrt(252))
    assert stats.ann_return == pytest.approx(mean * 252)
    assert stats.cagr == pytest.approx(np.prod(1 + net) ** (252 / 3) - 1)
    assert stats.max_drawdown == pytest.approx(0.01)
    assert stats.hit_rate == pytest.approx(2 / 3)
    assert stats.cost_drag_bps_annual == pytest.approx(0.001 * 252 * 1e4)
    assert stats.n_rebalances == 2 and stats.start == "2026-01-05" and stats.end == "2026-01-07"
    empty = period_stats("x", dates, 0, 0, net, gross, np.zeros(3), 0)
    assert math.isnan(empty.sharpe) and empty.n_days == 1


def test_backtest_invariants_on_the_momentum_dataset(history):
    ds = history.get("R01")
    bt = run_backtest(ds, "momentum_12_1", ds.start, ds.end, ds.in_sample_end)
    ins, oos, full = bt.periods["in_sample"], bt.periods["out_of_sample"], bt.periods["full"]
    assert ins.end == ds.in_sample_end and oos.start > ins.end and full.end == ds.end
    assert ins.n_days + oos.n_days == full.n_days
    assert ins.n_rebalances + oos.n_rebalances == full.n_rebalances == len(range(0, full.n_days, 21))
    assert full.cost_drag_bps_annual > 0 and full.sharpe < full.gross_sharpe
    assert 0.0 <= full.max_drawdown <= 1.0 and 0.0 <= full.hit_rate <= 1.0
    assert 2 <= len(bt.equity_curve) <= 24 and bt.equity_curve[0][0] == ds.start
    assert len(bt.top_contributors) == 5 and 0.0 < bt.pnl_concentration_top3 < 1.0
    assert bt.sharpe_ratio_oos_is == pytest.approx(oos.sharpe / ins.sharpe)
    free = run_backtest(ds, "momentum_12_1", ds.start, ds.end, ds.in_sample_end, cost_multiplier=0.0)
    assert free.periods["full"].cost_drag_bps_annual == pytest.approx(0.0)
    assert free.periods["full"].sharpe == pytest.approx(free.periods["full"].gross_sharpe)
    dear = run_backtest(ds, "momentum_12_1", ds.start, ds.end, ds.in_sample_end, cost_multiplier=5.0)
    assert dear.periods["full"].sharpe < bt.periods["full"].sharpe


def test_backtest_rejects_bad_parameters(history):
    ds = history.get("R01")
    with pytest.raises(ValueError):
        run_backtest(ds, "momentum_12_1", rebalance_days=0)
    with pytest.raises(ValueError):
        run_backtest(ds, "momentum_12_1", gross_notional=0)
    with pytest.raises(KeyError):
        run_backtest(ds, "nope")
