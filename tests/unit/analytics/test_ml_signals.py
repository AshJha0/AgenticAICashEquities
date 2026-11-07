"""2B.2: ridge combination with purged, embargoed walk-forward CV and ledger-counted trials (D12)."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from ceap.analytics.ml_signals import (
    ALPHA_GRID,
    PROMOTE_DSR,
    SIGNAL_NAMES,
    build_panel,
    combine_signals,
    fit_ridge,
    label_valid_is_dates,
    purged_walk_forward,
    zscore_rows,
)
from ceap.analytics.null_sweep import clopper_pearson
from ceap.analytics.signals import dataset_signal, zscore_matrix
from ceap.analytics.trial_ledger import TrialLedger


@pytest.mark.parametrize(("h", "embargo", "folds"), [(21, 5, 5), (5, 0, 3), (1, 2, 8)])
def test_purged_walk_forward_rule(h, embargo, folds):
    dates = np.arange(300, 1300)
    out = purged_walk_forward(dates, h, folds, embargo)
    assert len(out) == folds
    for train, test in out:
        assert train.max() + h + embargo < test.min()  # purge + embargo: no label window touches the test block
        assert np.all(np.diff(test) == 1)  # contiguous test block
        # nothing eligible is thrown away: the first date not in train violates the rule
        nxt = train.max() + 1
        assert nxt + h + embargo >= test.min()


def test_label_valid_dates_never_read_out_of_sample(history):
    p = build_panel(history.get("R01"))
    d = label_valid_is_dates(p)
    assert d.max() + p.horizon == p.is_end


def test_zscore_rows_equals_library_zscore(history):
    ds = history.get("R01")
    for name in SIGNAL_NAMES:
        s = dataset_signal(ds, name)
        np.testing.assert_allclose(zscore_rows(s), zscore_matrix(s), atol=1e-12, equal_nan=True)


def test_fit_is_unchanged_by_out_of_sample_prices(history):
    """A planted change to every price after the in-sample end must not move the fitted ridge."""
    ds = history.get("R01")
    p = build_panel(ds)
    coef = fit_ridge(p, label_valid_is_dates(p), 100.0)
    close = ds.close.copy()
    close[ds.in_sample_end_index + 1 :] *= 1.5
    p2 = build_panel(replace(ds, close=close))
    assert np.array_equal(coef, fit_ridge(p2, label_valid_is_dates(p2), 100.0))


def test_a_label_leak_would_be_caught(history):
    """Control for the test above: letting labels run past the IS end does move the fit."""
    ds = history.get("R01")
    p = build_panel(ds)
    leaky_dates = np.arange(p.start, p.is_end + 1)  # last h labels read OOS prices
    close = ds.close.copy()
    close[ds.in_sample_end_index + 1 :] *= np.linspace(1.0, 3.0, close.shape[0] - ds.in_sample_end_index - 1)[:, None] ** np.linspace(-1, 1, close.shape[1])
    p2 = build_panel(replace(ds, close=close))
    assert not np.array_equal(fit_ridge(p, leaky_dates, 100.0), fit_ridge(p2, leaky_dates, 100.0))


def test_every_trial_is_ledgered_once(history):
    ds = history.get("R01")
    ledger = TrialLedger(path=None)
    r = combine_signals(ds, ledger=ledger)
    n = len(ALPHA_GRID) + len(SIGNAL_NAMES)
    assert r.n_trials == n == len(ledger)
    again = combine_signals(ds, ledger=ledger)  # a re-run of identical configurations adds nothing
    assert again.n_trials == n and len(ledger) == n
    assert again.alpha == r.alpha and again.coef == r.coef
    assert r.label.startswith("fitted to synthetic generator ")
    d = r.to_dict()
    assert set(d["out_of_sample"]) == {"combination", *SIGNAL_NAMES}
    assert d["promote"] == (d["out_of_sample"]["combination"]["dsr"] >= PROMOTE_DSR)


def test_realistic_momentum_set_reports_combination_next_to_singles(history):
    """On the momentum-premium set the combination loads on momentum and its OOS DSR is reported."""
    from ceap.data.historical import HistoricalGenerator
    from ceap.data.research_scenarios import research_scenario_for

    ds = HistoricalGenerator().generate(research_scenario_for("momentum_premium"))
    r = combine_signals(ds, ledger=TrialLedger(path=None))
    assert max(r.coef, key=lambda k: abs(r.coef[k])) == "momentum_12_1" and r.coef["momentum_12_1"] > 0
    assert math.isfinite(r.combination.dsr) and all(math.isfinite(s.dsr) for s in r.singles.values())
    assert r.singles["momentum_12_1"].sharpe_ann > 0


def _null_promote(seed: int) -> bool:
    from ceap.data.historical import HistoricalGenerator
    from ceap.data.research_scenarios import noise_scenario

    return combine_signals(HistoricalGenerator().generate(noise_scenario(seed)), ledger=TrialLedger(path=None)).promote


def _null_rate(n: int, first: int) -> tuple[int, float, float]:
    k = sum(_null_promote(s) for s in range(first, first + n))  # serial: spawn-safe on Windows
    lo, hi = clopper_pearson(k, n)
    return k, lo, hi


@pytest.mark.slow
def test_null_promote_rate_smoke_40_seeds():
    k, lo, _ = _null_rate(40, 0)
    assert lo <= 0.05, f"{k}/40 PROMOTE on pure noise"


@pytest.mark.nightly
def test_null_promote_rate_within_hurdle_band_200_seeds():
    """Over 200 null seeds the PROMOTE rate is not significantly above the 5 % hurdle target."""
    k, lo, _ = _null_rate(200, 0)
    assert lo <= 0.05 and k / 200 <= 0.10, f"{k}/200 PROMOTE on pure noise"
