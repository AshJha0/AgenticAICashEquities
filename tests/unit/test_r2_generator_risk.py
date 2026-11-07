# ruff: noqa: N803, N806  (mathematical notation: R, P, T, A, B as in the formulas)
"""2.9 generator and parameter-derived ground truth, 2.12 risk, 2.6 capacity, 2.14 profitability."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from ceap.analytics.portfolio_risk import (
    history_limit_checks,
    raw_betas_ex_self,
    shrink_betas,
    stress,
    target_portfolio,
)
from ceap.analytics.profitability import capacity_sweep, profitability_table, sharpe_standard_error
from ceap.data.ground_truth import derive_ground_truth, expected_statistics, p_stay_tercile
from ceap.data.historical import HistoricalStore
from ceap.data.research_scenarios import RESEARCH_TEMPLATES_RAW, all_research_scenarios

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def store() -> HistoricalStore:
    return HistoricalStore()


# ------------------------------------------------------------------ 2.9
def test_twenty_year_history_with_stylised_facts(store):
    ds = store.get("R02")
    m = ds.market_return
    assert ds.n_days == 5040 and ds.in_sample_days == 3528
    z = (m - m.mean()) / m.std()
    assert float((z**4).mean()) > 4.0  # fat tails (normal = 3)
    a = np.abs(m - m.mean())
    assert np.corrcoef(a[1:], a[:-1])[0, 1] > 0.1  # volatility clustering
    vol = np.sqrt(np.convolve(m**2, np.ones(21) / 21, mode="same"))
    spread = np.nanmean(ds.spread_bps, axis=1)
    assert np.corrcoef(vol, spread)[0, 1] > 0.3  # spreads widen with volatility
    assert (~ds.member_mask).any() and len(ds.delistings) == 2


def test_ground_truth_is_derived_from_parameters_not_from_a_run(monkeypatch):
    import ceap.analytics.backtest as bt
    import ceap.analytics.signal_statistics as ss

    def boom(*a, **k):
        raise AssertionError("ground truth must not run the analytics")

    monkeypatch.setattr(bt, "simulate_backtest", boom)
    monkeypatch.setattr(ss, "evaluate_signal", boom)
    truths = [derive_ground_truth(s) for s in RESEARCH_TEMPLATES_RAW]
    assert len(truths) == 10 and all(t.verdict is not None for t in truths)


def test_near_threshold_scenarios_exist():
    near = [s.id for s in RESEARCH_TEMPLATES_RAW if derive_ground_truth(s).near_threshold]
    assert len(near) >= 2, near


def test_catalogue_has_thirty_scenarios_with_truth_attached():
    specs = all_research_scenarios()
    assert len(specs) == 30 and len({s.id for s in specs}) == 30
    for s in specs:
        t = derive_ground_truth(s)
        assert s.expected_verdict is t.verdict and s.expected_flags == t.flags


def test_analytic_building_blocks():
    assert p_stay_tercile(0.0) == pytest.approx(1 / 3, abs=1e-3)
    assert p_stay_tercile(0.999) > 0.95
    noise = expected_statistics(RESEARCH_TEMPLATES_RAW[1])
    assert noise["t_is"] == 0.0 and noise["gross_sharpe_is"] == 0.0
    r03 = expected_statistics(RESEARCH_TEMPLATES_RAW[2], lag=0)
    r03_lag = expected_statistics(RESEARCH_TEMPLATES_RAW[2], lag=1)
    assert r03["gross_sharpe_is"] > r03_lag["gross_sharpe_is"] > 0  # the transient decays before a t+1 fill


def test_power_curve_is_committed_and_monotone():
    doc = json.loads((ROOT / "docs" / "quant_power_curve.json").read_text(encoding="utf-8"))
    rows = doc["rows"]
    assert len(rows) >= 6 and doc["seeds_per_point"] >= 40
    rates = [r["detection_rate"] for r in rows]
    assert rates[0] <= 0.15 and rates[-1] >= 0.8
    assert all(b >= a - 0.1 for a, b in zip(rates, rates[1:], strict=False))


# ------------------------------------------------------------------ 2.12
def test_beta_excludes_the_name_and_shrinks_toward_one():
    rng = np.random.default_rng(0)
    T, m = 2000, 10
    mkt = rng.normal(0, 0.01, T)
    betas = np.linspace(0.6, 1.5, m)
    r = mkt[:, None] * betas[None, :] + rng.normal(0, 0.01, (T, m))
    raw, se = raw_betas_ex_self(r)
    # each name's market excludes itself, so the estimate is close to the true beta / mean(other betas)
    others = np.array([(betas.sum() - b) / (m - 1) for b in betas])
    assert raw == pytest.approx(betas / others, abs=0.08)
    shrunk = shrink_betas(np.array([2.0, 0.0, 1.0]), np.array([0.5, 0.5, 0.01]))
    assert 1.0 < shrunk[0] < 2.0 and 0.0 < shrunk[1] < 1.0 and shrunk[2] == pytest.approx(1.0)
    no_est = shrink_betas(np.array([3.0, 1.2, 0.8]), np.array([np.inf, 0.1, 0.1]))
    assert no_est[0] == 1.0


def test_stress_known_answers(store):
    ds = store.get("R01")
    rep = target_portfolio(ds, "momentum_12_1", gross_notional=50e6)
    st = stress(rep, -500.0, ds)
    shorts = sum(p.weight for p in rep.positions if p.weight < 0)
    assert st.pnl_short_squeeze == pytest.approx(50e6 * shorts * 0.20, abs=0.01)
    assert st.pnl_factor < 0  # the signal's own factor reversing hurts the book
    t = ds.index_at_or_before(rep.as_of)
    days = max(abs(p.notional) / (0.1 * ds.adv_20[t, ds.col(p.symbol)] * ds.close[t, ds.col(p.symbol)])
               for p in rep.positions)
    assert st.liquidation_days == pytest.approx(days, rel=1e-3) and st.liquidation_cost > 0


def test_limits_over_history_report_the_worst_date(store):
    ds = store.get("R06")
    hist = history_limit_checks(ds, "momentum_12_1", gross_notional=50e6)
    names = {c["limit_name"] for c in hist}
    assert {"max_gross", "max_net", "max_abs_beta", "adv_participation"} <= names
    part = next(c for c in hist if c["limit_name"] == "adv_participation")
    assert part["breached"] and part["date"] > ds.in_sample_end
    assert all(c["utilisation"] >= 0 for c in hist)


# ------------------------------------------------------------------ 2.6 and 2.14
def test_capacity_sweep_acceptance(store):
    ds = store.get("R03")
    cap = capacity_sweep(ds, "reversal_5", rebalance_days=5)
    rows = cap["rows"]
    assert [r["gross_notional"] for r in rows] == [10e6, 25e6, 50e6, 100e6, 250e6, 500e6]
    assert cap["spearman_notional_sharpe"] == pytest.approx(-1.0)
    small, big = rows[0], rows[-1]
    assert small["net_sharpe"] - big["net_sharpe"] > 2 * max(small["sharpe_se"], big["sharpe_se"])
    assert cap["breakeven_aum"] is not None or cap["breakeven_note"]


def test_sharpe_standard_error():
    assert sharpe_standard_error(0.0, 252) == pytest.approx(1.0)
    assert sharpe_standard_error(1.0, 252 * 4) == pytest.approx(math.sqrt((1 + 0.5 / 252) / 1008) * math.sqrt(252))


def test_profitability_table_reproduces(store):
    ds = store.get("R01")
    t = profitability_table(ds, "momentum_12_1", with_capacity=False)
    assert {"breakeven_cost_bps_per_unit_turnover", "max_drawdown", "longest_drawdown_days",
            "calendar_year_returns", "worst_months", "factor_model", "cost_breakdown_bps_annual"} <= set(t)
    years = t["calendar_year_returns"]
    assert len(years) == 20
    total = float(np.prod([1 + v for v in years.values()]) - 1)
    from ceap.analytics.backtest import simulate_backtest

    run = simulate_backtest(ds, "momentum_12_1", report_bounds=False)
    assert total == pytest.approx(float(np.prod(1 + run.net[run.i0 : run.i1 + 1]) - 1), rel=1e-9)
    assert len(t["worst_months"]) == 3 and math.isfinite(t["factor_model"]["alpha_t"])
    assert t == profitability_table(ds, "momentum_12_1", with_capacity=False)  # deterministic
