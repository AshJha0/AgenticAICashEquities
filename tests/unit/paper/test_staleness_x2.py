"""0.10.0 X2: forward-log staleness check (one missed session is enough)."""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ceap.paper.staleness import last_recorded, missed_sessions


def test_weekend_is_not_a_miss() -> None:
    # last row Fri 13 Nov, checked Mon 16 Nov 14:00 UTC (09:00 New York): nothing missed
    assert missed_sessions(date(2026, 11, 13), datetime(2026, 11, 16, 14, tzinfo=UTC)) == []


def test_one_missed_session_is_reported() -> None:
    assert missed_sessions(date(2026, 11, 12), datetime(2026, 11, 16, 14, tzinfo=UTC)) == [date(2026, 11, 13)]


def test_holiday_is_not_a_miss() -> None:
    # Thanksgiving, Thu 26 Nov 2026: last row Wed 25, checked Fri 27
    assert missed_sessions(date(2026, 11, 25), datetime(2026, 11, 27, 15, tzinfo=UTC)) == []


def test_naive_clock_is_refused() -> None:
    with pytest.raises(ValueError):
        missed_sessions(date(2026, 11, 12), datetime(2026, 11, 16, 14))


def test_last_recorded_reads_the_last_data_row(tmp_path: Path) -> None:
    p = tmp_path / "f.csv"
    p.write_text("date,book,x\n2026-11-12,forward,1\n2026-11-13,forward,2\n", encoding="utf-8")
    assert last_recorded(p) == date(2026, 11, 13)
    (tmp_path / "e.csv").write_text("date,book\n", encoding="utf-8")
    assert last_recorded(tmp_path / "e.csv") is None
