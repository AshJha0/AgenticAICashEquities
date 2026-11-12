"""Edge cases of the risk maths: refusals on too little data, the degenerate branches of the beta
estimate, liquidation with no ADV, the sigma price band and the robustness report.

Each test states the number it expects and why; none only executes lines.
"""

from __future__ import annotations

import copy
import math

import numpy as np
import pytest

from ceap.analytics import risk_research as rr
from ceap.analytics.portfolio_risk import raw_betas_ex_self, shrink_betas, stress
from ceap.paper.pretrade import RiskLimits, sigma_band


# ------------------------------------------------------------------ refusals on too little data
def test_ledoit_wolf_needs_two_complete_rows_and_drops_incomplete_ones():
    with pytest.raises(ValueError, match="two complete rows"):
        rr.ledoit_wolf(np.array([[0.01, 0.02], [np.nan, 0.01]]))
    x = np.random.default_rng(1).normal(size=(40, 3))
    with_gap = np.vstack([x, [np.nan, 0.0, 0.0]])
    cov, delta = rr.ledoit_wolf(with_gap)
    assert np.allclose(cov, rr.ledoit_wolf(x)[0]) and 0.0 <= delta <= 1.0


def test_ledoit_wolf_on_an_already_scaled_identity_shrinks_fully():
    # columns with equal variance and no correlation: sample == target, d2 == 0, delta defined as 1
    x = np.array([[1.0, 1.0], [-1.0, -1.0], [1.0, -1.0], [-1.0, 1.0]])
    cov, delta = rr.ledoit_wolf(x)
    assert delta == 1.0 and np.allclose(cov, np.eye(2))


def test_factor_regression_refuses_too_few_observations_after_dropping_gaps():
    y = np.array([0.01, 0.02, np.nan, 0.03, 0.0])
    f = np.array([0.0, 0.01, 0.02, np.nan, 0.01])
    with pytest.raises(ValueError, match="too few"):
        rr.factor_regression(y, f, ("mkt",))


def test_factor_regression_explicit_lags_and_constant_y():
    f = np.random.default_rng(2).normal(size=(30, 1))
    # y == 0 exactly (a constant like 0.01 leaves a few ulps of "variance" and a meaningless R^2)
    r = rr.factor_regression(np.zeros(30), f, ("mkt",), lags=0)
    assert r["nw_lags"] == 0 and r["alpha_per_period"] == pytest.approx(0.0, abs=1e-15)
    assert math.isnan(r["r2"])  # no variance to explain
    assert math.isnan(r["alpha_t"])  # zero residual: no standard error, no t


def test_residualise_returns_nan_without_enough_names():
    s = np.array([1.0, 2.0, np.nan])
    x = np.array([[0.1], [0.2], [0.3]])
    assert np.isnan(rr.residualise(s, x)).all()


def test_multi_factor_stress_ignores_factors_without_a_loading():
    out = rr.multi_factor_stress({"mkt_rf": 1.0}, rr.STRESS_SCENARIOS)
    assert out["2008-10 crisis"] == pytest.approx(-0.17)  # smb/hml/umd have no loading
    assert set(out) == set(rr.STRESS_SCENARIOS)


def test_tail_report_dates_and_flat_series():
    r = np.r_[np.full(30, 0.0), np.full(21, -0.01), np.full(30, 0.0)]
    dates = np.array([f"d{i}" for i in range(r.size)])
    rep = rr.tail_report(r, dates)
    assert rep["worst_21d"][0]["start"] == "d30"
    assert rep["worst_21d"][0]["return"] == pytest.approx(-0.21)
    flat = rr.tail_report(np.zeros(40))
    assert flat["skew"] == 0.0 and flat["max_drawdown"] == 0.0


def test_regime_split_sharpe_is_nan_on_a_constant_regime():
    m = np.random.default_rng(3).normal(0, 0.01, 300)
    reg = rr.regime_split(np.zeros(300), m, window=20)
    assert math.isnan(reg["calm_sharpe"]) and math.isnan(reg["stressed_sharpe"])
    assert reg["calm_days"] + reg["stressed_days"] == 300 - 21


def test_momentum_variant_is_lookback_return_skipping_the_recent_window():
    close = np.arange(1.0, 11.0)[:, None]  # 1..10
    sig = rr._momentum_variant(lookback=4, skip=1)(close)
    assert np.isnan(sig[:4]).all()
    # row 4: close[3] / close[0] - 1 = 4 / 1 - 1
    assert sig[4, 0] == pytest.approx(3.0) and sig[9, 0] == pytest.approx(9 / 6 - 1)
    assert np.isnan(rr._momentum_variant(20, 1)(close)).all()  # history shorter than the lookback


