"""0.10 WS-D research realism: trial accounting, a-priori hurdle, INCONCLUSIVE, overfitting diagnostics,
Newey-West over gaps, the leakage canary, adversarial generator families and the recalibrated premia."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from ceap.analytics.overfitting import (
    diebold_mariano,
    mean_ci,
    paired_sharpe_difference,
    pbo_cscv,
    sharpe_ci,
    stationary_bootstrap_indices,
)
from ceap.analytics.research_assessment import (
    DEFAULT_THRESHOLDS,
    MINED_HURDLE,
    SINGLE_TEST_HURDLE,
    hurdle_for,
    sharpe_interval,
    trial_count,
)
from ceap.analytics.signal_statistics import andrews_bandwidth, newey_west_t
from ceap.analytics.trial_accounting import (
    deflation_trial_count,
    effective_trials_cluster,
    effective_trials_eigen,
    per_period_sharpe_variance,
    trial_correlation,
)
from ceap.domain.research import ResearchVerdict


# ---------------------------------------------------------------- D8 trial accounting
def test_effective_trials_identical_and_independent():
    assert effective_trials_eigen(np.ones((4, 4))) == pytest.approx(1.0)
    assert effective_trials_eigen(np.eye(5)) == pytest.approx(5.0)
    block = np.kron(np.eye(3), np.ones((2, 2)))  # three pairs of identical trials
    assert effective_trials_eigen(block) == pytest.approx(3.0)
    assert effective_trials_cluster(block, 0.7) == 3


def test_trial_correlation_from_returns():
    rng = np.random.default_rng(0)
    a = rng.normal(size=500)
    r = np.column_stack([a, a + 0.01 * rng.normal(size=500), rng.normal(size=500)])
    c = trial_correlation(r)
    assert c[0, 1] > 0.99 and abs(c[0, 2]) < 0.15
    assert effective_trials_cluster(c) == 2


def test_trial_count_is_floored_at_the_search_or_prereg_variants():
    assert deflation_trial_count(3, None, 24) == 24
    assert deflation_trial_count(40, 7.2, 1) == 8
    th = DEFAULT_THRESHOLDS
    assert trial_count({"n_trials": 1}, th) == th.trial_floor == 24
    assert trial_count({"n_trials": 1, "preregistered_variants": 1}, th) == 1
    assert trial_count({"n_trials": 30, "n_trials_effective": 12.0, "preregistered_variants": 1}, th) == 12


def test_sharpe_variance_is_per_period():
    v = per_period_sharpe_variance([0.5, 1.0, 1.5], 252)
    assert v == pytest.approx(np.var(np.array([0.5, 1.0, 1.5]) / math.sqrt(252), ddof=1))
    assert math.isnan(per_period_sharpe_variance([1.0], 252))


# ---------------------------------------------------------------- D3 a-priori hurdle
def test_hurdle_is_a_priori():
    assert hurdle_for(1, DEFAULT_THRESHOLDS) == SINGLE_TEST_HURDLE == 1.645
    assert hurdle_for(24, DEFAULT_THRESHOLDS) == MINED_HURDLE == 3.0
    assert DEFAULT_THRESHOLDS.ic_t_stat_min == MINED_HURDLE


# ---------------------------------------------------------------- D4 INCONCLUSIVE
def test_three_verdicts():
    assert {v.value for v in ResearchVerdict} == {"PROMOTE", "REJECT", "INCONCLUSIVE"}


def test_sharpe_interval_lo_2002():
    lo, hi = sharpe_interval(0.2, 252 * 19)
    se = math.sqrt((1 + 0.5 * 0.2**2 / 252) / 19)  # daily form (Q1): the squared term is the daily Sharpe
    assert lo == pytest.approx(0.2 - 1.96 * se) and hi == pytest.approx(0.2 + 1.96 * se)
    assert lo < 0 < 0.5 < hi


def _inputs(is_t: float, full_sharpe: float, n_days: int):
    from ceap.domain.research import (
        BacktestPeriodStats,
        BacktestResult,
        PeriodSignalStats,
        PortfolioRiskReport,
        SignalStatistics,
    )

    def sp(name: str, t: float) -> PeriodSignalStats:
        return PeriodSignalStats(name, "2010-01-04", "2026-09-18", 200, 0.01, 0.2, t, 0.05, 0.5, 0.3, 0.0, 0.0, {})

    def bp(name: str, sharpe: float, days: int) -> BacktestPeriodStats:
        return BacktestPeriodStats(name, "2010-01-04", "2026-09-18", days, 0.0, 0.0, 0.1, sharpe, sharpe, 0.1, 3.0,
                                   0.5, sharpe + 0.1, 0.02, 50.0, 100)

    stats = SignalStatistics("momentum_12_1", "R08", 21, 34, {
        "in_sample": sp("in_sample", is_t), "out_of_sample": sp("out_of_sample", 0.5), "full": sp("full", is_t)})
    bt = BacktestResult("momentum_12_1", "R08", 21, True, 5e7, {
        "in_sample": bp("in_sample", full_sharpe, n_days - 1260), "out_of_sample": bp("out_of_sample", full_sharpe, 1260),
        "full": bp("full", full_sharpe, n_days)}, 1.0, (), (), 0.2)
    risk = PortfolioRiskReport("momentum_12_1", "R08", "2026-09-18", True, 5e7, (), 1.0, 0.0, 0.05, 0.05, 20.0, 0.1,
                               0.03, ())
    return stats, bt, risk


def test_inconclusive_replaces_a_statistical_reject():
    from ceap.analytics.research_assessment import assess_research
    from ceap.domain.research import ResearchFlag

    a = assess_research(*_inputs(is_t=1.0, full_sharpe=0.2, n_days=252 * 19))
    assert a.verdict is ResearchVerdict.INCONCLUSIVE and ResearchFlag.NO_ALPHA in a.flags
    assert a.metrics["full_sharpe_ci_low"] < 0 < 0.5 < a.metrics["full_sharpe_ci_high"]
    assert assess_research(*_inputs(is_t=1.0, full_sharpe=-0.6, n_days=252 * 19)).verdict is ResearchVerdict.REJECT
    # a preregistered single test uses the 1.645 hurdle: t = 2.0 clears it, but not the mined 3.0
    stats, bt, risk = _inputs(is_t=2.0, full_sharpe=0.2, n_days=252 * 19)
    assert assess_research(stats, bt, risk).metrics["hurdle_t"] == 3.0
    bt1 = replace(bt, trial={"n_trials": 1, "preregistered_variants": 1})
    assert assess_research(stats, bt1, risk).metrics["hurdle_t"] == 1.645


# ---------------------------------------------------------------- D9 overfitting diagnostics
def test_pbo_noise_is_about_half_and_skill_is_low():
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.01, (1600, 10))
    r = pbo_cscv(noise, n_blocks=8)
    assert r["n_splits"] == 70 and 0.2 <= r["pbo"] <= 0.8
    skill = noise.copy()
    skill[:, 0] += 0.004
    assert pbo_cscv(skill, n_blocks=8)["pbo"] < 0.1
    with pytest.raises(ValueError):
        pbo_cscv(noise, n_blocks=7)


def test_stationary_bootstrap():
    rng = np.random.default_rng(2)
    idx = stationary_bootstrap_indices(100, 10.0, rng)
    assert idx.shape == (100,) and idx.min() >= 0 and idx.max() < 100
    x = rng.normal(0.001, 0.01, 2000)
    ci = sharpe_ci(x, n_boot=200)
    assert ci["low"] < ci["estimate"] < ci["high"]
    m = mean_ci(rng.normal(0.02, 0.1, 300), n_boot=200)
    assert m["low"] < 0.02 < m["high"] + 0.02


def test_paired_difference_and_diebold_mariano():
    rng = np.random.default_rng(3)
    base = rng.normal(0, 0.01, 2000)
    better = base + 0.002
    d = paired_sharpe_difference(better, base, n_boot=300)
    assert d["difference"] > 0 and d["p_value"] < 0.05
    dm = diebold_mariano(-better, -base)
    assert dm["statistic"] < -3


# ---------------------------------------------------------------- D11 Newey-West
def test_newey_west_masks_gaps():
    x = np.array([1.0, 2.0, np.nan, 3.0, 4.0, np.nan, 5.0, 2.0])
    v = x[np.isfinite(x)]
    # lag 0 equals the plain t on the finite values (population variance)
    assert newey_west_t(x, 0) == pytest.approx(v.mean() / math.sqrt(v.var() / v.size))
    # lag 1 pairs only adjacent observed days: (1,2), (3,4), (5,2)
    d = np.where(np.isfinite(x), x - v.mean(), 0.0)
    lrv = (d @ d) / v.size + 2 * 0.5 * (d[1:] @ d[:-1]) / v.size
    assert newey_west_t(x, 1) == pytest.approx(v.mean() / math.sqrt(lrv / v.size))


def test_andrews_bandwidth_grows_with_persistence():
    rng = np.random.default_rng(4)
    e = rng.normal(size=3000)
    ar = np.empty_like(e)
    ar[0] = e[0]
    for t in range(1, e.size):
        ar[t] = 0.9 * ar[t - 1] + e[t]
    assert andrews_bandwidth(ar) > 5 * max(andrews_bandwidth(e), 1.0)
    assert math.isfinite(newey_west_t(ar + 0.1, None))


# ---------------------------------------------------------------- D7 leakage canary
def test_leakage_canary_collapses_exactly_at_the_lag():
    from ceap.analytics.leakage import leakage_canary
    from ceap.data.historical import HistoricalGenerator
    from ceap.data.research_scenarios import research_scenario_for

    ds = HistoricalGenerator(n_days=900, warmup_days=100, in_sample_days=500).generate(research_scenario_for("R02"))
    rows = leakage_canary(ds, delays=(0, 2, 3, 4, 5), horizon=5, execution_lag_days=1)
    for r in rows:
        if r["overlap"] > 0:
            assert r["gross_sharpe"] > 2.0, rows
        else:
            assert abs(r["gross_sharpe"]) < 1.5, rows


# ---------------------------------------------------------------- D2 generator families, D1 calibration
def test_adversarial_families_leave_existing_datasets_unchanged():
    from ceap.data.historical import HistoricalGenerator
    from ceap.data.research_scenarios import research_scenario_for

    g = HistoricalGenerator(n_days=600, warmup_days=100, in_sample_days=300)
    base = research_scenario_for("R02")
    a = g.generate(base)
    b = g.generate(replace(base, calibration="realistic"))
    assert a.dataset_hash == b.dataset_hash and np.array_equal(a.close, b.close, equal_nan=True)
    for kw in ({"hidden_factor_bps": 600.0}, {"jump_intensity": 4.0}, {"market_corr_premium_bps": 600.0},
               {"regime_switch": True, "premium_bps": 1100.0}):
        c = g.generate(replace(base, **kw))
        assert c.dataset_hash != a.dataset_hash
        assert not np.array_equal(c.close, a.close, equal_nan=True)


def test_realistic_templates_match_published_moments():
    from ceap.data.ground_truth import derive_ground_truth
    from ceap.data.research_scenarios import RESEARCH_TEMPLATES

    real = {s.id: s for s in RESEARCH_TEMPLATES if s.calibration == "realistic"}
    assert set(real) == {"R02", "R08", "R09"}
    m08 = derive_ground_truth(real["R08"]).metrics
    assert 0.02 <= m08["rank_ic_is"] <= 0.03 and 0.3 <= m08["gross_sharpe_is"] <= 0.5
    assert derive_ground_truth(real["R08"]).verdict is ResearchVerdict.INCONCLUSIVE
    m09 = derive_ground_truth(real["R09"]).metrics
    assert 0.02 <= m09["rank_ic_is"] <= 0.03
    for s in RESEARCH_TEMPLATES:
        if s.calibration == "stylised" and s.id != "R02":
            assert s.description.startswith("Stylised"), s.id


# ---------------------------------------------------------------- D15 alpha decay
def test_half_life_fit_recovers_a_geometric_curve():
    from ceap.analytics.alpha_decay import expected_term_structure, fit_half_life
    from ceap.data.research_scenarios import research_scenario_for

    assert fit_half_life([0.1 * 0.6**k for k in range(10)])["half_life_days"] == pytest.approx(math.log(0.5) / math.log(0.6))
    truth = expected_term_structure(research_scenario_for("R03"), "reversal_5", horizons=10)
    assert fit_half_life(truth)["half_life_days"] == pytest.approx(1.357, abs=0.05)  # transient rho = 0.6
    drift = expected_term_structure(research_scenario_for("R01"), "momentum_12_1", horizons=30)
    assert fit_half_life(drift)["half_life_days"] == pytest.approx(252.0, rel=0.05)  # latent half-life


# ---------------------------------------------------------------- D13, D16-D19
def test_ledoit_wolf_matches_scikit_learn():
    from sklearn.covariance import LedoitWolf

    from ceap.analytics.risk_research import ledoit_wolf

    x = np.random.default_rng(5).normal(size=(60, 20))
    cov, delta = ledoit_wolf(x)
    ref = LedoitWolf().fit(x)
    assert delta == pytest.approx(ref.shrinkage_, rel=1e-6)
    assert np.allclose(cov, ref.covariance_)


def test_factor_regression_recovers_alpha_and_loadings():
    from ceap.analytics.risk_research import factor_regression, multi_factor_stress, residualise

    rng = np.random.default_rng(6)
    f = rng.normal(0, 0.04, (240, 2))
    y = 0.005 + 0.8 * f[:, 0] - 0.3 * f[:, 1] + rng.normal(0, 0.01, 240)
    r = factor_regression(y, f, ("mkt_rf", "umd"))
    assert r["alpha_per_period"] == pytest.approx(0.005, abs=0.002) and r["alpha_t"] > 3
    assert r["loadings"]["mkt_rf"] == pytest.approx(0.8, abs=0.05) and r["r2"] > 0.9
    assert multi_factor_stress({"mkt_rf": 1.0, "umd": -0.5}, {"s": {"mkt_rf": -0.1, "umd": 0.2}})["s"] == pytest.approx(-0.2)
    expo = rng.normal(size=(30, 1))
    res = residualise(2.0 * expo[:, 0] + 1.0, expo)
    assert np.allclose(res, 0.0, atol=1e-9)


def test_tails_regimes_construction_and_frontier():
    from ceap.analytics.risk_research import (
        cost_frontier,
        regime_split,
        tail_report,
        turnover_penalised_weights,
    )

    rng = np.random.default_rng(7)
    r = rng.normal(0.0005, 0.01, 1000)
    rep = tail_report(r)
    assert rep["max_drawdown"] < 0 and rep["cvar"] >= rep["var"] > 0 and len(rep["worst_21d"]) == 5
    reg = regime_split(r, rng.normal(0, 0.01, 1000))
    assert reg["calm_days"] > reg["stressed_days"] > 0
    cov = np.diag([0.04, 0.09])
    w_prev = np.array([0.5, 0.5])
    mv = turnover_penalised_weights(np.array([0.02, 0.01]), cov, w_prev, gamma=1.0, tau=0.0)
    assert np.allclose(mv, [0.5, 1 / 9])
    sticky = turnover_penalised_weights(np.array([0.02, 0.01]), cov, w_prev, gamma=1.0, tau=1e6)
    assert np.allclose(sticky, w_prev, atol=1e-4)
    pts = [{"turnover": 1.0, "net_sharpe": 0.3}, {"turnover": 2.0, "net_sharpe": 0.2}, {"turnover": 3.0, "net_sharpe": 0.5}]
    assert [p["turnover"] for p in cost_frontier(pts)] == [1.0, 3.0]


# ---------------------------------------------------------------- D22 model inventory
def test_model_inventory_and_card_fields():
    from ceap.ml.inventory import MODEL_INVENTORY
    from ceap.ml.registry import ModelCard

    names = {e.name for e in MODEL_INVENTORY}
    assert {"pretrade_cost", "ridge_combination", "llm:narrative", "research_assessment"} <= names
    assert all(e.challenger and e.owner and e.validator for e in MODEL_INVENTORY)
    card = ModelCard("m", "k", "h", "d", "g", "fitted to synthetic generator g", "2026-11-09", {})
    assert card.status == "research" and "challenger" not in card.to_markdown()
    assert "Inventory." in card.to_markdown()


# ---------------------------------------------------------------- D14 data vintage
def test_vintage_diff_names_the_changed_year(tmp_path):
    from datetime import date

    from ceap.data.real.prices import DailySeries
    from ceap.data.real.vintage import record_vintages

    days = [date(2020, 12, 30), date(2020, 12, 31), date(2021, 1, 4), date(2021, 1, 5)]
    a = DailySeries("AAA", "yahoo", days, [10.0, 10.5, 11.0, 11.2], [1, 1, 1, 1])
    m = tmp_path / "vintage.json"
    assert record_vintages(m, [a]) == []
    assert record_vintages(m, [a])[0]["identical"]
    b = DailySeries("AAA", "yahoo", days, [10.0, 10.5, 11.0, 11.3], [1, 1, 1, 1])  # 2021 re-adjusted
    d = record_vintages(m, [b])[0]
    assert not d["identical"] and d["changed_years"] == ["2021"]
