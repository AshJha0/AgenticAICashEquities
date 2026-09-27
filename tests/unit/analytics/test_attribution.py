"""Cause attribution: known-answer tests."""

from __future__ import annotations

import pytest

from ceap.analytics.attribution import Cause, Thresholds, attribute_causes
from ceap.domain.execution import VenueStatistics

from .conftest import make_market, make_metrics


def test_attribution_normal_when_nothing_moves():
    res = attribute_causes(make_metrics(), make_metrics(), make_market(), make_market())
    assert res.primary is Cause.NORMAL
    assert res.material == ()
    assert not res.deteriorated


def test_attribution_volatility():
    res = attribute_causes(
        make_metrics(implementation_shortfall_bps=12.0),
        make_metrics(),
        make_market(realised_volatility_bps=10.0),
        make_market(),
    )
    assert res.primary is Cause.MARKET_VOLATILITY
    assert res.deteriorated


def test_attribution_wide_spreads_and_low_liquidity_both_material():
    res = attribute_causes(
        make_metrics(),
        make_metrics(),
        make_market(average_spread_bps=5.0, average_displayed_depth=1200.0, average_top_of_book_size=120.0),
        make_market(),
    )
    causes = {c.cause for c in res.material}
    assert {Cause.WIDE_SPREADS, Cause.LOW_LIQUIDITY} <= causes


def test_attribution_technology_from_engineering_signal():
    res = attribute_causes(
        make_metrics(), make_metrics(), make_market(), make_market(), engineering={"latency_ratio": 9.0}
    )
    assert res.primary is Cause.TECHNOLOGY_LATENCY


def test_attribution_platform_wide_rejects_not_blamed_on_venue():
    venues = {
        v: VenueStatistics(v, 20, 18, 10_000, 9_000, 0.9, 1.5, 8000.0, r, r / 20, 0.01)
        for v, r in (("XNAS", 1), ("ARCA", 2), ("BATS", 5), ("IEX", 1), ("DARK1", 1))
    }
    res = attribute_causes(
        make_metrics(reject_rate=0.10, average_latency_us=8000.0, venue_statistics=venues),
        make_metrics(),
        make_market(),
        make_market(),
    )
    assert res.primary is Cause.TECHNOLOGY_LATENCY
    assert Cause.VENUE_DEGRADATION not in {c.cause for c in res.material}


def test_attribution_venue_degradation_when_one_venue_lags():
    venues = {
        "XNAS": VenueStatistics("XNAS", 40, 36, 20_000, 18_000, 0.90, 1.5, 800.0, 0, 0.0, 0.02),
        "ARCA": VenueStatistics("ARCA", 25, 10, 12_000, 5_000, 0.42, 6.0, 800.0, 0, 0.0, 0.01),
        "BATS": VenueStatistics("BATS", 20, 18, 10_000, 9_000, 0.90, 1.6, 800.0, 0, 0.0, 0.01),
    }
    res = attribute_causes(make_metrics(venue_statistics=venues), make_metrics(), make_market(), make_market())
    assert res.primary is Cause.VENUE_DEGRADATION
    assert res.ranked[0].metrics["venue"] == "ARCA"


def test_attribution_price_move_halved_under_high_vol():
    calm = attribute_causes(make_metrics(), make_metrics(), make_market(price_drift_bps=200.0), make_market())
    stormy = attribute_causes(
        make_metrics(),
        make_metrics(),
        make_market(price_drift_bps=200.0, realised_volatility_bps=12.0),
        make_market(),
    )
    calm_score = next(c.score for c in calm.ranked if c.cause is Cause.PRICE_MOVEMENT)
    stormy_score = next(c.score for c in stormy.ranked if c.cause is Cause.PRICE_MOVEMENT)
    assert calm.primary is Cause.PRICE_MOVEMENT
    assert stormy_score == pytest.approx(calm_score / 2)


def test_attribution_large_order():
    res = attribute_causes(
        make_metrics(participation_rate=0.4, target_quantity=600_000), make_metrics(), make_market(), make_market()
    )
    assert res.primary is Cause.LARGE_ORDER_IMPACT
    assert res.ranked[0].score == 1.0


def test_attribution_market_data_anomaly():
    res = attribute_causes(
        make_metrics(), make_metrics(), make_market(stale_quote_fraction=0.15, crossed_quote_count=20), make_market()
    )
    assert res.primary is Cause.MARKET_DATA_ANOMALY


def test_attribution_thresholds_are_tunable():
    strict = Thresholds(volatility_ratio=5.0)
    res = attribute_causes(
        make_metrics(), make_metrics(), make_market(realised_volatility_bps=10.0), make_market(), thresholds=strict
    )
    assert res.primary is Cause.NORMAL


def test_attribution_without_baseline():
    res = attribute_causes(make_metrics(), None, make_market(), None)
    assert res.primary is Cause.NORMAL
    assert res.to_dict()["primary"] == "NORMAL"
