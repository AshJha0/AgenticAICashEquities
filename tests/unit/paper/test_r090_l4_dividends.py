"""F13 data (0.9.0): dividend source, cache, per-series hash and revision record. Fixtures only, no network."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import numpy as np

from ceap.paper.config import PaperConfig
from ceap.paper.dividends import REVISIONS, attach_dividends, read_cached, refresh, series_hash
from ceap.paper.feed import MarketPanel


class FixtureSource:
    name = "fixture"

    def __init__(self, data: dict[str, list[tuple[date, float]]]) -> None:
        self.data = data

    def fetch(self, symbol: str) -> list[tuple[date, float]]:
        return list(self.data.get(symbol, []))


def _panel() -> MarketPanel:
    dates = np.array(["2026-02-06", "2026-02-09", "2026-02-10"], dtype="datetime64[D]")
    c = np.full((3, 2), 100.0)
    return MarketPanel(name="p", symbols=("AAA", "BBB"), tiers=("A", "A"), dates=dates, close=c, low=c,
                       volume=c, adv_20=c, spread_bps=c, members=np.ones((3, 2), dtype=bool))


def _now() -> datetime:
    return datetime(2026, 11, 9, tzinfo=UTC)


def test_refresh_caches_hashes_and_records_revisions(tmp_path) -> None:  # type: ignore[no-untyped-def]
    src = FixtureSource({"AAA": [(date(2026, 2, 9), 0.26), (date(2025, 11, 10), 0.26)]})
    rec = refresh(["AAA", "BBB"], src, tmp_path, now=_now)
    assert [r["revised"] for r in rec] == [False, False]
    assert rec[0]["sha256"] == series_hash(src.data["AAA"]) and rec[0]["rows"] == 2
    assert read_cached(tmp_path, "AAA") == sorted(src.data["AAA"])
    # the vendor restates one amount: a new hash, flagged as a revision; history is kept
    src.data["AAA"][0] = (date(2026, 2, 9), 0.27)
    again = refresh(["AAA"], src, tmp_path, now=_now)
    assert again[0]["revised"] is True
    lines = (tmp_path / REVISIONS).read_text().splitlines()
    assert len(lines) == 3 and json.loads(lines[0])["sha256"] != json.loads(lines[2])["sha256"]


def test_attach_keeps_only_panel_sessions_and_records_hashes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    refresh(["AAA"], FixtureSource({"AAA": [(date(2026, 2, 9), 0.26), (date(2025, 11, 10), 0.24)]}), tmp_path,
            now=_now)
    p = attach_dividends(_panel(), tmp_path)
    assert [(d.symbol, d.ex_date, d.amount) for d in p.dividends] == [("AAA", "2026-02-09", 0.26)]
    assert p.meta["dividends"]["count"] == 1 and set(p.meta["dividends"]["series_sha256"]) == {"AAA"}


def test_dividends_are_off_by_default_and_not_fill_params() -> None:
    assert PaperConfig().execution is None
    fp = PaperConfig(execution={"dividends": True, "dividend_cache": "x"}).fill_params()
    assert not hasattr(fp, "dividends")
