"""Deterministic analytics: known-answer tests."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from ceap.analytics.attribution import Cause, Thresholds, attribute_causes
from ceap.analytics.implementation_shortfall import execution_cost_bps, implementation_shortfall_bps
from ceap.analytics.liquidity import (
    average_spread_bps,
    crossed_quote_count,
    participation_rate,
    stale_quote_fraction,
)
from ceap.analytics.market_impact import (
    market_impact_bps,
    square_root_impact_estimate_bps,
    temporary_impact_bps,
)
from ceap.analytics.slippage import effective_spread_bps, per_fill_slippage_bps, slippage_bps
from ceap.analytics.twap import twap
from ceap.analytics.volatility import (
    annualise_volatility,
    max_abs_move_bps,
    price_drift_bps,
    realised_volatility_bps,
)
from ceap.analytics.vwap import vwap, vwap_by_bucket
from ceap.domain.execution import ExecutionMetrics, Side, VenueStatistics
from ceap.domain.market import MarketStatistics, Quote

T0 = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)


# ---------------------------------------------------------------- VWAP / TWAP
def test_vwap_weights_by_quantity():
    assert vwap([10.0, 20.0], [1, 3]) == pytest.approx(17.5)


def test_vwap_empty_and_zero_quantity_are_nan():
    assert math.isnan(vwap([], []))
    assert math.isnan(vwap([1.0], [0]))


def test_vwap_shape_mismatch_raises():
    with pytest.raises(ValueError):
        vwap([1.0, 2.0], [1])


def test_vwap_by_bucket_groups_by_minute():
    ts = np.array(
        [(T0 + timedelta(seconds=s)).replace(tzinfo=None) for s in (0, 30, 60, 90)], dtype="datetime64[s]"
    )
    out = vwap_by_bucket(ts, np.array([1.0, 3.0, 5.0, 7.0]), np.array([1, 1, 1, 1]), 60)
    assert len(out) == 2
    assert out[0][1] == pytest.approx(2.0) and out[1][1] == pytest.approx(6.0)


def test_twap_simple_mean_without_timestamps():
    assert twap([1.0, 2.0, 3.0]) == pytest.approx(2.0)


def test_twap_time_weighted():
    prices = [10.0, 20.0, 30.0]
    stamps = [T0, T0 + timedelta(seconds=10), T0 + timedelta(seconds=40)]
    # weights: 10s, 30s, median gap 20s  -> (100 + 600 + 600) / 60
    assert twap(prices, stamps) == pytest.approx(1300 / 60)


# ------------------------------------------------------ implementation shortfall
def test_is_buy_positive_when_paying_up():
    # bought 100 @ 101 vs decision 100 -> +100 bps
    assert implementation_shortfall_bps(Side.BUY, 100.0, [101.0], [100], 100) == pytest.approx(100.0)


def test_is_sell_sign_flips():
    assert implementation_shortfall_bps(Side.SELL, 100.0, [99.0], [100], 100) == pytest.approx(100.0)
    assert implementation_shortfall_bps(Side.SELL, 100.0, [101.0], [100], 100) == pytest.approx(-100.0)


def test_is_includes_opportunity_cost_for_unfilled():
    # filled 50 at decision price; 50 unfilled while price rose 2% -> 0.5 * 200 bps = 100 bps
    assert implementation_shortfall_bps(
        Side.BUY, 100.0, [100.0], [50], 100, final_price=102.0
    ) == pytest.approx(100.0)
    assert implementation_shortfall_bps(Side.BUY, 100.0, [100.0], [50], 100) == pytest.approx(0.0)


def test_is_invalid_inputs_nan():
    assert math.isnan(implementation_shortfall_bps(Side.BUY, 0.0, [1.0], [1], 1))
    assert math.isnan(execution_cost_bps(Side.BUY, 100.0, float("nan")))


# --------------------------------------------------------------------- slippage
def test_slippage_signed_by_side():
    assert slippage_bps(Side.BUY, 100.1, 100.0) == pytest.approx(10.0)
    assert slippage_bps(Side.SELL, 100.1, 100.0) == pytest.approx(-10.0)


def test_per_fill_slippage_vectorised_and_nan_safe():
    out = per_fill_slippage_bps(Side.BUY, [101.0, 99.0], [100.0, 0.0])
    assert out[0] == pytest.approx(100.0)
    assert math.isnan(out[1])


def test_effective_spread_is_twice_distance_to_mid():
    assert effective_spread_bps([100.05], [100.0], Side.BUY) == pytest.approx(10.0)


# ------------------------------------------------------------------- volatility
def test_realised_volatility_scales_with_horizon():
    rng = np.random.default_rng(0)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 1e-4, 5000)))
    per_min = realised_volatility_bps(prices, 1.0, 60.0)
    per_sec = realised_volatility_bps(prices, 1.0, 1.0)
    assert per_min == pytest.approx(per_sec * math.sqrt(60), rel=1e-9)
    assert per_sec == pytest.approx(1.0, rel=0.1)  # 1e-4 = 1 bp per second


def test_annualised_volatility_reasonable():
    rng = np.random.default_rng(1)
    sigma = 0.20 / math.sqrt(252 * 23400)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, sigma, 20000)))
    assert annualise_volatility(prices, 1.0) == pytest.approx(0.20, rel=0.1)


def test_drift_and_jump_detector():
    prices = [100.0] * 60 + [101.0] * 60
    assert price_drift_bps(prices) == pytest.approx(100.0)
    assert max_abs_move_bps(prices, window=1) == pytest.approx(100.0)
    assert math.isnan(realised_volatility_bps([100.0]))


# -------------------------------------------------------------------- liquidity
def _q(bid, ask, bs=100, as_=100, s=0) -> Quote:
    return Quote("AAPL", T0 + timedelta(seconds=s), bid, bs, ask, as_)


def test_spread_bps_and_crossed_quotes_excluded():
    quotes = [_q(99.99, 100.01), _q(100.02, 100.00)]  # second is crossed
    assert average_spread_bps(quotes) == pytest.approx(2.0, rel=1e-3)
    assert crossed_quote_count(quotes) == 1


def test_stale_quote_fraction():
    quotes = [_q(99.99, 100.01, s=i) for i in range(5)]  # identical
    assert stale_quote_fraction(quotes) == pytest.approx(1.0)
    quotes = [_q(99.99, 100.01, bs=100 + i, s=i) for i in range(5)]
    assert stale_quote_fraction(quotes) == pytest.approx(0.0)
    assert stale_quote_fraction([]) == 0.0


def test_participation_rate():
    assert participation_rate(50, 1000) == pytest.approx(0.05)
    assert math.isnan(participation_rate(50, 0))


# ---------------------------------------------------------------- market impact
def test_market_impact_decomposition():
    assert market_impact_bps(Side.BUY, 100.0, 100.5) == pytest.approx(50.0)
    assert temporary_impact_bps(Side.BUY, 100.6, 100.5) == pytest.approx(9.95, rel=1e-2)
    assert square_root_impact_estimate_bps(0.04, 100.0, 0.6) == pytest.approx(12.0)


# ------------------------------------------------------------------ attribution
def _metrics(**over) -> ExecutionMetrics:
    base = dict(
        symbol="AAPL",
        start=T0,
        end=T0 + timedelta(hours=1),
        side=Side.BUY,
        target_quantity=60_000,
        executed_quantity=54_000,
        fill_rate=0.9,
        participation_rate=0.05,
        vwap=230.5,
        market_vwap=230.48,
        arrival_price=230.4,
        implementation_shortfall_bps=5.0,
        slippage_vs_arrival_bps=4.0,
        slippage_vs_vwap_bps=1.0,
        average_spread_bps=1.8,
        effective_spread_bps=2.0,
        market_impact_bps=3.0,
        temporary_impact_bps=0.5,
        price_drift_bps=2.0,
        realised_volatility_bps=3.4,
        average_latency_us=800.0,
        reject_rate=0.0,
        execution_count=200,
        child_order_count=120,
        venue_statistics={},
    )
    base.update(over)
    return ExecutionMetrics(**base)


def _market(**over) -> MarketStatistics:
    base = dict(
        symbol="AAPL",
        start=T0,
        end=T0 + timedelta(hours=1),
        open_mid=230.0,
        close_mid=230.1,
        high=231,
        low=229,
        vwap=230.1,
        traded_volume=800_000,
        trade_count=5000,
        average_spread_bps=1.8,
        realised_volatility_bps=3.4,
        annualised_volatility=0.1,
        average_displayed_depth=5000.0,
        average_top_of_book_size=450.0,
        price_drift_bps=4.0,
        stale_quote_fraction=0.001,
        crossed_quote_count=0,
        quote_count=3600,
    )
    base.update(over)
    return MarketStatistics(**base)


def test_attribution_normal_when_nothing_moves():
    res = attribute_causes(_metrics(), _metrics(), _market(), _market())
    assert res.primary is Cause.NORMAL
    assert res.material == ()
    assert not res.deteriorated


def test_attribution_volatility():
    res = attribute_causes(
        _metrics(implementation_shortfall_bps=12.0),
        _metrics(),
        _market(realised_volatility_bps=10.0),
        _market(),
    )
    assert res.primary is Cause.MARKET_VOLATILITY
    assert res.deteriorated


def test_attribution_wide_spreads_and_low_liquidity_both_material():
    res = attribute_causes(
        _metrics(),
        _metrics(),
        _market(average_spread_bps=5.0, average_displayed_depth=1200.0, average_top_of_book_size=120.0),
        _market(),
    )
    causes = {c.cause for c in res.material}
    assert {Cause.WIDE_SPREADS, Cause.LOW_LIQUIDITY} <= causes


def test_attribution_technology_from_engineering_signal():
    res = attribute_causes(_metrics(), _metrics(), _market(), _market(), engineering={"latency_ratio": 9.0})
    assert res.primary is Cause.TECHNOLOGY_LATENCY


def test_attribution_platform_wide_rejects_not_blamed_on_venue():
    venues = {
        v: VenueStatistics(v, 20, 18, 10_000, 9_000, 0.9, 1.5, 8000.0, r, r / 20, 0.01)
        for v, r in (("XNAS", 1), ("ARCA", 2), ("BATS", 5), ("IEX", 1), ("DARK1", 1))
    }
    res = attribute_causes(
        _metrics(reject_rate=0.10, average_latency_us=8000.0, venue_statistics=venues),
        _metrics(),
        _market(),
        _market(),
    )
    assert res.primary is Cause.TECHNOLOGY_LATENCY
    assert Cause.VENUE_DEGRADATION not in {c.cause for c in res.material}


def test_attribution_venue_degradation_when_one_venue_lags():
    venues = {
        "XNAS": VenueStatistics("XNAS", 40, 36, 20_000, 18_000, 0.90, 1.5, 800.0, 0, 0.0, 0.02),
        "ARCA": VenueStatistics("ARCA", 25, 10, 12_000, 5_000, 0.42, 6.0, 800.0, 0, 0.0, 0.01),
        "BATS": VenueStatistics("BATS", 20, 18, 10_000, 9_000, 0.90, 1.6, 800.0, 0, 0.0, 0.01),
    }
    res = attribute_causes(_metrics(venue_statistics=venues), _metrics(), _market(), _market())
    assert res.primary is Cause.VENUE_DEGRADATION
    assert res.ranked[0].metrics["venue"] == "ARCA"


def test_attribution_price_move_halved_under_high_vol():
    calm = attribute_causes(_metrics(), _metrics(), _market(price_drift_bps=200.0), _market())
    stormy = attribute_causes(
        _metrics(), _metrics(), _market(price_drift_bps=200.0, realised_volatility_bps=12.0), _market()
    )
    calm_score = next(c.score for c in calm.ranked if c.cause is Cause.PRICE_MOVEMENT)
    stormy_score = next(c.score for c in stormy.ranked if c.cause is Cause.PRICE_MOVEMENT)
    assert calm.primary is Cause.PRICE_MOVEMENT
    assert stormy_score == pytest.approx(calm_score / 2)


def test_attribution_large_order():
    res = attribute_causes(
        _metrics(participation_rate=0.4, target_quantity=600_000), _metrics(), _market(), _market()
    )
    assert res.primary is Cause.LARGE_ORDER_IMPACT
    assert res.ranked[0].score == 1.0


def test_attribution_market_data_anomaly():
    res = attribute_causes(
        _metrics(), _metrics(), _market(stale_quote_fraction=0.15, crossed_quote_count=20), _market()
    )
    assert res.primary is Cause.MARKET_DATA_ANOMALY


def test_attribution_thresholds_are_tunable():
    strict = Thresholds(volatility_ratio=5.0)
    res = attribute_causes(
        _metrics(), _metrics(), _market(realised_volatility_bps=10.0), _market(), thresholds=strict
    )
    assert res.primary is Cause.NORMAL


def test_attribution_without_baseline():
    res = attribute_causes(_metrics(), None, _market(), None)
    assert res.primary is Cause.NORMAL
    assert res.to_dict()["primary"] == "NORMAL"
