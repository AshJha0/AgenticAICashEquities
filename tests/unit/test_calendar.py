"""2.4 NYSE calendar: holidays, early closes, special closures, aware datetimes only."""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from ceap.data.calendar import (
    CALENDAR_PATH,
    NEW_YORK,
    generate_calendar_rows,
    is_early_close,
    is_holiday,
    is_trading_day,
    session_close,
    session_date,
    trading_days,
)
from ceap.data.historical import HistoricalStore, to_day


def test_committed_list_matches_the_rules():
    with CALENDAR_PATH.open(encoding="utf-8") as fh:
        committed = [(r["date"], r["kind"], r["name"]) for r in csv.DictReader(fh)]
    assert committed == generate_calendar_rows()


@pytest.mark.parametrize(
    "d",
    [date(2025, 11, 27), date(2024, 11, 28), date(2025, 4, 18), date(2024, 3, 29), date(2022, 6, 20),
     date(2021, 12, 24), date(2022, 1, 17), date(2027, 1, 1)],
)
def test_full_holidays_are_absent(d):
    assert is_holiday(d) and not is_trading_day(d)


@pytest.mark.parametrize("d", [date(2012, 10, 29), date(2012, 10, 30), date(2018, 12, 5), date(2025, 1, 9),
                               date(2001, 9, 11), date(2007, 1, 2)])
def test_special_closures(d):
    assert not is_trading_day(d)


def test_early_closes_at_13():
    black_friday = date(2025, 11, 28)
    assert is_early_close(black_friday) and is_trading_day(black_friday)
    assert session_close(black_friday) == datetime(2025, 11, 28, 13, 0, tzinfo=NEW_YORK)
    assert is_early_close(date(2025, 7, 3)) and is_early_close(date(2024, 12, 24))
    assert not is_early_close(date(2021, 12, 24))  # observed Christmas holiday, closed all day
    assert not is_early_close(date(2022, 7, 1))  # 4 July on a Monday: no 3 July session
    assert session_close(date(2025, 11, 26)).hour == 16


def test_new_years_day_on_saturday_is_not_observed_on_friday():
    assert is_trading_day(date(2021, 12, 31)) and is_trading_day(date(2010, 12, 31))


def test_year_counts():
    assert len(trading_days(date(2024, 1, 1), date(2024, 12, 31))) == 252
    assert len(trading_days(date(2023, 1, 1), date(2023, 12, 31))) == 250


def test_naive_datetimes_raise_and_dst_is_handled():
    with pytest.raises(ValueError, match="naive"):
        session_date(datetime(2025, 3, 10, 9, 30))
    with pytest.raises(ValueError, match="naive"):
        to_day(datetime(2025, 3, 10, 9, 30))
    # 03:30 UTC on the Monday after the March DST change is still Sunday evening in New York
    assert session_date(datetime(2025, 3, 10, 3, 30, tzinfo=UTC)) == date(2025, 3, 9)
    assert to_day(datetime(2025, 3, 10, 14, 0, tzinfo=UTC)) == np.datetime64("2025-03-10")
    assert session_close(date(2025, 3, 10)).utcoffset() == timedelta(hours=-4)
    assert session_close(date(2025, 1, 10)).utcoffset() == timedelta(hours=-5)


def test_research_dataset_dates_are_nyse_sessions():
    ds = HistoricalStore().get("R02")
    days = [d.astype(object) for d in ds.dates]
    assert len(days) == 5040 and str(ds.dates[-1]) == "2026-09-18"
    assert all(is_trading_day(d) for d in days)
    assert date(2025, 11, 27) not in days and date(2025, 11, 28) in days
