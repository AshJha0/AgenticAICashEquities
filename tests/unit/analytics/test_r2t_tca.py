"""R2T TCA realism: known-answer and generator-truth tests (2T.1 to 2T.8, PR tier).

Ground truth comes from generator parameters (``ceap.data.synthetic`` constants and the
universe's ``truth``), never from another analytics run. The 100/200-seed acceptance sweeps are in
``tests/evaluation/test_r2t_acceptance.py`` (``@pytest.mark.nightly``) with smaller PR-tier twins.
"""

from __future__ import annotations

import math
from datetime import time

import numpy as np
import pytest

from ceap.analytics.execution_metrics import StandardExecutionAnalytics, interval_vwap
from ceap.analytics.implementation_shortfall import (
    DEFAULT_FEES,
    implementation_shortfall_bps,
    is_standard_error_bps,
    schedule_price_variance,
    shortfall_components,
)
from ceap.analytics.market_impact import square_root_impact_estimate_bps
from ceap.analytics.peers import NO_CAUSAL_CLAIM, match_peers, peer_effect
from ceap.analytics.slippage import (
    effective_spread_bps,
    markout_bps,
    price_improvement_bps,
    realised_spread_bps,
)
from ceap.data.scenarios import get_scenario, scenario_for
from ceap.data.session import SESSION_TZ, matched_windows, previous_sessions
from ceap.data.synthetic import (
    ADV_SHARES,
    DECISION_DELAY_SECONDS,
    SIGMA_DAILY_BPS,
    SyntheticMarketGenerator,
    intraday_profile,
)
from ceap.domain.execution import Side


@pytest.fixture(scope="module")
def t01():
    return SyntheticMarketGenerator().generate(get_scenario("T01"))


@pytest.fixture(scope="module")
def t01_sell():
    return SyntheticMarketGenerator().generate(scenario_for("T01", "MSFT", seed=11, side="SELL"))


def _window_metrics(ds, start=None, end=None):
    from datetime import timedelta

    s, e = start or ds.window_start, end or ds.window_end
    a = StandardExecutionAnalytics()
    sel = lambda xs, hi=e: [x for x in xs if s <= x.timestamp < hi]  # noqa: E731
    return a.calculate(
        sel(ds.orders), sel(ds.executions), sel(ds.quotes, e + timedelta(seconds=300)), sel(ds.trades), s, e
    )


# ----------------------------------------------------------------------------- 2T.1
def test_session_is_us_regular_hours_new_york(t01):
    for q in t01.quotes:
        local = q.timestamp.astimezone(SESSION_TZ).time()
        assert time(9, 30) <= local < time(16, 0)
    assert t01.window_start.astimezone(SESSION_TZ).hour == 14
    assert t01.window_start.tzinfo is SESSION_TZ


def test_default_request_window_inside_us_session():
    """The platform's default 14:00-15:00 window (and its baseline) sit inside 09:30-16:00 ET."""
    from ceap.platform import default_request

    req = default_request("AAPL", "T01")
    for ts in (req.window_start, req.window_end, req.baseline_start, req.baseline_end):
        local = ts.astimezone(SESSION_TZ).time()
        assert time(9, 30) <= local <= time(16, 0)


def test_u_shaped_profile_has_mean_one_and_edges_above_midday():
    t = np.arange(23_400) + 0.5
    p = intraday_profile(t)
    assert p.mean() == pytest.approx(1.0, abs=1e-3)
    assert p[0] > 1.7 and p[-1] > 1.7 and p[11_700] < 0.61
    assert np.all(intraday_profile(t, u_shape=False) == 1.0)


def test_auction_and_late_prints_excluded_from_interval_vwap_by_default(t01):
    close = [t for t in t01.trades if t.condition == "AUCTION_CLOSE"]
    late = [t for t in t01.trades if t.condition == "LATE"]
    assert len(close) == 1 and late, "closing auction and late prints are generated and flagged"
    tail = [t for t in t01.trades if t.timestamp >= close[0].timestamp.replace(hour=15)]
    v_default, vol_default = interval_vwap(tail)
    v_all, vol_all = interval_vwap(tail, exclude_conditions=frozenset())
    assert vol_all - vol_default >= close[0].quantity
    assert v_default != v_all


