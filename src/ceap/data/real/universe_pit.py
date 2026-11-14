"""Approximately point-in-time S&P 500 membership (R5 item 5.2).

Inputs (committed, small): ``data/reference/sp500_changes.csv`` (``initial`` rows then dated ``add``
and ``remove`` events) and ``data/reference/ticker_map.csv`` (dated mapping from index ticker to the
price-vendor symbol, for renames such as FB to META). Membership on a date is replayed from the
events, so it is reproducible for any date. An event dated d takes effect at the open of d.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ceap.paths import data_path

REF = data_path("reference")
CHANGES_PATH = REF / "sp500_changes.csv"
TICKER_MAP_PATH = REF / "ticker_map.csv"


def normalise_ticker(raw: str) -> str:
    """Index-style ticker: upper case, class separator '.', e.g. 'brk-b' or 'BRK/B' -> 'BRK.B'."""
    return raw.strip().upper().replace("/", ".").replace("-", ".")


def yahoo_symbol(ticker: str) -> str:
    """Yahoo uses '-' for share classes: BRK.B -> BRK-B."""
    return ticker.replace(".", "-")


def stooq_symbol(ticker: str) -> str:
    """Stooq uses lower case, '-' for classes and a '.us' suffix: BRK.B -> brk-b.us."""
    return ticker.replace(".", "-").lower() + ".us"


@dataclass(frozen=True)
class Event:
    day: date
    action: str  # initial | add | remove
    ticker: str


@dataclass(frozen=True)
class MapRow:
    ticker: str
    vendor: str
    start: date
    end: date


def _rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        yield from csv.DictReader(line for line in fh if not line.startswith("#"))


def load_events(path: Path = CHANGES_PATH) -> list[Event]:
    ev = [
        Event(date.fromisoformat(r["date"]), r["action"], normalise_ticker(r["ticker"])) for r in _rows(path)
    ]
    bad = {e.action for e in ev} - {"initial", "add", "remove"}
    if bad:
        raise ValueError(f"unknown actions in {path}: {sorted(bad)}")
    order = {"initial": 0, "remove": 1, "add": 2}
    return sorted(ev, key=lambda e: (e.day, order[e.action], e.ticker))


def load_ticker_map(path: Path = TICKER_MAP_PATH) -> list[MapRow]:
    return [
        MapRow(
            normalise_ticker(r["ticker"]),
            r["vendor"].strip().upper(),
            date.fromisoformat(r["start"]),
            date.fromisoformat(r["end"]),
        )
        for r in _rows(path)
    ]


class PointInTimeUniverse:
    def __init__(self, events: list[Event], ticker_map: list[MapRow] | None = None) -> None:
        if not events or events[0].action != "initial":
            raise ValueError("membership events must start with 'initial' rows")
        self._events = events
        self._map = ticker_map or []
        self.start = events[0].day

    @classmethod
    def from_reference(cls) -> PointInTimeUniverse:
        return cls(load_events(), load_ticker_map())

    def members(self, on: date) -> frozenset[str]:
        if on < self.start:
            raise ValueError(f"{on} is before the first snapshot {self.start}")
        cur: set[str] = set()
        for e in self._events:
            if e.day > on:
                break
            if e.action == "remove":
                cur.discard(e.ticker)
            else:
                cur.add(e.ticker)
        return frozenset(cur)

    def ever_members(self, start: date, end: date) -> frozenset[str]:
        out = set(self.members(start))
        out.update(e.ticker for e in self._events if start < e.day <= end and e.action == "add")
        return frozenset(out)

    def vendor_symbol(self, ticker: str, on: date) -> str:
        """Price-vendor root symbol for an index ticker on a date (dated renames applied)."""
        for m in self._map:
            if m.ticker == ticker and m.start <= on <= m.end:
                return m.vendor
        return ticker


def coverage_by_month(
    universe: PointInTimeUniverse, month_ends: list[date], has_data: dict[str, set[date]]
) -> list[tuple[date, int, int]]:
    """(month_end, members, members with a usable close on that date) per month."""
    out = []
    for d in month_ends:
        mem = universe.members(d)
        ok = sum(1 for t in mem if d in has_data.get(t, set()))
        out.append((d, len(mem), ok))
    return out
