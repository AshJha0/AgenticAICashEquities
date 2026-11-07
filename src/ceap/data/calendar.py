"""NYSE trading calendar 2000-2030: full holidays, 13:00 early closes and special closures.

The calendar is a committed list (``data/reference/nyse_calendar.csv``) generated
from the NYSE rules below by :func:`generate_calendar_rows`; a test regenerates
it and fails on any difference, so the list and the rules cannot drift apart.

Rules (NYSE Rule 7.2 and the published holiday schedules):

* New Year's Day, Independence Day, Christmas: a Sunday holiday is observed on
  Monday, a Saturday holiday on Friday - except New Year's Day, which is not
  observed on the preceding Friday (that Friday is in the old year).
* Martin Luther King Jr. Day (third Monday of January, from 1998), Washington's
  Birthday (third Monday of February), Good Friday, Memorial Day (last Monday of
  May), Juneteenth (19 June, observed, from 2022), Labor Day (first Monday of
  September), Thanksgiving (fourth Thursday of November).
* Early close at 13:00 ET: 3 July when it is a Monday to Thursday, the day after
  Thanksgiving, and 24 December when it is a weekday that is not itself a holiday.
* Special closures: 2001-09-11..14 (attacks), 2004-06-11 (President Reagan),
  2007-01-02 (President Ford), 2012-10-29/30 (Hurricane Sandy), 2018-12-05
  (President G.H.W. Bush), 2025-01-09 (President Carter).

All session times are timezone-aware in ``America/New_York``; a naive datetime
passed to any function here raises ``ValueError``.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

NEW_YORK = ZoneInfo("America/New_York")
FIRST_YEAR = 2000
LAST_YEAR = 2030
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)
CALENDAR_PATH = Path(__file__).resolve().parents[3] / "data" / "reference" / "nyse_calendar.csv"

SPECIAL_CLOSURES: dict[date, str] = {
    date(2001, 9, 11): "September 11 attacks",
    date(2001, 9, 12): "September 11 attacks",
    date(2001, 9, 13): "September 11 attacks",
    date(2001, 9, 14): "September 11 attacks",
    date(2004, 6, 11): "National day of mourning, President Reagan",
    date(2007, 1, 2): "National day of mourning, President Ford",
    date(2012, 10, 29): "Hurricane Sandy",
    date(2012, 10, 30): "Hurricane Sandy",
    date(2018, 12, 5): "National day of mourning, President G.H.W. Bush",
    date(2025, 1, 9): "National day of mourning, President Carter",
}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _observed(d: date, allow_friday: bool = True) -> date | None:
    if d.weekday() == 6:
        return d + timedelta(days=1)
    if d.weekday() == 5:
        return d - timedelta(days=1) if allow_friday else None
    return d


def holidays_for_year(year: int) -> dict[date, str]:
    out: dict[date, str] = {}

    def add(d: date | None, name: str) -> None:
        if d is not None:
            out[d] = name

    add(_observed(date(year, 1, 1), allow_friday=False), "New Year's Day")
    add(_nth_weekday(year, 1, 0, 3), "Martin Luther King Jr. Day")
    add(_nth_weekday(year, 2, 0, 3), "Washington's Birthday")
    add(_easter(year) - timedelta(days=2), "Good Friday")
    add(_last_weekday(year, 5, 0), "Memorial Day")
    if year >= 2022:
        add(_observed(date(year, 6, 19)), "Juneteenth")
    add(_observed(date(year, 7, 4)), "Independence Day")
    add(_nth_weekday(year, 9, 0, 1), "Labor Day")
    add(_nth_weekday(year, 11, 3, 4), "Thanksgiving")
    add(_observed(date(year, 12, 25)), "Christmas")
    for d, name in SPECIAL_CLOSURES.items():
        if d.year == year:
            out[d] = name
    return out


def early_closes_for_year(year: int, holidays: dict[date, str]) -> dict[date, str]:
    out: dict[date, str] = {}
    jul3 = date(year, 7, 3)
    if jul3.weekday() <= 3 and jul3 not in holidays:
        out[jul3] = "Day before Independence Day"
    out[_nth_weekday(year, 11, 3, 4) + timedelta(days=1)] = "Day after Thanksgiving"
    dec24 = date(year, 12, 24)
    if dec24.weekday() <= 4 and dec24 not in holidays:
        out[dec24] = "Christmas Eve"
    return out


def generate_calendar_rows() -> list[tuple[str, str, str]]:
    """``(date, kind, name)`` rows, kind in {"holiday", "early_close"}, 2000-2030, sorted by date."""
    rows: list[tuple[str, str, str]] = []
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        hol = holidays_for_year(year)
        rows.extend((d.isoformat(), "holiday", n) for d, n in hol.items())
        rows.extend((d.isoformat(), "early_close", n) for d, n in early_closes_for_year(year, hol).items())
    return sorted(rows)


def write_calendar(path: Path = CALENDAR_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(("date", "kind", "name"))
        writer.writerows(generate_calendar_rows())


@lru_cache(maxsize=1)
def _load() -> tuple[frozenset[date], frozenset[date]]:
    holidays: set[date] = set()
    early: set[date] = set()
    if CALENDAR_PATH.exists():
        with CALENDAR_PATH.open(encoding="utf-8") as fh:
            rows = [(r["date"], r["kind"], r["name"]) for r in csv.DictReader(fh)]
    else:  # installed wheel: the committed list is not shipped; the rules generate the identical list
        rows = generate_calendar_rows()
    for d_text, kind, _name in rows:
        (holidays if kind == "holiday" else early).add(date.fromisoformat(d_text))
    return frozenset(holidays), frozenset(early)


def _check_range(d: date) -> None:
    if not FIRST_YEAR <= d.year <= LAST_YEAR:
        raise ValueError(f"{d} is outside the committed NYSE calendar {FIRST_YEAR}-{LAST_YEAR}")


def is_holiday(d: date) -> bool:
    _check_range(d)
    return d in _load()[0]


def is_early_close(d: date) -> bool:
    _check_range(d)
    return d in _load()[1]


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and not is_holiday(d)


def trading_days(start: date, end: date) -> np.ndarray:
    """All NYSE trading days in ``[start, end]`` as ``datetime64[D]``."""
    _check_range(start)
    _check_range(end)
    hol = np.array(sorted(_load()[0]), dtype="datetime64[D]")
    days = np.arange(np.datetime64(start, "D"), np.datetime64(end, "D") + np.timedelta64(1, "D"), dtype="datetime64[D]")
    return days[np.is_busday(days, holidays=hol)]


def trading_days_ending(end: date, n: int) -> np.ndarray:
    """The ``n`` NYSE trading days ending on ``end`` (inclusive if ``end`` trades)."""
    if n <= 0:
        raise ValueError("n must be positive")
    hol = np.array(sorted(_load()[0]), dtype="datetime64[D]")
    last = np.busday_offset(np.datetime64(end, "D"), 0, roll="backward", holidays=hol)
    days = np.busday_offset(last, -np.arange(n)[::-1], holidays=hol)
    first = days[0].astype(object)
    _check_range(first)
    return days


def require_aware(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"naive datetime {ts.isoformat()} is not allowed; attach a timezone")
    return ts


def session_close(d: date) -> datetime:
    """Aware ``America/New_York`` close of a trading day (13:00 on early-close days)."""
    if not is_trading_day(d):
        raise ValueError(f"{d} is not an NYSE trading day")
    return datetime.combine(d, EARLY_CLOSE if is_early_close(d) else REGULAR_CLOSE, tzinfo=NEW_YORK)


def session_open(d: date) -> datetime:
    if not is_trading_day(d):
        raise ValueError(f"{d} is not an NYSE trading day")
    return datetime.combine(d, REGULAR_OPEN, tzinfo=NEW_YORK)


def session_date(ts: datetime) -> date:
    """The New York calendar date of an aware timestamp; naive input raises."""
    return require_aware(ts).astimezone(NEW_YORK).date()
