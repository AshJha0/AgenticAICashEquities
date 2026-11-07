# ruff: noqa: N803, N806  (mathematical notation: R, P, T, A, B as in the formulas)
"""R2 backtest realism: execution lag (2.1), drift (2.2), financing (2.3), data policy (2.5), membership (2.13)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from ceap.analytics.backtest import (
    FinancingModel,
    PositionCaps,
    _Book,
    _Sim,
    run_backtest,
    simulate_backtest,
)
from ceap.data.historical import HistoricalStore, lagged_adv

OFF = FinancingModel(enabled=False)
NO_CAPS = PositionCaps(max_weight=1.0, max_participation=1e9)


@pytest.fixture(scope="module")
def store() -> HistoricalStore:
    return HistoricalStore()


# ------------------------------------------------------------------ 2.1 execution lag
def test_2_1_reversal_sign_flip_under_next_day_execution(store):
    """REVIEW 3.2 #7: legacy R03 net Sharpe 1.80 at the same close, about -0.67 one day later."""
    ds = store.get("L03")
    same_close = run_backtest(
        ds, "reversal_5", rebalance_days=5, execution_lag_days=0, drift=False, financing=OFF, caps=NO_CAPS
    )
    # the reviewer kept the rebalance calendar and lagged the signal: decision t-1, fill t
    lagged = run_backtest(
        ds, "reversal_5", start=str(ds.dates[ds.start_index - 1]), rebalance_days=5, execution_lag_days=1,
        drift=False, financing=OFF, caps=NO_CAPS,
    )
    assert same_close.period("full").sharpe == pytest.approx(1.80, abs=0.1)
    assert lagged.period("full").sharpe == pytest.approx(-0.67, abs=0.1)
    assert same_close.execution_lag_days == 0 and lagged.execution_lag_days == 1


def test_2_1_lag_sensitivity_is_monotone_for_a_fast_signal(store):
    ds = store.get("R03")
    sharpe = [run_backtest(ds, "reversal_5", rebalance_days=5, execution_lag_days=k).period("full").sharpe
              for k in (0, 1, 3)]
    assert sharpe[0] > sharpe[1] > sharpe[2]


def test_2_1_fill_rules(store):
    ds = store.get("R01")
    with pytest.raises(ValueError, match="open fill"):
        run_backtest(ds, "momentum_12_1", fill="open", execution_lag_days=0)
    with pytest.raises(ValueError):
        run_backtest(ds, "momentum_12_1", fill="vwap")
    res = run_backtest(ds, "momentum_12_1", fill="open", execution_lag_days=1)
    assert res.fill == "open" and np.isfinite(res.period("full").sharpe)
    assert "fill at the open of t+1" in res.assumptions["execution"]


# ------------------------------------------------------------------ 2.2 drift
def _sim(m: int, drift: bool) -> _Sim:
    n = 4
    return _Sim(ds=None, n=n, m=m, drift=drift, rf=np.zeros(n), borrow_daily=np.zeros(m), spread_daily=0.0,
                nav=np.full(n, np.nan), costs={k: np.zeros(n) for k in ("spread", "impact", "borrow", "financing")},
                contrib=np.zeros(m), events=[], counts={})


def test_2_2_two_name_three_day_known_answer():
    R = np.array([[0.10, -0.10], [0.0, 0.10], [0.05, 0.0]])
    sim = _sim(2, drift=True)
    book = sim.hold(_Book(np.array([0.5, 0.5]), 0.0), R, [1, 2, 3], np.zeros(3))
    assert book.P == pytest.approx([0.5775, 0.495])
    assert sim.nav[1:] == pytest.approx([1.0, 1.045, 1.0725])
    w = book.P / book.nav
    assert w == pytest.approx([0.53846154, 0.46153846])
    assert float(np.abs(np.array([0.5, 0.5]) - w).sum()) == pytest.approx(0.07692308)  # turnover back to target
    fixed = _sim(2, drift=False)
    fixed.hold(_Book(np.array([0.5, 0.5]), 0.0), R, [1, 2, 3], np.zeros(3))
    assert fixed.nav[3] == pytest.approx(1.0 * 1.05 * 1.025)  # constant weights: daily free rebalance


def test_2_2_drift_raises_measured_turnover(store):
    ds = store.get("R01")
    drifted = run_backtest(ds, "momentum_12_1").period("full")
    fixed = run_backtest(ds, "momentum_12_1", drift=False).period("full")
    assert drifted.turnover_annual > fixed.turnover_annual


# ------------------------------------------------------------------ 2.3 financing
def test_2_3_long_only_sharpe_falls_by_rf_over_sigma(store):
    ds = store.get("R01")
    rf_daily = 0.03 / 252
    base = simulate_backtest(ds, "momentum_12_1", long_short=False, caps=NO_CAPS, risk_free=np.zeros(ds.n_days))
    with_rf = simulate_backtest(
        ds, "momentum_12_1", long_short=False, caps=NO_CAPS, risk_free=np.full(ds.n_days, rf_daily)
    )
    b, r = base.result.period("full"), with_rf.result.period("full")
    expected_drop = 0.03 / b.ann_vol
    assert b.sharpe - r.sharpe == pytest.approx(expected_drop, rel=0.02)
    assert r.rf_ann == pytest.approx(0.03)


