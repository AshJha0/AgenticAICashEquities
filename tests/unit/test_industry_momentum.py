"""Unit tests for study E1 (synthetic data only; no network, no cached files)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from ceap.data.real.prereg import PreregistrationError, require_prereg_lock
from ceap.studies import industry_momentum as im


def _panel(n_months: int = 60, n_ind: int = 49, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.period_range("1926-07", periods=n_months, freq="M")
    return pd.DataFrame(
        rng.normal(0.01, 0.05, (n_months, n_ind)), index=idx, columns=[f"I{i}" for i in range(n_ind)]
    )


def test_prereg_lock_matches() -> None:
    require_prereg_lock(im.PREREG, im.LOCK)


def test_parse_first_block_stops_and_masks() -> None:
    text = "title\n,A,B\n192607, 1.00, -99.99\n192608, 2.00, 3.00\n\n Annual\n,A,B\n1927, 5, 5\n"
    df = im.parse_first_monthly_block(text)
    assert list(df.columns) == ["A", "B"] and len(df) == 2
    assert df.iloc[0, 0] == pytest.approx(0.01) and np.isnan(df.iloc[0, 1])


def test_book_picks_winners_and_no_lookahead() -> None:
    p = _panel()
    p.iloc[:, :7] = 0.10  # persistent winners
    p.iloc[:, -7:] = -0.10  # persistent losers
    book = im.build_book(p)
    assert book.gross.iloc[1] == pytest.approx(0.20)
    assert book.turnover.iloc[0] == pytest.approx(2.0)
    # changing a future month must not change earlier returns
    q = p.copy()
    q.iloc[-1] = 0.5
    b2 = im.build_book(q)
    pd.testing.assert_series_equal(book.gross.iloc[:-1], b2.gross.iloc[:-1])
    assert str(book.gross.index[0]) == "1927-01"


def test_breadth_drop() -> None:
    p = _panel()
    p.iloc[10, 25:] = np.nan
    assert str(p.index[10]) in im.build_book(p).dropped


def test_costs_and_breakeven() -> None:
    book = im.Book(pd.Series([0.01, 0.01]), pd.Series([1.0, 1.0]), [])
    assert im.net_returns(book, 10).iloc[0] == pytest.approx(0.009)
    assert im.breakeven_bps(book) == pytest.approx(100.0)


def test_nw_t_iid_close_to_ols() -> None:
    x = pd.Series(np.random.default_rng(1).normal(0.01, 0.05, 2000))
    r = im.nw_mean_t(x)
    ols_t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
    assert r["t"] == pytest.approx(ols_t, rel=0.1)


def test_andrews_lag_grows_with_autocorrelation() -> None:
    rng = np.random.default_rng(2)
    e = rng.normal(size=1000)
    ar = np.zeros(1000)
    for i in range(1, 1000):
        ar[i] = 0.7 * ar[i - 1] + e[i]
    assert im.andrews_lag(ar) > im.andrews_lag(e)


def test_bootstrap_ci_contains_point() -> None:
    x = pd.Series(np.random.default_rng(3).normal(0.01, 0.04, 300))
    lo, hi = im.stationary_bootstrap_sharpe_ci(x, n_boot=300)
    assert lo < im.sharpe_ann(x.to_numpy()) < hi


def test_market_state_shifted_to_holding_month() -> None:
    idx = pd.period_range("1926-07", periods=40, freq="M")
    ff3 = pd.DataFrame({"Mkt-RF": -0.01, "RF": 0.0}, index=idx)
    down = im.market_state(ff3)
    assert down.loc[pd.Period("1929-07", "M")] == 1.0  # formation 1929-06 closes the first 36-month window
    assert np.isnan(down.loc[pd.Period("1929-06", "M")])


def test_holdout_opened_once(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    im.open_holdout_once(ledger)
    assert json.loads(ledger.read_text())["opened"] is True
    with pytest.raises(PreregistrationError):
        im.open_holdout_once(ledger)


def test_state_test_single_state_not_testable() -> None:
    g = pd.Series([0.01, 0.02], index=pd.period_range("2016-01", periods=2, freq="M"))
    d = pd.Series([0.0, 0.0], index=g.index)
    assert im.state_test(g, d)["status"] == "NOT TESTABLE"


def test_crash_rerun_only_once(tmp_path) -> None:
    ledger, res = tmp_path / "l.json", tmp_path / "r.json"
    with pytest.raises(PreregistrationError):
        im.record_crash_rerun(ledger, res, "x")
    im.open_holdout_once(ledger)
    im.record_crash_rerun(ledger, res, "x")
    with pytest.raises(PreregistrationError):
        im.record_crash_rerun(ledger, res, "x")
