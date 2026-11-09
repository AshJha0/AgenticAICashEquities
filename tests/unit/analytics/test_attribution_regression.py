"""F8 regression attribution: a planted cause is recovered, and the flag keeps v0.7 output unchanged."""

from __future__ import annotations

import json

import numpy as np
import pytest

from ceap.analytics.attribution import PeerWindow, attribute_causes, regression_attribution

from .conftest import make_market, make_metrics


def _peers(n: int = 40, seed: int = 0) -> list[PeerWindow]:
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        spread = float(rng.uniform(1.0, 3.0))
        vol = float(rng.uniform(2.0, 5.0))
        # truth: 2 bps of residual per bps of spread; volatility has no effect
        resid = 2.0 * spread + float(rng.normal(0, 0.3))
        out.append(
            PeerWindow(
                make_metrics(is_residual_bps=resid, average_latency_us=float(rng.uniform(700, 900))),
                make_market(
                    average_spread_bps=spread,
                    realised_volatility_bps=vol,
                    average_displayed_depth=float(rng.uniform(4e3, 6e3)),
                ),
            )
        )
    return out


def test_planted_spread_effect_gets_the_share() -> None:
    peers = _peers()
    window = make_metrics(is_residual_bps=2.0 * 6.0)  # spread 6 bps: far wider than the peers
    market = make_market(average_spread_bps=6.0, realised_volatility_bps=3.5, average_displayed_depth=5e3)
    res = regression_attribution(window, market, peers, ridge=0.1, n_boot=300)
    assert res.target == "is_residual_bps" and res.n_obs == 40
    spread = res.shares["WIDE_SPREADS"]
    assert spread["bps"] == pytest.approx(res.excess_bps, rel=0.15)
    assert spread["ci_low_bps"] > 0
    assert abs(res.shares["MARKET_VOLATILITY"]["bps"]) < 1.0
    total = sum(s["bps"] for s in res.shares.values()) + res.unexplained_bps
    assert total == pytest.approx(res.excess_bps, rel=1e-9)


def test_too_few_observations_gives_no_shares() -> None:
    res = regression_attribution(make_metrics(is_residual_bps=1.0), make_market(), _peers(3))
    assert res.shares == {} and "too few" in res.note


def test_off_by_default_and_on_behind_the_flag(monkeypatch) -> None:
    peers = _peers(12)
    window, market = make_metrics(is_residual_bps=5.0), make_market(average_spread_bps=2.5)
    monkeypatch.delenv("CEAP_ATTRIBUTION", raising=False)
    plain = attribute_causes(window, None, market, None, peers=peers)
    assert "regression" not in plain.to_dict()
    flagged = attribute_causes(window, None, market, None, peers=peers, method="regression")
    assert flagged.to_dict()["regression"]["method"] == "ridge_regression"
    monkeypatch.setenv("CEAP_ATTRIBUTION", "regression")
    via_env = attribute_causes(window, None, market, None, peers=peers)
    assert via_env.regression == flagged.regression
    # the heuristic verdict is untouched by the flag
    a, b = plain.to_dict(), flagged.to_dict()
    b.pop("regression")
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(
        b, sort_keys=True, default=str
    )  # NaN-safe
