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
