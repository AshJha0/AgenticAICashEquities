"""0.10.0 item X4 (finding R-24): generator law and regime options, and the pre-trade card's tests."""

from __future__ import annotations

import math

import pytest

from ceap.data.synthetic import ADV_SHARES, IMPACT_GAMMA, SIGMA_DAILY_BPS, expected_impact_drift_bps
from ceap.data.tca_universe import (
    LINEAR_LAW_CROSSOVER,
    UniverseConfig,
    generate_tca_universe,
    impact_drift_bps,
)


def test_linear_impact_law_is_a_different_functional_form():
    """Same at the 1 % crossover, linear (not concave) in size, and the default stays square-root."""
    at = LINEAR_LAW_CROSSOVER * ADV_SHARES
    assert impact_drift_bps(at, SIGMA_DAILY_BPS, "linear") == pytest.approx(
        expected_impact_drift_bps(at, sigma_bps=SIGMA_DAILY_BPS)
    )
    small, big = (
        impact_drift_bps(at / 4, SIGMA_DAILY_BPS, "linear"),
        impact_drift_bps(4 * at, SIGMA_DAILY_BPS, "linear"),
    )
    assert big / small == pytest.approx(16.0)  # sqrt law would give 4
    assert impact_drift_bps(4 * at, SIGMA_DAILY_BPS) == pytest.approx(
        IMPACT_GAMMA * SIGMA_DAILY_BPS * math.sqrt(0.04)
    )
    with pytest.raises(ValueError, match="impact law"):
        impact_drift_bps(at, SIGMA_DAILY_BPS, "cubic")


def test_universe_impact_law_and_regime_reach_the_truth():
    cfg = UniverseConfig(n_sessions=2, parents_per_session=6, multi_session_fraction=0.0)
    sq = generate_tca_universe(5, cfg)
    lin = generate_tca_universe(
        5,
        UniverseConfig(n_sessions=2, parents_per_session=6, multi_session_fraction=0.0, impact_law="linear"),
    )
    big = generate_tca_universe(
        5, UniverseConfig(n_sessions=2, parents_per_session=6, regime=(("order_size_multiplier", 2.0),))
    )
    for pid, t in sq.truth.items():
        q = t["q_over_adv"]
        ratio = lin.truth[pid]["impact_drift_bps"] / t["impact_drift_bps"]
        assert ratio == pytest.approx(math.sqrt(q / LINEAR_LAW_CROSSOVER), rel=1e-9)
    assert sum(t["q_over_adv"] for t in big.truth.values()) > 1.8 * sum(
        t["q_over_adv"] for t in sq.truth.values()
    )
    with pytest.raises(ValueError, match="regime axes"):
        generate_tca_universe(
            5, UniverseConfig(n_sessions=2, parents_per_session=2, regime=(("price_move_bps", 1.0),))
        )
    with pytest.raises(ValueError, match="impact law"):
        generate_tca_universe(5, UniverseConfig(n_sessions=2, parents_per_session=2, impact_law="log"))


def test_group_conformal_quantile_ranks_regime_quantiles():
    import numpy as np

    from ceap.ml.pretrade_cost import conformal_quantile, group_conformal_quantile

    rng = np.random.default_rng(0)
    groups = [rng.normal(0, 1 + 0.1 * k, 400).__abs__() for k in range(20)]
    qs = sorted(conformal_quantile(g, 0.9) for g in groups)
    assert group_conformal_quantile(groups, 0.9) == qs[18]  # ceil(21 * 0.9) = 19th of 20
    assert group_conformal_quantile(groups[:10], 0.9) == max(conformal_quantile(g, 0.9) for g in groups[:10])
    assert group_conformal_quantile([], 0.9) == float("inf")


def _script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "scripts" / "train_pretrade.py"
    spec = importlib.util.spec_from_file_location("train_pretrade_x4", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_design_effect_counts_session_clustering():
    import numpy as np

    mod = _script()
    clusters = np.repeat(np.arange(50), 20)
    clustered = np.repeat((np.arange(50) % 10 != 0).astype(float), 20)  # whole sessions hit or miss
    assert mod.design_effect(clustered, clusters) == pytest.approx(20.0, rel=0.05)
    iid = (np.random.default_rng(1).random(1000) < 0.9).astype(float)
    assert mod.design_effect(iid, clusters) < 1.6
    lo, hi = mod.wilson(900, 1000)
    lo_eff, hi_eff = mod.wilson(900 / 20, 1000 / 20)
    assert lo_eff < lo < 0.9 < hi < hi_eff


def test_committed_card_reports_challengers_regimes_and_law_shift():
    from ceap.ml.pretrade_cost import default_model

    m = default_model()
    meta = m.meta
    assert set(meta["shift_evaluation"]) == set(meta["test_families"])
    assert (
        set(meta["law_shift"]["cells"]) == set(meta["test_families"]) and meta["law_shift"]["law"] == "linear"
    )
    for cell in [*meta["shift_evaluation"].values(), *meta["law_shift"]["cells"].values()]:
        assert set(cell["mae"]) == set(cell["bias"]) == {"model", "spread_only", "constant"}
        assert (
            cell["design_effect"] >= 1.0
            and cell["coverage_ci95"][0] <= cell["coverage"] <= cell["coverage_ci95"][1]
        )
    # q is the regime-level rank, and the test families take no part in it
    assert m.conformal_q == pytest.approx(max(meta["regime_conformal_q"].values()), abs=1e-6)
    assert not set(meta["regime_conformal_q"]) & set(meta["test_families"])
    card = _script().CARD_PATH.read_text(encoding="utf-8")
    assert "## Functional-form shift: a linear impact law" in card and "two-layer" in card


def test_training_gate_flags_exactly_the_cells_whose_interval_excludes_nominal():
    from ceap.ml.pretrade_cost import default_model

    m = default_model()
    expected = [
        f
        for f, r in m.meta["shift_evaluation"].items()
        if not (r["coverage_ci95"][0] <= m.nominal <= r["coverage_ci95"][1])
        or not 0.85 <= m.meta["coverage_shifted"][f] <= 0.95
    ]
    assert _script().failures(m) == expected


@pytest.mark.xfail(
    strict=True,
    reason="R-24 open in T08 (order size x10, outside the calibration regimes): 87.1 %, interval [84.5 %, 89.3 %]",
)
def test_every_shifted_family_interval_contains_nominal():
    from ceap.ml.pretrade_cost import default_model

    assert _script().failures(default_model()) == []
