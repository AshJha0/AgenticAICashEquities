"""Synthetic data -> repositories -> analytics, end to end (no agents)."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from ceap.analytics.execution_metrics import StandardExecutionAnalytics
from ceap.analytics.market_statistics import calculate_market_statistics
from ceap.data.export import dataset_frames, export_dataset
from ceap.data.repositories import DatasetStore, InMemoryExecutionRepository, InMemoryMarketDataRepository
from ceap.data.scenarios import SCENARIO_TEMPLATES, all_scenarios, get_scenario, scenario_for
from ceap.data.synthetic import SyntheticMarketGenerator
from ceap.domain.execution import OrderStatus


def test_scenario_catalogue():
    assert len(SCENARIO_TEMPLATES) == 10
    scenarios = all_scenarios()
    assert len(scenarios) == 50 and len({s.id for s in scenarios}) == 50
    assert len({(s.symbol, s.seed) for s in scenarios}) == 50
    assert get_scenario("technology_latency").id == "T06" and get_scenario("S37").symbol == "MSFT"
    assert scenario_for("T02", "GOOGL").symbol == "GOOGL"
    with pytest.raises(KeyError):
        get_scenario("nope")


def test_generation_is_deterministic():
    gen = SyntheticMarketGenerator()
    a, b = gen.generate(get_scenario("T01")), gen.generate(get_scenario("T01"))
    assert a.summary() == b.summary()
    assert [q.bid for q in a.quotes[:50]] == [q.bid for q in b.quotes[:50]]
    assert [e.price for e in a.executions] == [e.price for e in b.executions]


def test_dataset_shape(dataset_t01):
    ds = dataset_t01
    assert len(ds.quotes) == 4 * 3600 and len(ds.order_books) == 4 * 360
    assert len(ds.parent_orders) == 2 and all(o.strategy == "VWAP" for o in ds.parent_orders)
    assert all(q.ask > q.bid for q in ds.quotes)  # no crossed quotes in the normal scenario
    assert all(o.status is not OrderStatus.REJECTED for o in ds.orders)
    assert all(e.latency_us and e.latency_us > 0 for e in ds.executions)
    parents = {o.order_id for o in ds.parent_orders}
    assert all(e.parent_order_id in parents for e in ds.executions)
    assert sorted(ds.quotes, key=lambda q: q.timestamp) == ds.quotes


def test_scenario_effects_are_localised_to_window(store):
    ds = store.get(get_scenario("T06"))
    win = [o for o in ds.orders if not o.is_parent and ds.window_start <= o.timestamp < ds.window_end]
    base = [o for o in ds.orders if not o.is_parent and ds.baseline_start <= o.timestamp < ds.baseline_end]
    assert any(o.status is OrderStatus.REJECTED for o in win)
    assert not any(o.status is OrderStatus.REJECTED for o in base)
    lat_w = [e.latency_us for e in ds.executions if ds.window_start <= e.timestamp < ds.window_end]
    lat_b = [e.latency_us for e in ds.executions if ds.baseline_start <= e.timestamp < ds.baseline_end]
    assert sum(lat_w) / len(lat_w) > 5 * sum(lat_b) / len(lat_b)
    assert any(d.service == "smart-order-router" for d in ds.deployments)
    assert any(log.level == "ERROR" for log in ds.logs)


def test_market_data_anomaly_and_liquidity_scenarios(store):
    md = store.get(get_scenario("T07"))
    stats = calculate_market_statistics(
        "AAPL",
        md.window_start,
        md.window_end,
        [q for q in md.quotes if md.window_start <= q.timestamp < md.window_end],
        [],
        [],
    )
    assert stats.stale_quote_fraction > 0.1 and stats.crossed_quote_count == 20
    liq = store.get(get_scenario("T04"))
    books_w = [b for b in liq.order_books if liq.window_start <= b.timestamp < liq.window_end]
    books_b = [b for b in liq.order_books if liq.baseline_start <= b.timestamp < liq.baseline_end]
    depth = lambda bs: sum(b.displayed_depth() for b in bs) / len(bs)  # noqa: E731
    assert depth(books_w) < 0.45 * depth(books_b)


async def test_repositories_slice_by_time(dataset_t01):
    mr, er = InMemoryMarketDataRepository(dataset_t01), InMemoryExecutionRepository(dataset_t01)
    ds = dataset_t01
    quotes = await mr.quotes("AAPL", ds.window_start, ds.window_end)
    assert (
        len(quotes) == 3600
        and quotes[0].timestamp >= ds.window_start
        and quotes[-1].timestamp < ds.window_end
    )
    assert await mr.quotes("MSFT", ds.window_start, ds.window_end) == []
    assert await mr.symbols() == ["AAPL"]
    orders = await er.orders("AAPL", ds.window_start, ds.window_end)
    assert sum(1 for o in orders if o.is_parent) == 1
    books = await mr.order_books("AAPL", ds.window_start, ds.window_end)
    assert len(books) == 360
    assert len(await er.executions("AAPL", ds.window_start, ds.window_end)) > 100


async def test_standard_analytics_on_generated_data(dataset_t01):
    ds = dataset_t01
    mr, er = InMemoryMarketDataRepository(ds), InMemoryExecutionRepository(ds)
    s, e = ds.window_start, ds.window_end
    m = StandardExecutionAnalytics().calculate(
        await er.orders("AAPL", s, e),
        await er.executions("AAPL", s, e),
        await mr.quotes("AAPL", s, e),
        await mr.trades("AAPL", s, e),
        s,
        e,
    )
    assert m.symbol == "AAPL" and m.target_quantity == 60_000
    assert 0.5 < m.fill_rate <= 1.0 and 0.0 < m.participation_rate < 0.15
    assert math.isfinite(m.implementation_shortfall_bps) and math.isfinite(m.slippage_vs_vwap_bps)
    assert 0 < m.average_spread_bps < 5 and m.effective_spread_bps > 0
    assert abs(m.vwap - m.market_vwap) / m.market_vwap < 0.002
    assert m.arrival_price == pytest.approx(
        next(o.decision_price for o in ds.parent_orders if o.timestamp == s)
    )
    assert set(m.venue_statistics) == {"XNAS", "ARCA", "BATS", "IEX", "DARK1"}
    assert all(0 <= v.fill_rate <= 1 for v in m.venue_statistics.values())
    assert m.reject_rate == 0.0 and 500 < m.average_latency_us < 1500


async def test_analytics_with_no_orders_is_nan_safe():
    from datetime import UTC, datetime

    s = datetime(2026, 9, 18, 13, tzinfo=UTC)
    m = StandardExecutionAnalytics().calculate([], [], [], [], s, s.replace(hour=14))
    assert m.symbol == "?" and math.isnan(m.vwap) and m.execution_count == 0


def test_dataset_store_caches_and_clears(store):
    a = store.get("T01")
    assert store.get(get_scenario("T01")) is a
    store2 = DatasetStore()
    b = store2.get(replace(get_scenario("T01"), seed=999))
    assert b.scenario.seed == 999
    store2.clear()


def test_export_csv(tmp_path, dataset_t01):
    frames = dataset_frames(dataset_t01)
    assert set(frames) >= {"market/quotes", "orders/orders", "executions/executions"}
    written = export_dataset(dataset_t01, tmp_path, fmt="csv")
    assert len(written) == 8 and all(p.exists() and p.stat().st_size > 0 for p in written)
