"""Auction (F5), predicted-vs-realised loop (F6) and trading rules (F12)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from ceap.analytics.market_impact import square_root_impact_estimate_bps
from ceap.execution.auction import (
    AuctionOrder,
    auction_capacity,
    auction_impact_bps,
    indicative,
    synthetic_auction_book,
    uncross,
)
from ceap.execution.cost_loop import (
    CostLoop,
    CostObservation,
    drift_report,
    predict_bps,
    recalibrate,
    reliability_table,
)
from ceap.execution.rules import (
    LULDMonitor,
    LULDState,
    Tier,
    lot_type,
    luld_bands,
    on_tick,
    round_lot_size,
    round_to_tick,
    tick_size,
)


# ------------------------------------------------------------------------ auction
def test_uncross_known_answer() -> None:
    orders = [
        AuctionOrder(1, 500),  # MOC buy
        AuctionOrder(1, 300, 10.02),
        AuctionOrder(1, 200, 10.00),
        AuctionOrder(-1, 400, 9.99),
        AuctionOrder(-1, 400, 10.01),
        AuctionOrder(-1, 300, 10.03),
    ]
    # at 10.01: buys 800 (MOC + 10.02), sells 800 -> 800 matched, no imbalance
    price, matched, imb = indicative(orders, reference=10.00)
    assert (price, matched, imb) == (10.01, 800, 0)
    res = uncross(orders, 10.00)
    assert res.filled(0) == 500 and res.filled(1) == 300 and res.filled(2) == 0
    assert res.filled(3) == 400 and res.filled(4) == 400 and res.filled(5) == 0


def test_ties_break_on_imbalance_then_reference() -> None:
    orders = [AuctionOrder(1, 100, 10.05), AuctionOrder(-1, 100, 9.95)]
    price, matched, _ = indicative(orders, reference=10.01)
    assert matched == 100 and price == 10.01  # every price in [9.95, 10.05] clears; reference wins


def test_a_large_moc_buy_moves_the_print_against_us() -> None:
    rng = np.random.default_rng(0)
    book = synthetic_auction_book(rng, 50.0, 200_000)
    small = auction_impact_bps(book, AuctionOrder(1, 1_000), 50.0)
    large = auction_impact_bps(book, AuctionOrder(1, 60_000), 50.0)
    assert large > small >= 0
    assert auction_capacity(1_000_000, 0.10, 0.06) == 6_000


# ---------------------------------------------------------------------- cost loop
def _obs(
    n: int, y: float, delta: float, seed: int = 0, start: date = date(2026, 1, 5)
) -> list[CostObservation]:
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        q, adv, sig = float(rng.uniform(1e3, 2e5)), 1e6, float(rng.uniform(80, 200))
        real = y * sig * (q / adv) ** delta + float(rng.normal(0, 0.5))
        out.append(
            CostObservation(
                start + timedelta(days=i % 60), f"P{i}", q, adv, sig, predict_bps(q, adv, sig), real
            )
        )
    return out


def test_prediction_reuses_the_square_root_law() -> None:
    assert predict_bps(40_000, 1e6, 100.0) == square_root_impact_estimate_bps(40_000, 1e6, 100.0, 0.6)
    assert predict_bps(40_000, 1e6, 100.0, y=1.0, delta=1.0) == pytest.approx(4.0)


def test_recalibration_recovers_y_and_the_exponent() -> None:
    cal = recalibrate(_obs(400, 0.9, 0.6))
    assert cal.delta == pytest.approx(0.6, abs=0.051) and cal.y == pytest.approx(0.9, rel=0.1)


def test_store_round_trip_weekly_job_and_drift_alert(tmp_path) -> None:
    loop = CostLoop(str(tmp_path / "costs.sqlite"))
    for o in _obs(120, 0.9, 0.5):
        loop.record(o)
    assert len(loop.observations()) == 120
    cal, drift = loop.weekly(date(2026, 3, 5))
    assert drift.alert  # the model predicts Y = 0.6 against a true 0.9: biased low
    assert loop.y == pytest.approx(cal.y) and cal.y == pytest.approx(0.9, rel=0.1)
    loop.close()
    unbiased = drift_report(_obs(60, 0.6, 0.5, seed=3))  # z is calibrated: ~5.8 % of seeds alarm at 2
    assert not unbiased.alert


def test_reliability_table_is_monotone_when_the_model_is_right() -> None:
    rows = reliability_table(_obs(500, 0.6, 0.5), bins=5)
    assert len(rows) == 5 and sum(r["n"] for r in rows) == 500
    assert [r["realised_bps"] for r in rows] == sorted(r["realised_bps"] for r in rows)


# -------------------------------------------------------------------------- rules
def test_tick_sizes_and_conservative_rounding() -> None:
    assert tick_size(0.5) == 0.0001 and tick_size(1.0) == 0.01 and tick_size(50, tight_spread=True) == 0.005
    assert round_to_tick(10.019, 1) == 10.01 and round_to_tick(10.011, -1) == 10.02
    assert on_tick(10.01) and not on_tick(10.015) and on_tick(10.015, tight_spread=True)


def test_round_lots_by_price() -> None:
    assert [round_lot_size(p) for p in (100, 250, 251, 1_000, 1_001, 20_000)] == [100, 100, 40, 40, 10, 1]
    assert round_lot_size(5_000, classic=True) == 100
    assert lot_type(50, 100) == "ODD" and lot_type(300, 100) == "ROUND" and lot_type(150, 100) == "MIXED"
    assert lot_type(80, 400) == "ROUND"


def test_luld_bands_and_doubling_windows() -> None:
    assert luld_bands(100.0, 3_600, Tier.TIER_1) == (95.0, 105.0)
    assert luld_bands(100.0, 3_600, Tier.TIER_2) == (90.0, 110.0)
    assert luld_bands(100.0, 60, Tier.TIER_1) == (90.0, 110.0)  # 9:31: doubled
    assert luld_bands(100.0, 6 * 3600 + 10 * 60, Tier.TIER_1) == (90.0, 110.0)  # 15:40: doubled
    assert luld_bands(100.0, 6 * 3600 + 10 * 60, Tier.TIER_1, double_at_close=False) == (95.0, 105.0)
    assert luld_bands(2.0, 3_600) == (1.6, 2.4)
    assert luld_bands(0.5, 3_600) == (0.35, 0.65)  # lesser of $0.15 and 75 %


def test_limit_state_becomes_a_pause_after_fifteen_seconds() -> None:
    m = LULDMonitor()
    for t in range(1_000, 1_300, 10):
        m.on_trade(t, 100.0)
    assert m.on_quote(1_300, 104.0, 104.02) is LULDState.NORMAL
    assert m.on_quote(1_301, 105.0, 105.02) is LULDState.LIMIT
    assert not m.executable(1_302, 105.5, 1) and m.executable(1_302, 104.9, 1)
    assert m.on_quote(1_316, 105.0, 105.02) is LULDState.PAUSED
    assert not m.can_trade(1_500) and m.can_trade(1_617)
    m2 = LULDMonitor()
    m2.on_trade(1_000, 100.0)
    m2.on_quote(1_001, 105.0, 105.1)
    assert m2.on_quote(1_005, 104.0, 104.1) is LULDState.NORMAL  # left the band in time
