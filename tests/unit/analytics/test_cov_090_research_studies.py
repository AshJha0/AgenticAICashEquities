"""Behaviour tests for the research-study drivers: adversarial and power curves, the null sweep
bookkeeping, the alpha-decay fit and the calibration helpers (0.9.0 coverage lane).

The study loops are exercised with the expensive per-seed simulation replaced by a stub that returns
known t statistics, so the counting and table-building can be checked by hand. One real simulation
per driver runs under the ``slow`` mark.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from ceap.analytics import adversarial, alpha_decay, calibration, null_sweep, power
from ceap.analytics.research_assessment import DEFAULT_THRESHOLDS, MINED_HURDLE, SINGLE_TEST_HURDLE

# ------------------------------------------------------------------ adversarial


@pytest.mark.parametrize(
    ("family", "field", "value"),
    [
        ("hidden_factor", "hidden_factor_bps", 600.0),
        ("regime_switch", "regime_switch", True),
        ("garch_jumps", "jump_intensity", 4.0),
        ("market_corr", "market_corr_premium_bps", 600.0),
    ],
)
def test_family_spec_plants_the_premium_where_the_family_says(family, field, value):
    spec = adversarial.family_spec(family, 600.0, 3)
    assert getattr(spec, field) == value
    assert spec.seed == 70_003
    assert spec.id == f"A-{family}-600-3"


def test_family_spec_drift_switches_mechanism_off_at_zero_premium():
    assert adversarial.family_spec("drift", 0.0, 0).mechanism == "none"
    on = adversarial.family_spec("drift", 900.0, 0)
    assert on.mechanism == "drift" and on.premium_bps == 900.0
    assert adversarial.family_spec("garch_jumps", 0.0, 0).mechanism == "none"


def test_family_spec_rejects_an_unknown_family():
    with pytest.raises(ValueError, match="unknown family"):
        adversarial.family_spec("nope", 0.0, 0)


def test_summarise_counts_detections_at_both_hurdles():
    oos = DEFAULT_THRESHOLDS.oos_ic_t_min
    # one clears both hurdles, one clears only the single-test hurdle, one fails out of sample
    ts = [(MINED_HURDLE + 0.1, oos + 1), (SINGLE_TEST_HURDLE + 0.01, oos + 1), (10.0, oos - 1)]
    row = adversarial.summarise("drift", 600.0, ts, oos)
    assert row["kind"] == "power"
    assert row["rate_single_test"] == round(2 / 3, 4)
    assert row["rate_mined"] == round(1 / 3, 4)
    assert row["mean_t_is"] == round(sum(a for a, _ in ts) / 3, 4)


def test_summarise_labels_false_positive_rows_and_handles_no_seeds():
    assert adversarial.summarise("drift", 0.0, [(0.0, 0.0)], 0.5)["kind"] == "false_positive_rate"
    assert adversarial.summarise("market_corr", 2400.0, [(0.0, 0.0)], 0.5)["kind"] == "false_positive_rate"
    empty = adversarial.summarise("drift", 600.0, [], 0.5)
    assert empty["seeds"] == 0 and empty["mean_t_is"] is None and math.isnan(empty["rate_mined"])


def test_adversarial_study_builds_one_row_per_family_and_premium(monkeypatch):
    calls = []

    def fake(job):
        calls.append(job)
        return (5.0, 5.0) if job[1] > 0 else (0.0, 0.0)

    monkeypatch.setattr(adversarial, "_one", fake)
    doc = adversarial.adversarial_study(seeds=2)
    assert len(calls) == 2 * len(adversarial.FAMILIES) * len(adversarial.PREMIUMS_BPS)
    assert len(doc["rows"]) == len(adversarial.FAMILIES) * len(adversarial.PREMIUMS_BPS)
    assert doc["hurdles"] == [SINGLE_TEST_HURDLE, MINED_HURDLE]
    by = {(r["family"], r["premium_bps"]): r for r in doc["rows"]}
    assert by[("drift", 0.0)]["rate_mined"] == 0.0
    assert by[("drift", 2400.0)]["rate_mined"] == 1.0
    assert by[("market_corr", 2400.0)]["kind"] == "false_positive_rate"


@pytest.mark.slow
def test_adversarial_one_runs_a_real_simulation():
    a, b = adversarial._one(("garch_jumps", 600.0, 0))
    assert math.isfinite(a) and math.isfinite(b)


# ------------------------------------------------------------------ power


def test_power_curve_counts_detections_and_predicts_monotone(monkeypatch):
    monkeypatch.setattr(power, "_one", lambda job: (9.0, 9.0) if job[0] >= 1200 else (0.0, 0.0))
    doc = power.power_curve(seeds=2)
    rows = doc["rows"]
    assert [r["premium_bps"] for r in rows] == list(power.PREMIUMS_BPS)
    for key in ("1.645", "3"):
        assert [r["detection_rate"][key] for r in rows] == [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        predicted = [r["predicted_detection"][key] for r in rows]
        assert predicted == sorted(predicted)  # more premium never predicts less power
        assert all(0.0 <= p <= 1.0 for p in predicted)
    assert rows[0]["mean_t_is"] == 0.0 and rows[-1]["mean_t_is"] == 9.0
    assert doc["hurdles"] == [DEFAULT_THRESHOLDS.ic_t_single_test, DEFAULT_THRESHOLDS.ic_t_stat_min]
    assert doc["detectable_premium_bps"]["3"]["premium_bps"] == 1200.0 and doc["seeds_per_point"] == 2


@pytest.mark.slow
def test_power_one_runs_a_real_simulation():
    a, b = power._one((0.0, 0))
    assert math.isfinite(a) and math.isfinite(b)


# ------------------------------------------------------------------ null sweep


def test_binomial_cdf_edges_and_a_hand_value():
    assert null_sweep.binomial_cdf(-1, 5, 0.3) == 0.0
    assert null_sweep.binomial_cdf(5, 5, 0.3) == 1.0
    assert null_sweep.binomial_cdf(1, 5, 0.0) == 1.0
    assert null_sweep.binomial_cdf(1, 5, 1.0) == 0.0
    # P(X <= 1), X ~ Bin(4, 0.5) = (1 + 4) / 16
    assert null_sweep.binomial_cdf(1, 4, 0.5) == pytest.approx(5 / 16)


def test_clopper_pearson_closed_forms():
    assert null_sweep.clopper_pearson(0, 0) == (0.0, 1.0)
    lo, hi = null_sweep.clopper_pearson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(1 - 0.025 ** (1 / 10), abs=1e-9)
    lo, hi = null_sweep.clopper_pearson(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.025 ** (1 / 10), abs=1e-9)
    lo, hi = null_sweep.clopper_pearson(5, 10)
    assert lo < 0.5 < hi and lo == pytest.approx(1 - hi, abs=1e-9)  # symmetric at k = n/2


def test_false_promote_curve_and_choose_hurdle():
    stats = [float("-inf")] * 98 + [1.0, 2.0]
    curve = null_sweep.false_promote_curve(stats)
    assert curve[0]["hurdle"] == 0.0 and curve[0]["false_promotes"] == 2
    at = {r["hurdle"]: r for r in curve}
    assert at[1.5]["false_promotes"] == 1 and at[2.05]["false_promotes"] == 0
    chosen = null_sweep.choose_hurdle(stats)
    assert chosen["upper"] <= 0.05
    assert chosen["hurdle"] == min(r["hurdle"] for r in curve if r["upper"] <= 0.05)
    with pytest.raises(ValueError, match="no hurdle"):
        null_sweep.choose_hurdle([10.0] * 5)
    assert null_sweep.false_promote_curve([])[0]["rate"] == 0.0


def _fake_sweep_seed(seed, thresholds=None):
    stat = 4.0 if seed == 1 else float("-inf")
    return null_sweep.SeedResult(seed, stat, {"momentum_12_1/21": 2.5 if seed == 1 else 0.3}, backtests=seed)


def test_run_null_sweep_summary_ledger_and_thresholds(monkeypatch, tmp_path):
    monkeypatch.setattr(null_sweep, "sweep_seed", _fake_sweep_seed)
    s = null_sweep.run_null_sweep(4)
    assert s["n_seeds"] == 4 and s["backtests_run"] == 0 + 1 + 2 + 3
    assert [p["stat"] for p in s["per_seed"]] == [None, 4.0, None, None]
    assert s["abs_t_gt_2_rate"] == 0.25
    assert s["abs_t_gt_2_by_family"] == {"momentum_12_1/21": 0.25}
    assert s["chosen"] is None  # four seeds cannot pin the rate under 5 %
    assert s["sweep_hash"] == null_sweep.run_null_sweep(4)["sweep_hash"]

    row = null_sweep.rate_at(s, 3.0)
    assert row["hurdle"] == 3.0 and row["false_promotes"] == 1
    q = null_sweep.quick_check(s, n=2)
    assert q == {"n_seeds": 2, "false_promotes": 1, "rate": 0.5}

    ledger = tmp_path / "sub" / "ledger.jsonl"
    null_sweep.append_sweep_ledger(s, "first", ledger)
    null_sweep.append_sweep_ledger(s, "second", ledger)
    recs = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [r["reason"] for r in recs] == ["first", "second"]  # append-only
    assert recs[0]["false_promotes"] == 1 and recs[0]["generator"] == null_sweep.GENERATOR_VERSION

    th, per = tmp_path / "th.json", tmp_path / "x" / "per.json"
    null_sweep.write_thresholds(s, th, per)
    doc = json.loads(th.read_text(encoding="utf-8"))
    assert doc["ic_t_stat_min"] == DEFAULT_THRESHOLDS.ic_t_stat_min
    assert doc["lowest_hurdle_with_upper_le_5pct"] is None
    assert doc["false_promotes"] == 1 and doc["n_seeds"] == 4
    assert all(r["hurdle"] * 4 == round(r["hurdle"] * 4) for r in doc["curve"])
    assert json.loads(per.read_text(encoding="utf-8"))["per_seed"][1] == {"seed": 1, "stat": 4.0}


def test_write_thresholds_reports_the_chosen_hurdle(monkeypatch, tmp_path):
    monkeypatch.setattr(null_sweep, "sweep_seed", lambda seed, th=None: null_sweep.SeedResult(seed, float("-inf")))
    s = null_sweep.run_null_sweep(80)
    assert s["chosen"]["hurdle"] == 0.0  # zero false promotes in 80: upper bound 4.5 % already at h = 0
    assert s["abs_t_gt_2_by_family"] == {}
    assert math.isnan(s["abs_t_gt_2_rate"])
    null_sweep.write_thresholds(s, tmp_path / "t.json", tmp_path / "p.json")
    assert json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))["lowest_hurdle_with_upper_le_5pct"] == 0.0


# ------------------------------------------------------------------ alpha decay


def test_fit_half_life_exact_geometric_and_degenerate_inputs():
    fit = alpha_decay.fit_half_life([0.08 * 0.5**h for h in range(6)])
    assert fit["phi"] == pytest.approx(0.5) and fit["half_life_days"] == pytest.approx(1.0)
    neg = alpha_decay.fit_half_life([-0.04, -0.02, -0.01, -0.005])
    assert neg["half_life_days"] == pytest.approx(1.0)
    for bad in ([0.1, 0.05], [0.0, 0.1, 0.1], [float("nan"), 0.1, 0.1], [0.1, -0.1, -0.1, 0.1]):
        assert math.isnan(alpha_decay.fit_half_life(bad)["phi"])
    growing = alpha_decay.fit_half_life([0.01, 0.02, 0.04])
    assert growing["half_life_days"] == float("inf")


def test_fit_half_life_ignores_the_noise_tail_after_the_floor():
    # a clean halving for 4 horizons, then values under a fifth of IC_1: the tail must not flatten phi
    ic = [0.1, 0.05, 0.025, 0.0125, 0.001, 0.009, 0.001, 0.008]
    assert alpha_decay.fit_half_life(ic)["phi"] == pytest.approx(0.5)


# ------------------------------------------------------------------ calibration helpers


def test_reliability_bins_and_ece_on_a_worked_example():
    p, y = [0.05, 0.15, 0.95, 1.0], [0, 0, 1, 0]
    bins = calibration.reliability_bins(p, y)
    assert [(b["lo"], b["n"]) for b in bins] == [(0.0, 1), (0.1, 1), (0.9, 2)]  # 1.0 lands in the last bin
    # ECE = 1/4*0.05 + 1/4*0.15 + 2/4*|0.975 - 0.5|
    assert calibration.ece(p, y) == pytest.approx(0.25 * 0.05 + 0.25 * 0.15 + 0.5 * 0.475)
    assert math.isnan(calibration.ece([], []))
    assert math.isnan(calibration.brier([], []))


def test_isotonic_map_vector_and_params_round_trip():
    m = calibration.IsotonicMap((0.0, 1.0), (0.2, 0.8))
    assert m(0.5) == pytest.approx(0.5)
    assert np.allclose(m(np.array([-1.0, 2.0])), [0.2, 0.8])  # clipped to the end knots
    assert m.to_params() == {"x": [0.0, 1.0], "y": [0.2, 0.8]}


def test_apply_calibration_unshipped_and_missing_models(monkeypatch):
    from ceap.ml import registry

    class Card:
        metrics = {"shipped": False}
        label = "lbl"

    class Model:
        card, model_hash, params = Card(), "h1", {"x": [0, 1], "y": [0, 1]}

    class Reg:
        def load(self, name):
            if name == "calibration_not_shipped":
                return Model()
            raise LookupError(name)

    monkeypatch.setattr(registry, "ModelRegistry", Reg)
    calibration._registered.cache_clear()
    try:
        assert calibration.calibration_model_name("a.b") == "calibration_a_b"
        info = calibration.calibration_info("not.shipped")
        assert info == {"key": "not.shipped", "applied": False, "model_hash": "h1", "label": "lbl"}
        assert calibration.apply_calibration("not.shipped", 0.3) == 0.3
        assert calibration.calibration_info("absent")["model_hash"] is None
    finally:
        calibration._registered.cache_clear()


def test_apply_calibration_passes_non_finite_raw_through(monkeypatch):
    monkeypatch.setattr(calibration, "_registered", lambda key: (calibration.IsotonicMap((0, 1), (0.5, 0.5)), "h", "l"))
    assert calibration.apply_calibration("k", 0.9) == 0.5
    assert math.isnan(calibration.apply_calibration("k", float("nan")))


# ------------------------------------------------------------------ dataset-backed pieces


def test_ic_term_structure_shape_and_truth_curve():
    from ceap.data.historical import HistoricalGenerator
    from ceap.data.research_scenarios import RESEARCH_TEMPLATES_RAW

    spec = RESEARCH_TEMPLATES_RAW[0]
    ds = HistoricalGenerator().generate(spec)
    out = alpha_decay.ic_term_structure(ds, "momentum_12_1", horizons=4, step=21)
    assert [c["h"] for c in out["curve"]] == [1, 2, 3, 4]
    assert all(c["n"] > 0 and -1.0 <= c["ic"] <= 1.0 for c in out["curve"])
    assert out["lag"] == 1 and out["step"] == 21 and {"phi", "half_life_days"} <= set(out)
    truth = alpha_decay.expected_term_structure(spec, "momentum_12_1", horizons=3)
    # a slow latent drift: the true marginal IC is positive and fades slowly with the horizon
    assert len(truth) == 3 and truth[0] > 0 and truth[0] >= truth[1] >= truth[2] > 0.9 * truth[0]


def test_change_point_samples_one_seed_pairs_confidence_with_outcome():
    p, y = calibration.change_point_samples([0])
    assert len(p) == len(y) <= len(calibration.SESSION_MULTIPLIERS)
    assert all(0.0 <= v <= 1.0 for v in p) and set(y) <= {0.0, 1.0}


@pytest.mark.slow  # one noise seed through the 24-configuration search: ~8 s
def test_sweep_seed_on_noise_records_every_family():
    r = null_sweep.sweep_seed(0)
    assert len(r.is_t) == len({(c["signal"], c["rebalance_days"]) for c in null_sweep.search_space()})
    assert r.stat == float("-inf") or r.stat >= 0.0
    assert r.backtests >= 0
