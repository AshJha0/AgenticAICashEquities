"""Understand the request: extract symbol and time window from natural language.

This is deliberately rule-based. The LLM plans *how* to investigate; the
parameters that scope data access (symbol, window) are extracted
deterministically so they can be validated by policy before any model
sees the request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ceap.data.scenarios import SYMBOLS

LONDON = ZoneInfo("Europe/London")
_TIME = re.compile(r"\b([01]?\d|2[0-3])(?::([0-5]\d))?\s*(am|pm)?\b", re.IGNORECASE)
_RANGE = re.compile(
    r"(?:between|from)\s+(?P<a>[0-2]?\d(?::[0-5]\d)?\s*(?:am|pm)?)\s+(?:and|to|-|–)\s+(?P<b>[0-2]?\d(?::[0-5]\d)?\s*(?:am|pm)?)",
    re.IGNORECASE,
)
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


@dataclass(frozen=True)
class ParsedRequest:
    symbol: str | None
    window_start: datetime | None
    window_end: datetime | None
    session_date: date
    relative_day: str | None


def _to_time(token: str) -> tuple[int, int] | None:
    m = _TIME.match(token.strip())
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and hh < 12:
        hh += 12
    if ampm == "am" and hh == 12:
        hh = 0
    return hh, mm


def resolve_session_date(explicit: str | None = None, today: date | None = None) -> date:
    """The trading date a question refers to when it gives none: the dataset's session date.

    An explicit ``YYYY-MM-DD`` wins. Relative words ("yesterday", "today") in
    the question are resolved by :func:`parse_question` against ``today``.
    """
    if explicit:
        return date.fromisoformat(explicit)
    from ceap.data.synthetic import DEFAULT_SESSION_DATE

    return DEFAULT_SESSION_DATE


def parse_question(
    question: str, default_date: date, symbols: tuple[str, ...] = SYMBOLS, today: date | None = None
) -> ParsedRequest:
    upper = question.upper()
    symbol = next((s for s in symbols if re.search(rf"\b{s}\b", upper)), None)
    today = today or date.today()

    session_date = default_date
    relative = None
    if m := _DATE.search(question):
        session_date = date.fromisoformat(m.group(1))
    elif re.search(r"\byesterday\b", question, re.IGNORECASE):
        session_date, relative = today - timedelta(days=1), "yesterday"
    elif re.search(r"\btoday\b", question, re.IGNORECASE):
        session_date, relative = today, "today"

    ws = we = None
    if m := _RANGE.search(question):
        a, b = _to_time(m.group("a")), _to_time(m.group("b"))
        if a and b:
            ws = datetime(session_date.year, session_date.month, session_date.day, a[0], a[1], tzinfo=LONDON)
            we = datetime(session_date.year, session_date.month, session_date.day, b[0], b[1], tzinfo=LONDON)
            if we <= ws:
                we += timedelta(days=1)
    return ParsedRequest(symbol, ws, we, session_date, relative)
