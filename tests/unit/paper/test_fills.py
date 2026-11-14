"""RP.1 / RP.2 known answers for the pure paper step (no database)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ceap.analytics.backtest import CostModel, FinancingModel, PositionCaps
from ceap.paper.feed import MarketPanel, Split, apply_splits
from ceap.paper.fills import BookState, FillParams, OpenOrder, simulate_day

DATES = np.array(["2026-01-05", "2026-01-06", "2026-01-07"], dtype="datetime64[D]")


def panel(close, adv=(100_000.0, 50_000.0), spread=10.0, low=None, members=None, tiers=("A", "C")) -> MarketPanel:
    c = np.asarray(close, dtype=float)
    n, m = c.shape
    return MarketPanel(
        name="hand", symbols=("AAA", "BBB")[:m], tiers=tiers[:m], dates=DATES[:n], close=c,
        low=c.copy() if low is None else np.asarray(low, dtype=float),
        volume=np.tile(np.asarray(adv[:m]), (n, 1)), adv_20=np.tile(np.asarray(adv[:m], dtype=float), (n, 1)),
        spread_bps=np.full((n, m), spread),
        members=np.ones((n, m), dtype=bool) if members is None else np.asarray(members, dtype=bool),
    )


VOL = np.array([100.0, 100.0])  # 100 bps daily vol for the cost model


def run(p: MarketPanel, state: BookState, params: FillParams, rf=(0.0, 0.0, 0.0)) -> list:
    out = []
    for i in range(p.n_days):
        res = simulate_day(state, p, i, params, rf[i], VOL)
        out.append(res)
        state = res.state
    return out


def test_two_name_three_day_ledger_by_hand():
    """Long 1000 AAA, short 500 BBB filled on day 0; financing and borrow on days 1 and 2."""
    p = panel([[100.0, 50.0], [102.0, 49.0], [101.0, 51.0]])
    s0 = BookState.new(1_000_000.0)
    s0.orders = {"AAA": OpenOrder("o1", "AAA", 1000.0, "2026-01-02"),
                 "BBB": OpenOrder("o2", "BBB", -500.0, "2026-01-02")}
    params = FillParams(financing=FinancingModel(), costs=CostModel())
    rf = (0.0001, 0.0001, 0.0001)
    days = run(p, s0, params, rf)
    # day 0: participation 1000/100000 = 500/50000 = 0.01 -> impact 0.6 * 100 * sqrt(0.01) = 6 bps, +5 half spread
    cost_a, cost_b = 1000 * 100 * 11e-4, 500 * 50 * 11e-4
    cash0 = 1_000_000 - 100_000 - cost_a + 25_000 - cost_b
    assert days[0].state.cash == pytest.approx(cash0, abs=1e-9)
    assert days[0].snapshot["equity"] == pytest.approx(cash0 + 100_000 - 25_000, abs=1e-9)
    assert days[0].snapshot["costs"] == pytest.approx(137.5, abs=1e-9)
    # day 1: interest on day-0 cash, borrow on 500 BBB at 50 (tier C, 1000 bps a year)
    b = 0.10 / 252
    cash1 = cash0 * 1.0001 - 500 * 50 * b
    assert days[1].snapshot["mark_pnl"] == pytest.approx(1000 * 2 + (-500) * (-1), abs=1e-9)
    assert days[1].state.cash == pytest.approx(cash1, abs=1e-9)
    assert days[1].snapshot["equity"] == pytest.approx(cash1 + 102_000 - 24_500, abs=1e-9)
    # day 2: borrow now on BBB marked at 49
    cash2 = cash1 * 1.0001 - 500 * 49 * b
    assert days[2].state.cash == pytest.approx(cash2, abs=1e-9)
    assert days[2].snapshot["equity"] == pytest.approx(cash2 + 101_000 - 25_500, abs=1e-9)
    assert days[2].snapshot["gross_exposure"] == pytest.approx((101_000 + 25_500) / days[2].snapshot["equity"])
    assert days[2].snapshot["net_exposure"] == pytest.approx((101_000 - 25_500) / days[2].snapshot["equity"])
    assert days[2].state.positions == {"AAA": 1000.0, "BBB": -500.0}


def test_participation_cap_carries_forward_until_filled():
    p = panel([[10.0], [10.0], [10.0]], adv=(1_000.0,))  # cap = 10 % of 1000 = 100 shares a day
    s0 = BookState.new(100_000.0)
    s0.orders = {"AAA": OpenOrder("o1", "AAA", 250.0, "2026-01-02")}
    days = run(p, s0, FillParams(financing=FinancingModel(enabled=False)))
    fills = [[e["quantity"] for e in d.events if e["kind"] == "fill"] for d in days]
    assert fills == [[100.0], [100.0], [50.0]]
    assert days[0].events[-1]["detail"]["carried"] == 150.0
    assert days[2].state.positions["AAA"] == 250.0 and not days[2].state.orders


def test_missing_price_rejects_and_carries_and_non_member_cancels():
    p = panel([[float("nan"), 50.0], [10.0, 50.0], [10.0, 50.0]], members=[[1, 1], [1, 0], [1, 0]])
    s0 = BookState.new(100_000.0)
    s0.orders = {"AAA": OpenOrder("o1", "AAA", 10.0, "2026-01-02"), "BBB": OpenOrder("o2", "BBB", 5.0, "2026-01-05")}
    days = run(p, s0, FillParams(financing=FinancingModel(enabled=False)))
    kinds0 = {(e["kind"], e["symbol"]): e for e in days[0].events}
    assert kinds0[("reject", "AAA")]["detail"]["reason"] == "missing_price"
    assert ("fill", "BBB") not in kinds0  # staged at day-0 close: fills next session at the earliest
    kinds1 = {(e["kind"], e["symbol"]): e for e in days[1].events}
    assert kinds1[("fill", "AAA")]["quantity"] == 10.0  # the carried quantity fills when a price returns
    assert kinds1[("cancel", "BBB")]["detail"]["reason"] == "not_a_member"


def test_ssr_blocks_only_the_short_increasing_part():
    close = [[100.0], [100.0], [100.0]]
    low = [[100.0], [88.0], [100.0]]  # day 1 trades 12 % below the prior close
    p = panel(close, low=low, adv=(1e9,))
    s0 = BookState.new(1e6)
    s0.orders = {"AAA": OpenOrder("o0", "AAA", 100.0, "2026-01-02")}
    d0 = simulate_day(s0, p, 0, FillParams(), 0.0, VOL)
    st1 = d0.state
    st1.orders = {"AAA": OpenOrder("o1", "AAA", -300.0, "2026-01-05")}
    d1 = simulate_day(st1, p, 1, FillParams(), 0.0, VOL)
    rej = [e for e in d1.events if e["kind"] == "reject"]
    fill = [e for e in d1.events if e["kind"] == "fill"]
    assert rej[0]["quantity"] == -200.0 and rej[0]["detail"]["reason"] == "ssr"
    assert fill[0]["quantity"] == -100.0  # selling the long down to flat is allowed
    assert "AAA" not in d1.state.positions


def test_split_doubles_shares_and_orders_and_leaves_equity_unchanged():
    base = panel([[100.0], [100.0], [100.0]], adv=(1e9,))
    p = apply_splits(base, [Split("AAA", "2026-01-06", 2.0)])
    s0 = BookState.new(1e6)
    s0.orders = {"AAA": OpenOrder("o0", "AAA", 1000.0, "2026-01-02")}
    params = FillParams(financing=FinancingModel(enabled=False), costs=CostModel(impact_coefficient=0.0))
    d0 = simulate_day(s0, p, 0, params, 0.0, VOL)
    st = d0.state
    st.orders = {"AAA": OpenOrder("o1", "AAA", 10.0, "2026-01-05")}
    d1 = simulate_day(st, p, 1, params, 0.0, VOL)
    split = [e for e in d1.events if e["kind"] == "split"][0]
    assert split["quantity"] == 1000.0 and split["detail"]["ratio"] == 2.0
    fill = [e for e in d1.events if e["kind"] == "fill"][0]
    assert fill["quantity"] == 20.0 and fill["price"] == 50.0  # the open order was split too
    assert d1.state.positions["AAA"] == 2020.0
    cost = 20 * 50.0 * 5e-4
    assert d1.state.equity == pytest.approx(d0.state.equity - cost, abs=1e-9)
    assert p.adjusted_close(1)[0, 0] == 50.0 and p.adjusted_close(0)[0, 0] == 100.0  # no look-ahead


def test_order_expires_after_ttl():
    p = panel([[float("nan")], [float("nan")], [float("nan")]])
    s0 = BookState.new(1e5)
    s0.orders = {"AAA": OpenOrder("o1", "AAA", 1.0, "2026-01-02")}
    days = run(p, s0, FillParams(order_ttl_days=2))
    assert [e["detail"]["reason"] for e in days[1].events if e["kind"] == "cancel"] == ["expired"]


def test_fill_cost_equals_backtest_cost_model_for_random_trades():
    rng = np.random.default_rng(7)
    cm = CostModel()
    for _ in range(20):
        price, adv, spread, vol = rng.uniform(5, 500), rng.uniform(1e4, 1e7), rng.uniform(1, 40), rng.uniform(50, 400)
        qty = float(rng.integers(-5000, 5000)) or 1.0
        p = MarketPanel("r", ("AAA",), ("A",), DATES[:1], np.array([[price]]), np.array([[price]]),
                        np.array([[adv]]), np.array([[adv]]), np.array([[spread]]), np.ones((1, 1), dtype=bool))
        s = BookState.new(1e9)
        s.orders = {"AAA": OpenOrder("o", "AAA", qty, "2026-01-02")}
        params = FillParams(caps=PositionCaps(max_participation=1.0))
        res = simulate_day(s, p, 0, params, 0.0, np.array([vol]))
        fill = [e for e in res.events if e["kind"] == "fill"][0]
        bps = cm.trade_cost_bps(np.array([spread / 2]), np.array([vol]), np.array([abs(qty) / adv]))[0]
        assert fill["cost"] == pytest.approx(abs(qty) * price * bps / 1e4, rel=1e-12)


@st.composite
def books(draw):
    n = draw(st.integers(3, 12))
    m = draw(st.integers(1, 2))
    rets = draw(st.lists(st.floats(-0.15, 0.15), min_size=n * m, max_size=n * m))
    close = 50.0 * np.cumprod(1.0 + np.array(rets).reshape(n, m), axis=0)
    orders = draw(st.lists(st.floats(-2000, 2000), min_size=n * m, max_size=n * m))
    rf = draw(st.lists(st.floats(-0.0002, 0.0005), min_size=n, max_size=n))
    return close, np.array(orders).reshape(n, m), rf


@settings(max_examples=200, deadline=None)  # the profile decides randomisation (E2)
@given(books())
def test_property_cash_plus_positions_is_equity_and_pnl_decomposes(case):
    """cash + position value = equity, and equity - capital = mark P&L + financing - borrow - costs."""
    close, orders, rf = case
    n, m = close.shape
    dates = np.arange(np.datetime64("2026-01-05"), np.datetime64("2026-01-05") + n).astype("datetime64[D]")
    p = MarketPanel("prop", ("AAA", "BBB")[:m], ("A", "C")[:m], dates, close, close * 0.95,
                    np.full((n, m), 3000.0), np.full((n, m), 3000.0), np.full((n, m), 8.0), np.ones((n, m), dtype=bool))
    state = BookState.new(1e6)
    cum = 0.0
    for i in range(n):
        for j, sym in enumerate(p.symbols):
            if orders[i, j]:
                state.orders[sym] = OpenOrder(f"o{i}{j}", sym, float(orders[i, j]), "2026-01-01")
        res = simulate_day(state, p, i, FillParams(), rf[i], np.full(m, 120.0))
        s = res.snapshot
        cum += s["mark_pnl"] + s["financing"] - s["borrow"] - s["costs"]
        assert math.isclose(res.state.cash + res.state.market_value(), res.state.equity, rel_tol=1e-12, abs_tol=1e-6)
        assert math.isclose(res.state.equity - 1e6, cum, rel_tol=1e-9, abs_tol=1e-6)
        state = res.state
