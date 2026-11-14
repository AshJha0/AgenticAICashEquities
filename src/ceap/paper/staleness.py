"""Forward-log staleness: which NYSE sessions have no row yet (0.10.0 X2, finding R-22).

Plain idea: the daily job records each completed session. A check that runs later the same day
lists every trading day strictly after the last recorded session and strictly before today's New
York date. Any such day is a miss; one miss is enough to open an issue ("zero misses").

Worked example: last row 2026-11-12 (Thu), checked on Mon 2026-11-16 in New York. The sessions
in between are Fri 2026-11-13 only (the weekend does not trade), so one session is missed.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ceap.data.calendar import trading_days

NEW_YORK = ZoneInfo("America/New_York")


def last_recorded(csv_path: str | Path) -> date | None:
    """Date in the first column of the last data row of an exported forward log, if any."""
    with Path(csv_path).open(encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.reader(fh) if r and not r[0].startswith("#")]
    for row in reversed(rows[1:]):  # rows[0] is the header
        try:
            return date.fromisoformat(row[0])
        except ValueError:
            continue
    return None


def missed_sessions(last: date, now: datetime) -> list[date]:
    """Trading days in ``(last, today_ny)``; ``now`` must be timezone-aware."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = now.astimezone(NEW_YORK).date()
    start, end = last + timedelta(days=1), today - timedelta(days=1)
    if start > end:
        return []
    return [d.astype(object) for d in trading_days(start, end)]
