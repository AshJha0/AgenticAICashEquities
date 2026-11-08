"""Session clock for the US cash-equity desk (2T.1).

Kept import-light on purpose: platform, API and MCP code read the desk timezone from here
without pulling in the scenario catalogue.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SESSION_TZ = ZoneInfo("America/New_York")
SESSION_TZ_NAME = "America/New_York"
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
SESSION_SECONDS = 23_400
#: previous sessions whose same clock window forms the time-of-day-matched baseline (2T.2)
REFERENCE_SESSIONS = 5


def session_time(session_date: date, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    return datetime(session_date.year, session_date.month, session_date.day, hh, mm, ss, tzinfo=SESSION_TZ)


def previous_sessions(session_date: date, k: int = REFERENCE_SESSIONS) -> tuple[date, ...]:
    """The ``k`` weekdays before ``session_date``, most recent first (holidays ignored: synthetic)."""
    out: list[date] = []
    d = session_date
    while len(out) < k:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return tuple(out)


def matched_windows(
    start: datetime, end: datetime, k: int = REFERENCE_SESSIONS
) -> list[tuple[datetime, datetime]]:
    """The same wall-clock window on each of the ``k`` previous sessions (2T.2 baseline)."""
    local_s, local_e = start.astimezone(SESSION_TZ), end.astimezone(SESSION_TZ)
    out = []
    for d in previous_sessions(local_s.date(), k):
        out.append(
            (
                session_time(d, local_s.hour, local_s.minute, local_s.second),
                session_time(d, local_e.hour, local_e.minute, local_e.second),
            )
        )
    return out
