"""2.7 statistics, 2.8 trial ledger and multiple testing, 2.10 null-sweep machinery."""

from __future__ import annotations

import json
import math
import os

import numpy as np
import pytest

from ceap.analytics.null_sweep import choose_hurdle, clopper_pearson, false_promote_curve
from ceap.analytics.research_assessment import (
    DEFAULT_THRESHOLDS,
    THRESHOLDS_PATH,
    assess_research,
    deflated_sharpe,
    expected_max_sharpe,
)
from ceap.analytics.signal_statistics import (
    evaluate_signal,
    forward_returns,
    newey_west_t,
    rowwise_rank_corr,
    spearman,
)
from ceap.analytics.trial_ledger import TrialLedger
from ceap.data.historical import HistoricalGenerator, HistoricalStore
from ceap.data.research_scenarios import noise_scenario
from ceap.domain.research import ResearchFlag, search_space, validate_choice

NIGHTLY = os.environ.get("CEAP_NIGHTLY") == "1"


# ------------------------------------------------------------------ 2.7
def test_vectorised_rank_ic_matches_spearman():
    rng = np.random.default_rng(3)
    a = rng.normal(size=(30, 25))
    b = a * 0.3 + rng.normal(size=(30, 25))
    a[3, :4] = np.nan
    fast = rowwise_rank_corr(a, b)
    slow = np.array([spearman(a[t], b[t]) for t in range(30)])
    assert fast == pytest.approx(slow, abs=1e-12)


def test_forward_returns_respect_the_execution_lag():
    close = np.arange(1.0, 11.0)[:, None]
    f = forward_returns(close, horizon=2, lag=1)
    assert f[0, 0] == pytest.approx(4.0 / 2.0 - 1.0)  # close[3] / close[1]
    assert np.isnan(f[-3:, 0]).all() and np.isfinite(f[:-3, 0]).all()


def test_newey_west_known_answer():
    x = np.array([1.0, 2.0, 3.0, 4.0])
    # mean 2.5, d = [-1.5,-.5,.5,1.5]; g0 = 5/4; g1 = (.75-.25+.75)/4 = 0.3125; lrv = 1.25 + 2*0.5*0.3125
    lrv = 1.25 + 0.3125
    assert newey_west_t(x, 1) == pytest.approx(2.5 / math.sqrt(lrv / 4))
    assert newey_west_t(x, 0) == pytest.approx(2.5 / math.sqrt(1.25 / 4))


def test_gate_uses_non_overlapping_horizon_ic_and_trims_the_in_sample_tail():
    ds = HistoricalStore().get("R01")
    stats = evaluate_signal(ds, "momentum_12_1", rebalance_days=21)
    ins, oos = stats.period("in_sample"), stats.period("out_of_sample")
    assert stats.horizon_days == 21 and ins.horizon_days == 21 and ins.execution_lag_days == 1
    expected_n = (ds.in_sample_end_index - 22 - ds.start_index) // 21 + 1
    assert abs(ins.n_dates - expected_n) <= 1
    # the last in-sample decision date's forward window ends on or before the locked split
    last = ds.index_at_or_before(ins.end)
    assert last + 1 + 21 <= ds.in_sample_end_index
    assert np.isfinite(ins.ic_t_stat_nw) and ins.ic_ir_annualised == pytest.approx(ins.ic_ir * math.sqrt(12))
    assert 0.0 <= ins.rank_autocorrelation_turnover <= 2.0 and ins.turnover == ins.rank_autocorrelation_turnover
    assert oos.n_dates > 0


def _null_t(seed: int) -> float:
    ds = HistoricalGenerator().generate(noise_scenario(seed))
    return evaluate_signal(ds, "momentum_12_1", rebalance_days=21).period("in_sample").ic_t_stat


@pytest.mark.slow  # 50 seeded datasets; ~45 s idle, near the 120 s default under parallel load
def test_null_t_rate_pr_50_seeds():
    ts = np.array([_null_t(s) for s in range(1000, 1050)])
    rate = float((np.abs(ts) > 2.0).mean())
    assert 0.0 <= rate <= 0.14, rate


@pytest.mark.nightly
@pytest.mark.skipif(not NIGHTLY, reason="nightly: 500 seeds (set CEAP_NIGHTLY=1)")
def test_null_t_rate_nightly_500_seeds():
    ts = np.array([_null_t(s) for s in range(1000, 1500)])
    rate = float((np.abs(ts) > 2.0).mean())
    assert 0.030 <= rate <= 0.075, rate


