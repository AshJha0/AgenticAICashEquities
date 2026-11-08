"""2B.3: dynamic leakage tests for every signal and feature; a planted look-ahead must fail both."""

from __future__ import annotations

import numpy as np
import pytest

from ceap.analytics.leakage import check_feature, forward_return, perturbation_test, shuffled_label_test
from ceap.analytics.ml_signals import build_panel, zscore_rows
from ceap.analytics.signals import SIGNALS


@pytest.fixture(scope="module")
def prices(history):
    ds = history.get("R01")
    return ds.close, ds.volume


@pytest.mark.parametrize("name", sorted(SIGNALS))
def test_every_library_signal_passes_both(prices, name):
    fn = SIGNALS[name].fn
    pert, shuf = check_feature(fn, *prices, name=name)
    assert pert.passed, pert.detail
    assert shuf.passed, shuf.detail


@pytest.mark.parametrize("name", sorted(SIGNALS))
def test_zscored_combination_features_pass_both(prices, name):
    fn = SIGNALS[name].fn
    pert, shuf = check_feature(lambda c, v: zscore_rows(fn(c, v)), *prices, name=f"z({name})")
    assert pert.passed and shuf.passed, (pert.detail, shuf.detail)


def _leaky(close: np.ndarray, volume: np.ndarray | None = None) -> np.ndarray:
    """Momentum plus a planted peek at tomorrow's return (the classic one-row misalignment)."""
    mom = SIGNALS["momentum_12_1"].fn(close, volume)
    return np.nan_to_num(mom) + 10.0 * np.nan_to_num(forward_return(close, 1))


def test_planted_look_ahead_fails_both(prices):
    pert = perturbation_test(_leaky, *prices, name="leaky")
    shuf = shuffled_label_test(_leaky, *prices, name="leaky")
    assert not pert.passed and pert.first_bad_cut is not None
    assert not shuf.passed and abs(shuf.statistic) > 4.0


def test_full_sample_normalisation_fails_perturbation(prices):
    def whole_sample(c, v):
        m = SIGNALS["reversal_5"].fn(c, v)
        return (m - np.nanmean(m)) / np.nanstd(m)

    assert not perturbation_test(whole_sample, *prices).passed


def test_ridge_panel_features_do_not_read_the_future(history):
    """Perturbing prices after t leaves every panel feature row <= t bit-identical."""
    ds = history.get("R01")
    base = build_panel(ds).X
    t = ds.in_sample_end_index
    rng = np.random.default_rng(7)
    close = ds.close.copy()
    close[t + 1 :] *= np.exp(rng.normal(0, 0.05, close[t + 1 :].shape))
    from dataclasses import replace

    pert = build_panel(replace(ds, close=close)).X
    assert np.array_equal(base[: t + 1], pert[: t + 1], equal_nan=True)
