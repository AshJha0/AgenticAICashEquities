"""RP.6: replaying the backtest's own fills through the paper step reproduces the backtest's NAV.

Both use the same fills (the backtest's post-trade weights, turned into shares at the fill close), so any
difference would be a difference in costs, financing, borrow or marking arithmetic. Financing is on with
a non-zero rf, SSR off and caps wide (so the fills are exactly the backtest's).
"""

from __future__ import annotations

import numpy as np

from ceap.analytics.backtest import (
    FinancingModel,
    PositionCaps,
    daily_returns,
    rolling_vol_bps,
    simulate_backtest,
)
from ceap.data.historical import HistoricalStore
from ceap.paper.feed import panel_from_historical
from ceap.paper.fills import BookState, FillParams, OpenOrder, simulate_day
from ceap.paper.job import PaperJob

CAPITAL = 10_000_000.0
DAYS = 60


def _window(ds):
    members = ds.member_mask
    for i0 in range(ds.start_index + 30, ds.n_days - DAYS):
        if members[i0 : i0 + DAYS + 1].all():
            return i0, i0 + DAYS
    raise AssertionError("no fully-listed window")


def test_paper_replay_equals_backtest_nav_within_1e9():
    ds = HistoricalStore().get("R01")
    i0, i1 = _window(ds)
    rf = np.full(ds.n_days, 0.00015)
    fin = FinancingModel(ssr=False)
    caps = PositionCaps(max_weight=1.0, max_participation=1e6)
    run = simulate_backtest(ds, "momentum_12_1", start=str(ds.dates[i0]), end=str(ds.dates[i1]),
                            rebalance_days=5, gross_notional=CAPITAL, financing=fin, caps=caps, risk_free=rf,
                            report_bounds=False)
    assert run.i0 == i0 and run.i1 == i1 and run.fill_weights
    nav_bt = np.cumprod(1.0 + run.net[i0 : i1 + 1])  # NAV(i0) = 1 (no fill on the first day)

    panel = panel_from_historical(ds, "equiv")
    vol = rolling_vol_bps(daily_returns(ds.close))
    params = FillParams(financing=fin, caps=caps)
    state = BookState.new(CAPITAL)
    equity = []
    for e in range(i0, i1 + 1):
        if e in run.fill_weights:
            pre = simulate_day(state, panel, e, params, rf[e], vol[e]).state  # the book just before the fill
            w = run.fill_weights[e]
            orders = {}
            for j, sym in enumerate(panel.symbols):
                q = w[j] * pre.equity / ds.close[e, j] - state.positions.get(sym, 0.0) * 1.0
                if abs(q) > 0:
                    orders[sym] = OpenOrder(f"bt-{e}-{j}", sym, float(q), "1900-01-01")
            state = state.copy()
            state.orders = orders
        res = simulate_day(state, panel, e, params, rf[e], vol[e])
        assert not res.state.orders
        state = res.state
        equity.append(state.equity / CAPITAL)
    np.testing.assert_allclose(np.array(equity), nav_bt, rtol=0, atol=1e-9)


def test_job_vol_row_equals_the_backtest_vol_series():
    ds = HistoricalStore().get("R01")
    from ceap.paper.config import PaperConfig
    from ceap.paper.feed import SyntheticFeed
    from ceap.persistence.db import Database

    full = rolling_vol_bps(daily_returns(ds.close))
    with Database() as db:
        job = PaperJob(db, PaperConfig(), SyntheticFeed("R01"))
        for i in (300, 1000, 3000):
            np.testing.assert_allclose(job.vol_row(i), full[i], rtol=1e-12, equal_nan=True)