# ------------------------------------------------------------------ 2.8
def test_ledger_counts_distinct_trials_and_ignores_reruns(tmp_path):
    path = tmp_path / "ledger.jsonl"
    led = TrialLedger(path)
    a = led.record("ds1", {"signal": "momentum_12_1", "rebalance_days": 21}, 0.02)
    again = led.record("ds1", {"rebalance_days": 21, "signal": "momentum_12_1"}, 0.02)
    b = led.record("ds1", {"signal": "reversal_5", "rebalance_days": 5}, -0.01)
    other = led.record("ds2", {"signal": "reversal_5", "rebalance_days": 5}, 0.0)
    assert (a.n_trials, again.n_trials, b.n_trials, other.n_trials) == (1, 1, 2, 1)
    assert a.new_trial and not again.new_trial
    assert b.sharpe_variance == pytest.approx(np.var([0.02, -0.01], ddof=1))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3  # append-only, the re-run wrote nothing
    assert TrialLedger(path).summary("ds1").n_trials == 2  # survives a restart


def test_deflated_sharpe_known_answers():
    assert expected_max_sharpe(1, 0.01) == 0.0
    sr0 = expected_max_sharpe(24, 1.0 / 3528)
    assert sr0 * math.sqrt(252) == pytest.approx(0.529, abs=0.01)
    dsr, bar = deflated_sharpe(1.5, 3528, 0.0, 3.0, 1, None)
    assert bar == 0.0 and dsr == pytest.approx(1.0, abs=1e-6)
    low, bar24 = deflated_sharpe(0.5, 3528, 0.0, 3.0, 24, None)
    assert bar24 == pytest.approx(sr0 * math.sqrt(252)) and low < 0.5


async def test_caller_cannot_lower_n_trials_and_distinct_specs_raise_the_bar():
    from ceap.mcp.backtest.server import build_server

    srv = build_server(HistoricalStore(), TrialLedger())
    tool = srv.get("run_backtest").fn
    with pytest.raises(TypeError):

        await tool(signal="momentum_12_1", dataset="R01", n_trials=1)
    first = await tool(signal="momentum_12_1", dataset="R01", capacity=False)
    rerun = await tool(signal="momentum_12_1", dataset="R01", capacity=False)
    second = await tool(signal="momentum_12_1", dataset="R01", rebalance_days=5, capacity=False)
    assert first["trial"]["n_trials"] == rerun["trial"]["n_trials"] == 1
    assert second["trial"]["n_trials"] == 2
    from ceap.domain.research import backtest_result_from_dict

    b1, b2 = backtest_result_from_dict(first), backtest_result_from_dict(second)
    i1, i2 = b1.period("in_sample"), b2.period("in_sample")
    _, bar1 = deflated_sharpe(i1.sharpe, i1.n_days, i1.skew, i1.kurtosis, 1, None)
    _, bar2 = deflated_sharpe(i2.sharpe, i2.n_days, i2.skew, i2.kurtosis, 2, b2.trial["sharpe_variance"])
    assert bar1 == 0.0 and bar2 > 0.0


def test_search_space_is_enumerated():
    assert len(search_space()) == 24
    validate_choice("reversal_5", 5, True, "rank")
    for bad in (("momentum_12_1", 10, True, "equal"), ("alpha_x", 21, True, "equal"), ("reversal_5", 5, True, "vol")):
        with pytest.raises(ValueError):
            validate_choice(*bad)


def test_in_sample_end_is_locked():
    ds = HistoricalStore().get("R01")
    later = str(ds.dates[ds.in_sample_end_index + 200])
    assert ds.locked_split(later, ds.start_index, ds.n_days - 1) == ds.in_sample_end_index
    earlier = str(ds.dates[ds.in_sample_end_index - 100])
    assert ds.locked_split(earlier, ds.start_index, ds.n_days - 1) == ds.in_sample_end_index - 100


def test_flags_renamed_and_regime_break_exists():
    assert ResearchFlag.OVERFIT is ResearchFlag.OOS_DECAY and ResearchFlag.OOS_DECAY.value == "OOS_DECAY"
    assert ResearchFlag.REGIME_BREAK.value == "REGIME_BREAK"
    from ceap.domain.research import research_assessment_from_dict

    old = research_assessment_from_dict({"verdict": "REJECT", "flags": ["OVERFIT"]})
    assert old.flags == (ResearchFlag.OOS_DECAY,)