def test_buy_and_sell_parents_fill_on_the_right_side(t01, t01_sell):
    from ceap.analytics.execution_metrics import QuoteIndex

    for ds, side in ((t01, Side.BUY), (t01_sell, Side.SELL)):
        idx = QuoteIndex(ds.quotes)
        flags = {e.liquidity_flag for e in ds.executions}
        assert flags == {"ADD", "REMOVE"}, "passive and aggressive fills"
        for e in ds.executions[:200]:
            assert e.side is side
            q = idx.at(e.timestamp)
            mid = q.mid
            # aggressive fills cross to the far side of the mid, passive ones rest on the near side
            far = (e.price - mid) * side.sign
            assert far > 0 if e.liquidity_flag == "REMOVE" else far < 0


def test_odd_lots_and_duplicate_timestamps_present(t01):
    assert any(e.quantity % 100 for e in t01.executions)
    ts = [e.timestamp for e in t01.executions]
    assert len(ts) != len(set(ts))


def test_delay_cost_comes_from_the_decision_lead(t01):
    parent = next(o for o in t01.parent_orders if o.order_id.endswith("-1400") and "-2026" not in o.order_id)
    assert (parent.timestamp - parent.decision_time).total_seconds() == DECISION_DELAY_SECONDS
    assert parent.adv_shares == ADV_SHARES and parent.sigma_daily_bps == SIGMA_DAILY_BPS


def test_matched_reference_sessions_carry_the_same_clock_window(t01):
    assert t01.reference_sessions == previous_sessions(t01.session_date)
    wins = matched_windows(t01.window_start, t01.window_end)
    assert len(wins) == 5
    for s, e in wins:
        assert (s.hour, e.hour) == (14, 15) and s.weekday() < 5
        assert any(o.is_parent and o.timestamp == s for o in t01.orders)


def test_universe_features():
    from ceap.data.tca_universe import ALGOS, BROKERS, UniverseConfig, generate_tca_universe

    u = generate_tca_universe(3, UniverseConfig(n_sessions=8, parents_per_session=40))
    assert {p.broker for p in u.parents} == set(BROKERS)
    assert {p.algo for p in u.parents} == set(ALGOS)
    assert {p.side for p in u.parents} == {Side.BUY, Side.SELL}
    assert any(p.multi_session for p in u.parents)
    moc = [p for p in u.parents if p.algo == "MOC"]
    assert moc and all(set(p.fill_flags) == {"AUCTION"} for p in moc)
    assert all(p.fill_qty.sum() <= p.quantity for p in u.parents), "a parent is never overfilled"
    assert u.halts and u.halts[0]["seconds"] == 300
    h = u.halts[0]
    for p in u.parents:
        if p.symbol == h["symbol"] and p.sessions[0].isoformat() == h["session"]:
            in_halt = (p.fill_times >= h["start_s"]) & (p.fill_times < h["start_s"] + h["seconds"])
            assert not in_halt.any(), "no fills during a LULD pause"


def test_multi_session_parent_is_linked_across_sessions():
    from datetime import datetime, timedelta

    from ceap.analytics.tca_aggregate import link_parent_children
    from ceap.domain.execution import Execution, Order, OrderStatus

    d1 = datetime(2026, 9, 17, 15, 0, tzinfo=SESSION_TZ)
    d2 = d1 + timedelta(days=1, hours=-5)
    parent = Order("P1", None, "AAPL", Side.BUY, 1000, "VWAP", None, d1)
    kids = [
        Order("C1", "P1", "AAPL", Side.BUY, 500, "VWAP", "XNAS", d1, status=OrderStatus.FILLED),
        Order("C2", "P1", "AAPL", Side.BUY, 500, "VWAP", "ARCA", d2, status=OrderStatus.FILLED),
        Order("C9", "MISSING", "AAPL", Side.BUY, 100, "VWAP", "ARCA", d2),
    ]
    fills = [
        Execution("E1", "C1", "AAPL", Side.BUY, 500, 100.0, "XNAS", d1, parent_order_id="P1"),
        Execution("E2", "C2", "AAPL", Side.BUY, 500, 100.1, "ARCA", d2),  # no parent id: via child
    ]
    linked = link_parent_children([parent, *kids], fills)
    assert set(linked) == {"P1"}
    lp = linked["P1"]
    assert lp.sessions == ("2026-09-17", "2026-09-18")
    assert lp.executed_quantity == 1000 and len(lp.children) == 2


