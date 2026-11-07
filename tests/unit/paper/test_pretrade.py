"""Desk controls: pre-trade checks, borrow availability, fill price band, attribution."""

from __future__ import annotations

import numpy as np
import pytest

from ceap.analytics.backtest import FinancingModel
from ceap.paper.feed import MarketPanel
from ceap.paper.fills import BookState, FillParams, OpenOrder, simulate_day
from ceap.paper.pretrade import RiskLimits, check_orders, halt_reason

DATES = np.array(["2026-01-05", "2026-01-06"], dtype="datetime64[D]")


def panel(close, adv=1e6, tiers=("A", "C")) -> MarketPanel:
    c = np.asarray(close, dtype=float)
    n, m = c.shape
    return MarketPanel("t", ("AAA", "BBB")[:m], tiers[:m], DATES[:n], c, c.copy(), np.full((n, m), adv),
                       np.full((n, m), adv), np.full((n, m), 10.0), np.ones((n, m), dtype=bool))


P = panel([[100.0, 50.0], [100.0, 50.0]])


def rules(res):
    return {(b["symbol"], b["detail"]["rule"]): b["detail"]["allowed"] for b in res.blocks}


def test_restricted_blocks_increases_but_allows_reduction():
    lim = RiskLimits(restricted=("AAA",))
    res = check_orders({"AAA": 100.0}, {"AAA": 50.0}, 1e6, P, 1, lim)
    assert res.deltas == {} and rules(res) == {("AAA", "restricted"): 0.0}
    res = check_orders({"AAA": -80.0}, {"AAA": 50.0}, 1e6, P, 1, lim)
    assert res.deltas == {"AAA": -50.0}  # flatten, never flip


def test_hard_to_borrow_blocks_the_short_increase_at_staging():
    res = check_orders({"BBB": -300.0}, {"BBB": 100.0}, 1e6, P, 1, RiskLimits(htb_tiers=("C",)))
    assert res.deltas == {"BBB": -100.0} and rules(res) == {("BBB", "hard_to_borrow"): -100.0}


def test_fat_finger_band_drops_the_order():
    jump = panel([[100.0, 50.0], [140.0, 50.0]])
    res = check_orders({"AAA": 10.0, "BBB": 10.0}, {}, 1e6, jump, 1, RiskLimits(price_band=0.25))
    assert res.deltas == {"BBB": 10.0} and ("AAA", "price_band") in rules(res)


def test_max_order_adv_and_name_weight_clip():
    small = panel([[100.0, 50.0], [100.0, 50.0]], adv=1000.0)
    res = check_orders({"AAA": 900.0}, {}, 1e9, small, 1, RiskLimits(max_order_adv=0.25))
    assert res.deltas == {"AAA": 250.0}
    res = check_orders({"AAA": 2000.0}, {}, 1e6, P, 1, RiskLimits(max_name_weight=0.10))
    assert res.deltas == {"AAA": pytest.approx(1000.0)}  # 10 % of 1e6 at 100
    res = check_orders({"AAA": 10.0}, {"AAA": 1500.0}, 1e6, P, 1, RiskLimits(max_name_weight=0.10))
    assert res.deltas == {}  # already over the cap after drift: may not grow


def test_gross_limit_drops_only_gross_increasing_orders():
    res = check_orders({"AAA": 1000.0, "BBB": -100.0}, {"AAA": 5000.0, "BBB": 9000.0}, 1e6, P, 1,
                       RiskLimits(max_gross=1.0, max_name_weight=1.0))
    assert res.deltas == {"BBB": -100.0} and ("AAA", "max_gross") in rules(res)


def test_kill_switch_reads_the_environment(monkeypatch):
    monkeypatch.delenv("CEAP_PAPER_HALT", raising=False)
    assert halt_reason() is None
    monkeypatch.setenv("CEAP_PAPER_HALT", "vendor data suspect")
    assert halt_reason() == "vendor data suspect"


def test_fill_time_htb_and_price_band():
    s = BookState.new(1e6)
    s.orders = {"BBB": OpenOrder("o1", "BBB", -100.0, "2026-01-02", staging_id="S", approval_id="A"),
                "AAA": OpenOrder("o2", "AAA", 10.0, "2026-01-02", decision_price=70.0)}
    params = FillParams(risk=RiskLimits(htb_tiers=("C",), fill_price_band=0.25))
    res = simulate_day(s, P, 0, params, 0.0, np.array([100.0, 100.0]))
    by = {(e["kind"], e["symbol"]): e for e in res.events}
    assert by[("reject", "BBB")]["detail"] == {"reason": "hard_to_borrow", "staging_id": "S", "approval_id": "A"}
    assert by[("cancel", "AAA")]["detail"]["reason"] == "price_band"  # 100 vs 70 decision: > 25 %
    assert not res.state.positions and res.state.waived == {"BBB": -100.0, "AAA": 10.0}


def test_attribution_sums_to_the_equity_change():
    p = panel([[100.0, 50.0], [103.0, 48.0]])
    s = BookState.new(1e6)
    s.orders = {"AAA": OpenOrder("o", "AAA", 1000.0, "2026-01-02"), "BBB": OpenOrder("p", "BBB", -500.0, "2026-01-02")}
    params = FillParams(financing=FinancingModel())
    d0 = simulate_day(s, p, 0, params, 0.0001, np.array([100.0, 100.0]))
    st = d0.state
    st.target = {"AAA": 1200.0, "BBB": -500.0}  # 200 AAA short of target: that gap is drift
    d1 = simulate_day(st, p, 1, params, 0.0001, np.array([100.0, 100.0]))
    a = d1.snapshot["attribution"]
    assert a["signal"] == pytest.approx(1200 * 3 + (-500) * (-2))
    assert a["drift"] == pytest.approx(-200 * 3)
    assert sum(a.values()) == pytest.approx(d1.state.equity - d0.state.equity, abs=1e-9)
