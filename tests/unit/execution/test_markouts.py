"""Markout curves and routing table (F7): known answers, CIs and price-scale invariance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ceap.domain.execution import Execution, Side
from ceap.domain.market import Quote
from ceap.execution.markouts import CLOSE, markout_curves, routing_table

T0 = datetime(2026, 9, 18, 14, 0, tzinfo=UTC)


def _quotes(scale: float = 1.0) -> list[Quote]:
    # mid 100.00 until t=10 s, 100.10 until 70 s, then 99.90
    out = []
    for s in range(0, 400):
        mid = 100.0 if s < 10 else (100.10 if s < 70 else 99.90)
        out.append(
            Quote("AAPL", T0 + timedelta(seconds=s), (mid - 0.01) * scale, 100, (mid + 0.01) * scale, 100)
        )
    return out


def _fills(scale: float = 1.0) -> list[Execution]:
    out = []
    for i in range(40):
        venue = "XNAS" if i % 2 == 0 else "DARK1"
        flag = "ADD" if i % 4 < 2 else "REMOVE"
        out.append(
            Execution(
                f"E{i}",
                "C",
                "AAPL",
                Side.BUY,
                100 + i,
                100.0 * scale,
                venue,
                T0 + timedelta(seconds=5),
                liquidity_flag=flag,
            )
        )
    return out


def test_known_markouts_for_a_buy() -> None:
    pts = markout_curves(_fills(), _quotes(), horizons=(1, 30, 300, CLOSE), by=(), n_boot=200)
    by_h = {p.horizon: p for p in pts}
    assert by_h[1].mean_bps == pytest.approx(0.0, abs=1e-9)  # t=6 s: mid 100.00
    assert by_h[30].mean_bps == pytest.approx(10.0, rel=1e-9)  # t=35 s: mid 100.10, price moved our way
    assert by_h[300].mean_bps == pytest.approx(-10.0, rel=1e-9)  # t=305 s: mid 99.90
    assert by_h[CLOSE].n == 40


def test_groups_by_venue_and_flag_with_cis() -> None:
    pts = markout_curves(_fills(), _quotes(), horizons=(30,), n_boot=200)
    assert {p.group for p in pts} == {
        ("XNAS", "ADD"),
        ("XNAS", "REMOVE"),
        ("DARK1", "ADD"),
        ("DARK1", "REMOVE"),
    }
    for p in pts:
        assert p.n == 10 and p.ci_low_bps <= p.mean_bps <= p.ci_high_bps


def test_horizon_after_the_last_quote_is_skipped() -> None:
    pts = markout_curves(_fills(), _quotes(), horizons=(10_000,), by=())
    assert pts[0].n == 0


def test_markouts_are_invariant_to_price_scale() -> None:
    a = markout_curves(_fills(1.0), _quotes(1.0), horizons=(30, 300), by=("venue",), n_boot=100)
    b = markout_curves(_fills(25.0), _quotes(25.0), horizons=(30, 300), by=("venue",), n_boot=100)
    for x, y in zip(a, b, strict=True):
        assert y.mean_bps == pytest.approx(x.mean_bps, rel=1e-9)
        assert y.ci_low_bps == pytest.approx(x.ci_low_bps, rel=1e-9)


def test_routing_table_flags_adverse_venues() -> None:
    fills = _fills()
    # DARK1 fills arrive just before the drop: adverse at 60 s
    fills = [
        e
        if e.venue == "XNAS"
        else Execution(
            e.execution_id,
            "C",
            "AAPL",
            Side.BUY,
            e.quantity,
            100.10,
            "DARK1",
            T0 + timedelta(seconds=65),
            liquidity_flag="ADD",
        )
        for e in fills
    ]
    table = routing_table(fills, _quotes(), horizon=60, n_boot=200)
    rows = {r.venue: r for r in table}
    assert rows["DARK1"].verdict == "adverse" and rows["XNAS"].verdict in ("favourable", "indistinguishable")
    assert sum(r.suggested_weight for r in table) == pytest.approx(1.0)
    assert rows["DARK1"].suggested_weight < rows["DARK1"].fill_share


def test_close_markout_uses_each_fills_own_session() -> None:
    day2 = [Quote("AAPL", q.timestamp + timedelta(days=1), q.bid + 5, 100, q.ask + 5, 100) for q in _quotes()]
    fills = _fills()[:2]
    pts = markout_curves(fills, _quotes() + day2, horizons=(CLOSE,), by=(), n_boot=50)
    assert pts[0].mean_bps == pytest.approx(-10.0, rel=1e-9)  # day-1 close 99.90, not day 2's 104.90
