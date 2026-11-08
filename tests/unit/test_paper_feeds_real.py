"""RP x R5 shim: the Stooq paper feed builds a point-in-time panel and never reads the R5 holdout."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from ceap.data.real.universe_pit import Event, PointInTimeUniverse
from ceap.paper.feed import get_feed
from ceap.paper.feeds_real import ADV_WINDOW, StooqFeed, parse_stooq_ohlcv


def _universe() -> PointInTimeUniverse:
    return PointInTimeUniverse(
        [
            Event(date(2000, 1, 3), "initial", "AAA"),
            Event(date(2000, 1, 3), "initial", "BRK.B"),
            Event(date(2026, 2, 2), "add", "CCC"),
        ]
    )


def _write(cache: Path, name: str, rows: list[tuple[str, float, float, float]]) -> None:
    d = cache / "stooq"
    d.mkdir(parents=True, exist_ok=True)
    body = "Date,Open,High,Low,Close,Volume\n" + "".join(
        f"{day},{c},{c},{lo},{c},{v}\n" for day, c, lo, v in rows
    )
    (d / name).write_text(body, encoding="utf-8")


def _feed(tmp_path: Path, **kw: object) -> StooqFeed:
    from ceap.data.calendar import trading_days

    days = [str(x) for x in trading_days(date(2026, 1, 2), date(2026, 3, 31))]
    _write(tmp_path, "aaa.us.txt", [(d, 100.0 + i, 99.0 + i, 1000.0 + i) for i, d in enumerate(days)])
    _write(tmp_path, "ccc.us.csv", [(d, 50.0, 49.0, 500.0) for d in days])
    return StooqFeed(cache=tmp_path, end="2026-03-31", universe=_universe(), **kw)  # type: ignore[arg-type]


def test_parse_both_stooq_formats() -> None:
    per_symbol = "Date,Open,High,Low,Close,Volume\n2026-01-02,1,2,0.5,1.5,10\n"
    bulk = "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>\nAAA.US,D,20260102,0,1,2,0.5,1.5,10,0\n"
    assert (
        parse_stooq_ohlcv(per_symbol, "AAA")
        == parse_stooq_ohlcv(bulk, "AAA")
        == {date(2026, 1, 2): (1.5, 0.5, 10.0)}
    )


def test_panel_shape_membership_adv_and_spread(tmp_path: Path) -> None:
    p = _feed(tmp_path).panel()
    assert p.name == "stooq" and p.symbols == ("AAA", "BRK.B", "CCC") and set(p.tiers) == {"A"}
    assert str(p.dates[0]) == "2026-01-02"
    a, b, c = (p.col(s) for s in ("AAA", "BRK.B", "CCC"))
    assert p.meta["missing_files"] == ["BRK.B"] and np.isnan(p.close[:, b]).all()
    assert not p.members[p.index_of("2026-01-30"), c] and p.members[p.index_of("2026-02-02"), c]
    assert p.close[0, a] == 100.0 and p.low[0, a] == 99.0
    assert np.isnan(p.adv_20[ADV_WINDOW - 1, a])
    # lagged one day: row 20 uses rows 0..19 only
    assert p.adv_20[ADV_WINDOW, a] == pytest.approx(np.mean(1000.0 + np.arange(ADV_WINDOW)))
    assert np.all(p.spread_bps == 10.0)  # 2 x 5 bps half-spread after 2009
    assert np.array_equal(p.adjusted_close(5), p.close[:6], equal_nan=True)


def test_refuses_the_unspent_r5_holdout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="holdout"):
        StooqFeed(cache=tmp_path, start="2025-12-31", universe=_universe())


def test_rows_inside_the_holdout_are_never_loaded(tmp_path: Path) -> None:
    _write(tmp_path, "aaa.us.txt", [("2025-12-31", 1.0, 1.0, 1.0), ("2026-01-02", 2.0, 2.0, 2.0)])
    p = StooqFeed(cache=tmp_path, end="2026-01-05", universe=_universe()).panel()
    assert p.dates[0] >= np.datetime64("2026-01-01") and p.close[0, p.col("AAA")] == 2.0


def test_registered_through_get_feed(tmp_path: Path) -> None:
    feed = get_feed(
        "stooq", scenario="R01", forward_start=None, cache=tmp_path, end="2026-01-05", universe=_universe()
    )
    assert isinstance(feed, StooqFeed)