# ----------------------------------------------------------------------------- 2T.3
def test_perold_components_known_answer_and_sum():
    # BUY 1000 target, decision 100, arrival 100.10, fills 600 @ 100.20 (mid 100.15), final 100.30
    c = shortfall_components(
        Side.BUY, 100.0, 100.10, [100.20], [600], [100.15], 1000, final_price=100.30, fees_per_share=[0.004]
    )
    norm = 100.0 * 1000 / 1e4
    assert c.delay_bps == pytest.approx(0.10 * 1000 / norm)  # decision-to-arrival on the whole order
    assert c.spread_bps == pytest.approx(0.05 * 600 / norm)
    assert c.timing_bps == pytest.approx(0.05 * 600 / norm)
    assert c.opportunity_bps == pytest.approx(0.20 * 400 / norm)  # priced from arrival
    assert c.fees_bps == pytest.approx(0.004 * 600 / norm)
    parts = c.delay_bps + c.spread_bps + c.timing_bps + c.opportunity_bps + c.fees_bps
    assert abs(parts - c.total_bps) <= 1e-9
    # without fees the components reproduce the classic Perold IS exactly
    plain = implementation_shortfall_bps(Side.BUY, 100.0, [100.20], [600], 1000, final_price=100.30)
    assert abs((c.total_bps - c.fees_bps) - plain) <= 1e-9


def test_sell_components_sign():
    c = shortfall_components(Side.SELL, 100.0, 99.9, [99.8], [1000], [99.85], 1000)
    assert c.delay_bps > 0 and c.spread_bps > 0 and c.timing_bps > 0


@pytest.mark.parametrize("fixture", ["t01", "t01_sell"])
def test_components_sum_to_reported_is_on_generated_data(fixture, request):
    m = _window_metrics(request.getfixturevalue(fixture))
    parts = m.delay_cost_bps + m.spread_cost_bps + m.timing_cost_bps + m.opportunity_cost_bps + m.fees_bps
    assert abs(parts - m.implementation_shortfall_bps) <= 1e-9
    assert abs(m.arrival_shortfall_bps - (m.implementation_shortfall_bps - m.delay_cost_bps)) <= 1e-9


def test_fee_schedule_signs():
    assert DEFAULT_FEES.per_share("REMOVE") == pytest.approx(0.004)
    assert DEFAULT_FEES.per_share("ADD") == pytest.approx(-0.001)
    assert DEFAULT_FEES.per_share("AUCTION") == pytest.approx(0.001)


# ----------------------------------------------------------------------------- 2T.4
def test_is_standard_error_linear_schedule_is_sigma_sqrt_t_over_3():
    assert schedule_price_variance([(0.3, 1.0, 0.0)]) == pytest.approx(0.1)
    assert is_standard_error_bps(100.0, 0.3) == pytest.approx(100.0 * math.sqrt(0.1))
    assert is_standard_error_bps(100.0, 0.3, execution_noise_bps=10.0) == pytest.approx(
        math.sqrt(1000.0 + 100.0)
    )
    # a constant hold (MOC from arrival) carries the full T; half held overnight is 0.25 * T
    assert schedule_price_variance([(0.5, 1.0, 1.0)]) == pytest.approx(0.5)
    assert schedule_price_variance([(1.0, 0.5, 0.5)]) == pytest.approx(0.25)


def test_linear_schedule_variance_matches_monte_carlo():
    """sigma^2 T/3 is the variance of int x(t) dW for x falling linearly from 1 to 0 (generator check)."""
    rng = np.random.default_rng(0)
    n, paths = 400, 20_000
    x = 1.0 - (np.arange(n) + 0.5) / n
    dw = rng.normal(0.0, math.sqrt(1.0 / n), (paths, n))
    v = (dw * x).sum(axis=1).var()
    assert v == pytest.approx(1.0 / 3.0, rel=0.03)


