"""2B.4: held-out confidence calibration."""

from __future__ import annotations

import numpy as np
import pytest

from ceap.analytics.calibration import (
    CHANGE_POINT_KEY,
    apply_calibration,
    brier,
    calibrate,
    calibration_info,
    ece,
    fit_isotonic,
    reliability_bins,
)


def test_brier_and_ece_hand_values():
    p = [0.9, 0.9, 0.2, 0.6]
    y = [1, 0, 0, 1]
    assert brier(p, y) == pytest.approx((0.01 + 0.81 + 0.04 + 0.16) / 4)
    # bins: [0.2]->y0, [0.6]->y1, [0.9,0.9]->y mean 0.5
    assert ece(p, y) == pytest.approx(0.25 * 0.2 + 0.25 * 0.4 + 0.5 * 0.4)
    assert [b["n"] for b in reliability_bins(p, y)] == [1, 1, 2]
    assert reliability_bins([1.0], [1])[0]["hi"] == 1.0  # p = 1 falls in the last bin


def test_isotonic_map_reproduces_sklearn_predict():
    from sklearn.isotonic import IsotonicRegression

    rng = np.random.default_rng(0)
    p = rng.uniform(0.5, 0.95, 300)
    y = (rng.uniform(size=300) < (p - 0.4)).astype(float)
    m = fit_isotonic(p, y)
    ref = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip").fit(p, y)
    q = np.linspace(0.3, 1.0, 50)
    np.testing.assert_allclose(m(q), ref.predict(q), atol=1e-12)
    assert np.all(np.diff(m(q)) >= 0)


def test_overconfident_raw_is_improved_and_shipped():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.6, 0.95, 4000)
    y = (rng.uniform(size=4000) < 0.5).astype(float)  # truth: a coin flip, raw says 0.6 to 0.95
    rep = calibrate("k", p[:2000], y[:2000], p[2000:], y[2000:])
    assert rep.shipped and rep.brier_calibrated < rep.brier_raw and rep.ece_calibrated < rep.ece_raw


def test_not_shipped_when_calibration_hurts_held_out():
    p_fit, y_fit = [0.6, 0.7, 0.8, 0.9], [0, 0, 0, 0]  # fit says "never right"
    p_eval, y_eval = [0.6, 0.7, 0.8, 0.9], [1, 1, 1, 1]  # held out says "always right"
    rep = calibrate("k", p_fit, y_fit, p_eval, y_eval)
    assert not rep.shipped and rep.brier_calibrated > rep.brier_raw


def test_unknown_key_passes_through():
    assert apply_calibration("no.such.key", 0.73) == 0.73
    assert calibration_info("no.such.key")["applied"] is False


def test_registered_change_point_calibration_is_held_out_and_labelled():
    from ceap.analytics.calibration import calibration_model_name
    from ceap.ml.registry import ModelRegistry

    m = ModelRegistry().load(calibration_model_name(CHANGE_POINT_KEY))
    met = m.card.metrics
    assert m.card.label.startswith("calibrated to synthetic generator ")
    assert met["n_eval"] > 0 and met["shipped"] == (met["brier_calibrated_heldout"] <= met["brier_raw_heldout"])
    info = calibration_info(CHANGE_POINT_KEY)
    assert info["applied"] == met["shipped"] and info["model_hash"] == m.model_hash
    if met["shipped"]:
        assert 0.0 <= apply_calibration(CHANGE_POINT_KEY, 0.9) <= 1.0
