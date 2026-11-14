"""Q3 (review finding Q-14): one zero-variance tolerance, used by all five regression sites."""

from __future__ import annotations

import math

import numpy as np

from ceap.analytics.attribution import _ridge_contributions
from ceap.analytics.ml_signals import _moments
from ceap.analytics.numerics import negligible_variation
from ceap.analytics.portfolio_risk import raw_betas_ex_self
from ceap.analytics.profitability import factor_alpha
from ceap.analytics.risk_research import factor_regression

RNG = np.random.default_rng(14)


def _noisy_constant(level: float, n: int) -> np.ndarray:
    """A constant plus floating-point-sized noise: exact ``== 0`` tests miss this."""
    return level + RNG.normal(0.0, 1e-15, n)


def test_constant_y_gives_nan_r2_with_a_reason() -> None:
    f = RNG.normal(0.0, 0.01, (300, 1))
    out = factor_regression(_noisy_constant(0.001, 300), f, ("mkt",))
    assert math.isnan(out["r2"])
    assert "constant" in out["r2_note"]


def test_varying_y_keeps_r2_and_no_note() -> None:
    f = RNG.normal(0.0, 0.01, (300, 1))
    out = factor_regression(0.5 * f[:, 0] + RNG.normal(0.0, 0.01, 300), f, ("mkt",))
    assert 0.0 < out["r2"] < 1.0 and "r2_note" not in out


def test_near_constant_market_gives_a_flagged_beta() -> None:
    window = np.column_stack([RNG.normal(0.0, 0.01, 200), _noisy_constant(0.002, 200)])
    beta, se = raw_betas_ex_self(window)
    assert np.isinf(se[0]) and beta[0] == 1.0  # flagged: no estimate, shrinkage target
    out = factor_alpha(RNG.normal(0.0, 0.01, 200), _noisy_constant(0.002, 200))
    assert math.isnan(out["beta"]) and "constant" in out["note"]


def test_zero_mean_series_is_handled() -> None:
    z = RNG.normal(0.0, 0.01, 500)
    z -= z.mean()  # mean exactly about zero; a mean-relative tolerance would call this constant
    assert not negligible_variation(z)
    assert negligible_variation(np.zeros(50))  # the absolute floor covers the all-zero case
    out = factor_alpha(0.3 * z + RNG.normal(0.0, 0.01, 500), z)
    assert math.isfinite(out["beta"]) and "note" not in out


def test_ml_moments_and_ridge_use_the_tolerance() -> None:
    sr, skew, kurt = _moments(_noisy_constant(0.001, 100))
    assert math.isnan(sr) and skew == 0.0 and kurt == 3.0
    x = np.column_stack([RNG.normal(0.0, 1.0, 80), _noisy_constant(5.0, 80)])
    y = 2.0 * x[:, 0] + RNG.normal(0.0, 0.1, 80)
    beta, contrib, _ = _ridge_contributions(x, y, x[0], 1.0)
    assert np.all(np.isfinite(beta)) and abs(beta[1]) < 1e-9 and np.all(np.isfinite(contrib))