def test_residual_z_reported_and_finite(t01):
    m = _window_metrics(t01)
    assert math.isfinite(m.pretrade_estimate_bps) and math.isfinite(m.is_residual_z)
    assert m.is_residual_bps == pytest.approx(m.arrival_shortfall_bps - m.pretrade_estimate_bps)
    assert m.is_standard_error_bps > m.is_price_risk_bps > 0
    lo, hi = m.pretrade_interval_bps
    assert lo < m.pretrade_estimate_bps < hi


def test_legacy_delta_is_only_a_fallback():
    from dataclasses import replace

    from ceap.analytics.attribution import attribute_causes

    from .conftest import make_market, make_metrics

    w = make_metrics(implementation_shortfall_bps=10.0)
    b = make_metrics(implementation_shortfall_bps=5.0)
    mk = make_market()
    legacy = attribute_causes(w, b, mk, mk)
    assert legacy.basis == "legacy_delta" and legacy.deteriorated
    modern = attribute_causes(
        replace(w, is_residual_bps=3.0, is_standard_error_bps=15.0, is_price_risk_bps=14.0), b, mk, mk
    )
    assert modern.basis == "pretrade_residual" and not modern.deteriorated  # 0.2 SE is noise


# ----------------------------------------------------------------------------- 2T.5
def test_markout_realised_spread_and_price_improvement_known_answers():
    p, m0, mh, q = [100.05, 100.06], [100.0, 100.0], [100.10, 100.10], [100, 300]
    # markout: s (m_h - p) / p, size weighted
    expected_mk = (100 * (0.05 / 100.05) + 300 * (0.04 / 100.06)) / 400 * 1e4
    assert markout_bps(Side.BUY, p, mh, q) == pytest.approx(expected_mk)
    # beta adjustment removes beta * index return
    assert markout_bps(Side.BUY, p, mh, q, index_returns=[0.0005, 0.0005], beta=1.0) == pytest.approx(
        expected_mk - 5.0
    )
    eff = effective_spread_bps(p, m0, Side.BUY, q)
    real = realised_spread_bps(Side.BUY, p, m0, mh, q)
    impact = (100 * 2 * 0.10 / 100.0 + 300 * 2 * 0.10 / 100.0) / 400 * 1e4
    assert eff == pytest.approx(real + impact)  # effective = realised + price impact
    assert eff == pytest.approx((100 * 10.0 + 300 * 12.0) / 400)  # size weighted, not 11.0
    assert price_improvement_bps(Side.BUY, [100.04], [100.05], [100]) == pytest.approx(0.01 / 100.05 * 1e4)
    assert price_improvement_bps(Side.SELL, [99.97], [99.95], [100]) == pytest.approx(0.02 / 99.95 * 1e4)


def test_markout_beyond_data_is_nan_not_clamped(t01):
    m = _window_metrics(t01, end=t01.window_end)
    assert all(math.isfinite(v) for v in m.markouts_bps.values())
    from ceap.analytics.execution_metrics import QuoteIndex

    idx = QuoteIndex(t01.quotes[:10])
    assert math.isnan(idx.mid_at_or_nan(t01.quotes[50].timestamp))


def test_square_root_impact_units_daily_clock():
    # 1 % of ADV with 63 bps daily sigma: c * 63 * 0.1
    assert square_root_impact_estimate_bps(0.01 * ADV_SHARES, ADV_SHARES, 63.0, 0.6) == pytest.approx(3.78)
    assert math.isnan(square_root_impact_estimate_bps(100, 0, 63.0))


def test_t01_impact_reads_near_zero_not_minus_45(t01):
    m = _window_metrics(t01)
    assert abs(m.market_impact_bps) < 5.0  # v0.4 read -45 bps of drift as "permanent impact"


def test_venue_statistics_renamed_and_conditioned(t01):
    m = _window_metrics(t01)
    v = next(iter(m.venue_statistics.values()))
    assert v.volume_share == v.market_volume_participation  # deprecated alias kept
    assert sum(x.fill_share for x in m.venue_statistics.values()) == pytest.approx(1.0)
    keys = list(m.venue_statistics_conditioned)
    assert any("|ADD|" in k for k in keys) and any("|REMOVE|" in k for k in keys)