def test_assessment_carries_a_hypothesis_table():
    from ceap.analytics.backtest import run_backtest
    from ceap.analytics.portfolio_risk import target_portfolio, with_checks

    ds = HistoricalStore().get("R02")
    a = assess_research(
        evaluate_signal(ds, "momentum_12_1"), run_backtest(ds, "momentum_12_1"),
        with_checks(target_portfolio(ds, "momentum_12_1")),
    )
    assert a.hypotheses is not None and a.hypotheses.trials_counted == DEFAULT_THRESHOLDS.trial_floor  # D8
    assert [r.name for r in a.hypotheses.rows] == [
        # Q4 (R-20): the factor-neutral IC is a gate condition, so it has its own row
        "in_sample_ic_t", "in_sample_ic_t_factor_neutral", "deflated_sharpe", "out_of_sample_ic_t",
        "out_of_sample_sharpe_t"]
    assert a.metrics["hurdle_t"] == DEFAULT_THRESHOLDS.ic_t_stat_min
    assert "hypotheses" in a.to_dict()


# ------------------------------------------------------------------ 2.10
def test_clopper_pearson_known_answers():
    lo, hi = clopper_pearson(0, 50)
    assert lo == 0.0 and hi == pytest.approx(1 - 0.025 ** (1 / 50), abs=1e-6)
    lo, hi = clopper_pearson(10, 500)
    assert lo == pytest.approx(0.00963, abs=2e-4) and hi == pytest.approx(0.03649, abs=2e-4)


@pytest.mark.slow  # seeded grid; ~35 s idle, near the 120 s default under parallel load
def test_hurdle_is_the_lowest_grid_t_with_upper_bound_at_most_5pct():
    stats = list(np.linspace(-1, 4.0, 500))
    row = choose_hurdle(stats)
    curve = false_promote_curve(stats)
    prev = [r for r in curve if r["hurdle"] < row["hurdle"]]
    assert row["upper"] <= 0.05 and all(r["upper"] > 0.05 for r in prev)


def test_committed_thresholds_record_the_sweep():
    doc = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    assert doc["ic_t_stat_min"] == DEFAULT_THRESHOLDS.ic_t_stat_min
    assert doc["sweep_hash"] == DEFAULT_THRESHOLDS.hurdle_sweep_hash and len(doc["sweep_hash"]) == 64
    assert doc["n_seeds"] == 500 and doc["band95"][1] <= 0.05
    # D3: the hurdle is a priori; the sweep measured 0 false PROMOTEs at t >= 3 over 500 seeds
    assert doc["ic_t_single_test"] == DEFAULT_THRESHOLDS.ic_t_single_test == 1.645
    assert doc["false_promotes"] == 0 and doc["quick_check_50"]["n_seeds"] == 50
    per_seed = json.loads((THRESHOLDS_PATH.parents[2] / "docs" / "research" / "null_sweep_per_seed.json").read_text())
    assert per_seed["sweep_hash"] == doc["sweep_hash"] and len(per_seed["per_seed"]) == 500


def test_wheel_fallbacks_match_the_committed_files(monkeypatch, tmp_path):
    import ceap.analytics.research_assessment as ra
    import ceap.data.calendar as cal

    assert (ra.COMMITTED_HURDLE, ra.COMMITTED_SWEEP_HASH) == (DEFAULT_THRESHOLDS.ic_t_stat_min,
                                                            DEFAULT_THRESHOLDS.hurdle_sweep_hash)
    monkeypatch.setattr(ra, "THRESHOLDS_PATH", tmp_path / "missing.json")
    assert ra._load_sweep_hash() == ra.COMMITTED_SWEEP_HASH
    on_disk = cal._load()
    cal._load.cache_clear()
    monkeypatch.setattr(cal, "CALENDAR_PATH", tmp_path / "missing.csv")
    assert cal._load() == on_disk  # rules regenerate the committed list
    cal._load.cache_clear()
    import numpy as np

    from ceap.analytics.backtest import risk_free_daily
    rf, source = risk_free_daily(np.array(["2020-01-02"], dtype="datetime64[D]"), tmp_path / "none.csv")
    assert rf.tolist() == [0.0] and source.startswith("unavailable")

