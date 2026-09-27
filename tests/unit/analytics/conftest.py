"""Shared fixtures/builders for per-metric analytics tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ceap.domain.execution import ExecutionMetrics, Side
from ceap.domain.market import MarketStatistics, Quote

T0 = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)


def make_quote(bid, ask, bs=100, as_=100, s=0) -> Quote:
    return Quote("AAPL", T0 + timedelta(seconds=s), bid, bs, ask, as_)


def make_metrics(**over) -> ExecutionMetrics:
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


def make_market(**over) -> MarketStatistics:
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