# ----------------------------------------------------------------------------- 2T.6
def test_peer_effect_known_answer_and_ci():
    e = peer_effect(10.0, [1.0, 2.0, 3.0, 4.0, 5.0], n_boot=4000, seed=1)
    assert e.effect == pytest.approx(7.0) and e.peer_mean == pytest.approx(3.0)
    assert e.ci_low < 7.0 < e.ci_high and e.excludes_zero
    assert e.standardised == pytest.approx(7.0 / np.std([1, 2, 3, 4, 5], ddof=1))
    assert peer_effect(1.0, [1.0]).n_peers == 1 and math.isnan(peer_effect(1.0, []).effect)
    # deterministic in the seed
    assert peer_effect(10.0, [1, 2, 3], seed=4) == peer_effect(10.0, [1, 2, 3], seed=4)


def test_match_peers_same_key_other_sessions():
    items = [
        ("AAPL", "BUY", "M", "d1"),
        ("AAPL", "BUY", "M", "d2"),
        ("AAPL", "SELL", "M", "d2"),
        ("AAPL", "BUY", "M", "d1"),
    ]
    peers = match_peers(items[0], items, key=lambda x: x[:3], session=lambda x: x[3])
    assert peers == [items[1]]


def test_attribution_reports_effect_per_cause_and_no_causal_claim():
    from ceap.evaluation import tca_attribution_for

    row = tca_attribution_for(get_scenario("T03"))
    assert "WIDE_SPREADS" in row["material"]
    from ceap.analytics.attribution import attribute_causes  # noqa: F401 - API presence

    assert "does not identify a cause" in NO_CAUSAL_CLAIM


# ----------------------------------------------------------------------------- 2T.8
def test_pretrade_model_label_and_shift_coverage_committed():
    from ceap.data.tca_universe import generator_hash
    from ceap.ml.pretrade_cost import default_model

    m = default_model()
    assert m.label == f"fitted to synthetic generator {generator_hash()}"
    cov = m.meta["coverage_shifted"]
    assert set(cov) == set(m.meta["test_families"]) and not set(cov) & set(m.meta["train_families"])
    assert all(0.85 <= v <= 0.95 for v in cov.values()), cov
    assert m.impact_coefficient > 0


def test_pretrade_uses_arrival_features_only():
    import inspect

    from ceap.ml.pretrade_cost import PretradeFeatures

    names = set(inspect.signature(PretradeFeatures).parameters)
    assert names == {"spread_bps", "sigma_daily_bps", "q_over_adv", "algo", "schedule"}


def test_conformal_quantile_and_robust_noise():
    from ceap.ml.pretrade_cost import conformal_quantile, robust_exec_noise

    s = np.arange(1, 101, dtype=float)
    assert conformal_quantile(s, 0.9) == 91.0  # ceil(101 * 0.9) = 91st smallest
    rng = np.random.default_rng(0)
    pr = np.full(20_000, 10.0)
    r = rng.normal(0, math.sqrt(10.0**2 + 4.0**2), 20_000)
    r[:200] += 500.0  # 1 % of huge events must not move the estimate much
    assert robust_exec_noise(r, pr) == pytest.approx(4.0, abs=1.0)


# ------------------------------------------------------- second-reader regressions (D1 to D6)
def test_conformal_quantile_is_unbounded_for_tiny_calibration_sets():
    from ceap.ml.pretrade_cost import conformal_quantile

    assert conformal_quantile(np.arange(1.0, 9.0), 0.9) == float("inf")  # ceil(9 * 0.9) = 9 > 8


def test_partial_window_has_no_pretrade_comparison_and_full_target_normalisation(t01):
    from datetime import timedelta

    half = _window_metrics(t01, end=t01.window_start + timedelta(minutes=30))
    assert math.isnan(half.pretrade_estimate_bps) and math.isnan(half.is_residual_z)
    parts = half.delay_cost_bps + half.spread_cost_bps + half.timing_cost_bps + half.opportunity_cost_bps
    assert abs(parts + half.fees_bps - half.implementation_shortfall_bps) <= 1e-9


def test_unfilled_leg_measured_from_arrival_so_arrival_is_has_no_decision_drift():
    # nothing filled, price unchanged after arrival: arrival shortfall must be exactly zero
    c = shortfall_components(Side.BUY, 100.0, 100.5, [], [], [], 1000, final_price=100.5)
    assert c.arrival_bps == pytest.approx(0.0) and c.delay_bps == pytest.approx(50.0)