def test_2_3_dollar_neutral_book_is_self_financing_and_pays_borrow(store):
    ds = store.get("R01")
    base = simulate_backtest(ds, "momentum_12_1", risk_free=np.zeros(ds.n_days)).result.period("full")
    rf = simulate_backtest(ds, "momentum_12_1", risk_free=np.full(ds.n_days, 0.03 / 252)).result.period("full")
    assert rf.sharpe == pytest.approx(base.sharpe, abs=0.02)  # short rebate offsets rf
    res = run_backtest(ds, "momentum_12_1")
    assert res.cost_breakdown_bps_annual["borrow"] > 0
    assert set(res.cost_breakdown_bps_annual) == {"spread", "impact", "borrow", "financing"}
    assert res.assumptions["borrow_bps_annual"] == {"A": 50.0, "B": 200.0, "C": 1000.0}


def test_2_3_leverage_limit_and_ssr_log(store):
    ds = store.get("R01")
    with pytest.raises(ValueError, match="Reg T"):
        run_backtest(ds, "momentum_12_1", gross_leverage=2.5)
    two_x = run_backtest(ds, "momentum_12_1", gross_leverage=2.0, caps=NO_CAPS)
    assert two_x.period("full").turnover_annual > run_backtest(ds, "momentum_12_1", caps=NO_CAPS).period("full").turnover_annual
    crash = store.get("R10")
    res = run_backtest(crash, "momentum_12_1")
    blocked = [e for e in res.events if e["kind"] == "ssr_blocked"]
    assert res.event_counts.get("ssr_blocked", 0) == len(blocked) > 0
    assert all({"date", "symbol", "target", "held"} <= set(e) for e in blocked)


# ------------------------------------------------------------------ 2.5 data policy
def test_2_5_adv_is_lagged_one_day():
    v = np.arange(1.0, 31.0)[:, None] * np.ones((1, 2))
    adv = lagged_adv(v, window=20, min_obs=5)
    assert np.isnan(adv[:5]).all()
    assert adv[5, 0] == pytest.approx(np.mean(v[0:5, 0]))
    assert adv[25, 0] == pytest.approx(np.mean(v[5:25, 0]))  # excludes day 25 itself


def test_2_5_participation_cap_breaches_are_logged_not_traded(store):
    ds = store.get("R06")
    res = run_backtest(ds, "momentum_12_1", gross_notional=500e6)
    capped = [e for e in res.events if e["kind"] == "participation_cap"]
    assert res.event_counts["participation_cap"] >= len(capped) > 0
    assert all(abs(e["allowed"]) < abs(e["wanted"]) for e in capped)


def test_2_5_delisting_bounds_are_both_reported(store):
    ds = store.get("R01")
    assert {k for _, k in ds.delistings.values()} == {"merger", "performance"}
    for signal in ("momentum_12_1", "reversal_5", "low_vol_60"):
        res = run_backtest(ds, signal, rebalance_days=5)
        if res.event_counts.get("delisting"):
            b = res.delisting_bounds
            assert set(b) == {"merger", "performance"}
            assert all(np.isfinite(v["full_sharpe"]) for v in b.values())
            return
    pytest.fail("no backtest held a delisting name")


def test_2_5_nan_price_on_a_held_name_is_logged(store):
    ds = store.get("R01")
    close = ds.close.copy()
    res0 = simulate_backtest(ds, "momentum_12_1")
    e = sorted(res0.fill_weights)[40]
    j = int(np.flatnonzero(res0.fill_weights[e])[0])
    close[e + 1, j] = np.nan
    broken = replace(ds, close=close)
    res = run_backtest(broken, "momentum_12_1")
    assert res.event_counts.get("nan_return", 0) >= 1


# ------------------------------------------------------------------ 2.13 membership
def test_2_13_non_members_are_never_held(store):
    rng = np.random.default_rng(7)
    ds = store.get("R03")
    for _ in range(3):
        mask = ds.member_mask.copy()
        cols = rng.choice(ds.n_symbols, size=6, replace=False)
        for j in cols:
            a = int(rng.integers(ds.start_index, ds.n_days - 300))
            mask[a : a + int(rng.integers(50, 300)), j] = False
        close = np.where(mask, ds.close, np.nan)
        test_ds = replace(ds, members=mask, close=close)
        run = simulate_backtest(test_ds, "reversal_5", rebalance_days=5, execution_lag_days=1)
        for e, w in run.fill_weights.items():
            assert np.all(w[~mask[e]] == 0.0), f"non-member held on {ds.dates[e]}"
