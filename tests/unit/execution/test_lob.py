"""Limit order book (F1): queue-priority known answers, latency, and the trade-through property."""

from __future__ import annotations

import numpy as np
import pytest

from ceap.execution.lob import (
    AGENT,
    BACKGROUND,
    BUY,
    SELL,
    LOBSimulator,
    OrderBook,
    RestingOrder,
    SimulatorParams,
)


def _book() -> OrderBook:
    b = OrderBook()
    b.rest(RestingOrder(1, "a", SELL, 101, 100))
    b.rest(RestingOrder(2, "b", SELL, 101, 200))
    b.rest(RestingOrder(3, "c", SELL, 102, 300))
    b.rest(RestingOrder(4, "d", BUY, 99, 100))
    return b


def test_fifo_within_a_level_then_price_priority() -> None:
    b = _book()
    fills, tape, left = b.match(BUY, 350, None, 10, 99, "taker")
    passive = [(f.order_id, f.quantity, f.price) for f in fills if f.liquidity == "ADD"]
    assert passive == [(1, 100, 101), (2, 200, 101), (3, 50, 102)]
    assert left == 0 and sum(p.quantity for p in tape) == 350
    assert b.best(SELL) == 102 and b.displayed(SELL, 102) == 250


def test_limit_stops_the_sweep_and_reports_the_rest() -> None:
    b = _book()
    _, _, left = b.match(BUY, 500, 101, 10, 99, "taker")
    assert left == 200 and b.best(SELL) == 102


def test_queue_position_is_the_displayed_size_ahead() -> None:
    b = _book()
    b.rest(RestingOrder(5, AGENT, SELL, 101, 50))
    assert b.queue_ahead(1) == 0 and b.queue_ahead(2) == 100 and b.queue_ahead(5) == 300
    b.match(BUY, 120, None, 10, 99, "taker")  # order 1 gone, 20 off order 2
    assert b.queue_ahead(5) == 180


def test_iceberg_refill_goes_to_the_back_of_the_queue() -> None:
    b = OrderBook()
    b.rest(RestingOrder(1, "ice", SELL, 101, 100, hidden=200, peak=100))
    b.rest(RestingOrder(2, "lit", SELL, 101, 100))
    assert b.displayed(SELL, 101) == 200  # hidden size is not displayed
    fills, _, _ = b.match(BUY, 150, None, 5, 99, "taker")
    assert [(f.order_id, f.quantity) for f in fills if f.liquidity == "ADD"] == [(1, 100), (2, 50)]
    assert b.queue_ahead(1) == 50  # the refill queues behind what was there
    assert b.get(1).visible == 100 and b.get(1).hidden == 100


def test_resting_order_may_not_cross_and_cancel_cleans_levels() -> None:
    b = _book()
    with pytest.raises(ValueError):
        b.rest(RestingOrder(9, "x", BUY, 101, 10))
    assert b.cancel(4) is not None and b.best(BUY) is None and b.cancel(4) is None


def test_order_entry_latency_and_market_data_latency() -> None:
    sim = LOBSimulator(SimulatorParams(entry_latency_ms=5, md_latency_ms=3), seed=1)
    sim.run_until(1_000)
    bid = sim.view().bid
    oid = sim.submit(BUY, 100, limit=bid - 2)
    sim.run_until(1_004)
    assert sim.book.get(oid) is None  # still in flight
    sim.run_until(1_005)
    assert sim.book.get(oid) is not None
    assert sim.view().ts_ms <= sim.now_ms - 3


def test_simulator_is_deterministic_in_its_seed() -> None:
    def run(seed: int) -> list[tuple[int, int, int]]:
        sim = LOBSimulator(seed=seed)
        sim.run_until(20_000)
        return [(p.ts_ms, p.price, p.quantity) for p in sim.tape]

    assert run(4) == run(4) and run(4) != run(5)


@pytest.mark.parametrize("seed", range(6))
def test_passive_fills_only_when_traded_through(seed: int) -> None:
    """Property: an agent's passive fill is a background taker trading at the agent's own limit,
    never at a better price, and only after the queue ahead of it is gone."""
    rng = np.random.default_rng(seed)
    sim = LOBSimulator(seed=seed)
    limits: dict[int, int] = {}
    for t in range(0, 120_000, 1_500):
        sim.run_until(t)
        v = sim.view()
        if v.bid is None or v.ask is None:
            continue
        side = BUY if rng.random() < 0.5 else SELL
        px = (v.bid - int(rng.integers(0, 2))) if side == BUY else (v.ask + int(rng.integers(0, 2)))
        limits[sim.submit(side, int(rng.integers(50, 300)), limit=px)] = px
    sim.run_until(125_000)
    passive = [f for f in sim.agent_fills if f.liquidity == "ADD"]
    assert passive, "the property needs passive fills to test"
    prints = {
        (p.ts_ms, p.price) for p in sim.tape if p.passive_owner == AGENT and p.aggressor_owner == BACKGROUND
    }
    for f in passive:
        assert f.price == limits[f.order_id]  # no price improvement for a resting order
        assert (f.ts_ms, f.price) in prints  # a background taker traded through us
    for f in sim.agent_fills:
        if f.liquidity == "REMOVE":
            assert (f.price - limits[f.order_id]) * f.side <= 0  # an aggressive fill respects its limit