def test_robustness_report_on_the_default_dataset():
    from ceap.data.historical import HistoricalStore
    from ceap.mcp.common import DEFAULT_RESEARCH_DATASET, select_history

    ds = select_history(HistoricalStore(), DEFAULT_RESEARCH_DATASET)
    before = set(__import__("ceap.analytics.signals", fromlist=["SIGNALS"]).SIGNALS)
    rep = rr.robustness_report(ds, cost_multipliers=(1.0, 3.0), neighbours=((252, 21),))
    assert set(rep["cost_multiplier"]) == {"x1", "x3"} and set(rep["lookback_skip"]) == {"252-21"}
    # higher costs never raise the net Sharpe
    assert rep["cost_multiplier"]["x3"] <= rep["cost_multiplier"]["x1"]
    assert len(rep["halves"]) == 2 and len(rep["sub_periods"]) == 3
    assert isinstance(rep["sign_stable"], bool)
    # the temporary neighbour signals are removed again
    from ceap.analytics.signals import SIGNALS

    assert set(SIGNALS) == before


# ------------------------------------------------------------------ betas
def test_raw_beta_skips_a_column_with_no_market_variance():
    # column 0's "market" (mean of the others) is constant, so it keeps the prior beta 1 and se inf
    # exact zeros: a constant like 0.02 averages to values a few ulps apart, which is not degenerate
    w = np.array([[0.01, 0.0, 0.0], [0.03, 0.0, 0.0], [-0.01, 0.0, 0.0]] * 10)
    beta, se = raw_betas_ex_self(w, min_obs=5)
    assert beta[0] == 1.0 and math.isinf(se[0])


def test_shrink_betas_with_fewer_than_two_estimates_keeps_them_raw():
    out = shrink_betas(np.array([1.7, 0.4]), np.array([0.1, np.inf]), target=1.0)
    assert out.tolist() == [1.7, 1.0]  # one estimate: no cross-section to shrink toward


# ------------------------------------------------------------------ stress with no ADV
def test_liquidation_days_are_infinite_when_a_name_has_no_adv():
    from ceap.analytics.portfolio_risk import target_portfolio
    from ceap.data.historical import HistoricalStore
    from ceap.mcp.common import DEFAULT_RESEARCH_DATASET, select_history

    ds = select_history(HistoricalStore(), DEFAULT_RESEARCH_DATASET)
    report = target_portfolio(ds, "momentum_12_1", None, True, 1_000_000.0)
    base = stress(report, ds=ds)
    assert math.isfinite(base.liquidation_days) and base.liquidation_cost > 0
    t = ds.index_at_or_before(report.as_of)
    held = report.positions[0].symbol
    broken = copy.copy(ds)
    adv = np.array(ds.adv_20, copy=True)
    adv[t, ds.col(held)] = np.nan
    object.__setattr__(broken, "adv_20", adv)
    out = stress(report, ds=broken)
    assert math.isinf(out.liquidation_days)
    # the name with no ADV adds no liquidation cost: the total can only fall
    assert out.liquidation_cost < base.liquidation_cost
    assert np.isfinite(ds.adv_20[t, ds.col(held)])  # the shared dataset was not touched


# ------------------------------------------------------------------ sigma price band
def test_sigma_band_falls_back_to_the_cap_with_too_few_returns():
    limits = RiskLimits(price_band_sigma=6.0, price_band_cap=0.10, sigma_window=20)
    adj = np.linspace(100.0, 101.0, 40)[:, None]
    assert sigma_band(adj, 5, 0, limits) == 0.10  # 4 returns < max(5, 10)
    gappy = adj.copy()
    gappy[10:30, 0] = np.nan
    assert sigma_band(gappy, 30, 0, limits) == 0.10  # NaN returns are dropped before counting


def test_sigma_band_is_k_sigma_capped():
    rng = np.random.default_rng(4)
    r = rng.normal(0, 0.01, 30)
    adj = (100.0 * np.cumprod(1 + r))[:, None]
    limits = RiskLimits(price_band_sigma=6.0, price_band_cap=0.10, sigma_window=20)
    seg = adj[10:30, 0]
    expected = 6.0 * float(np.std(seg[1:] / seg[:-1] - 1.0, ddof=1))
    assert sigma_band(adj, 30, 0, limits) == pytest.approx(min(0.10, expected))
    assert sigma_band(adj, 30, 0, RiskLimits(price_band_sigma=60.0, sigma_window=20)) == 0.10
