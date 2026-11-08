"""Data quality gate for real daily prices (R5 item 5.3).

Checks per series: duplicate or unordered dates, stale runs (unchanged close for 5+ consecutive
days), zero-volume days, days off the NYSE calendar (holiday alignment) and missing NYSE days, moves
beyond 40 % (flagged, and confirmed or refuted by the second vendor), and known split ex-dates where
an adjusted series must NOT show the raw split jump. Cross-vendor: daily simple returns compared on
common dates; agreement means an absolute difference of at most 10 bps (preregistration section 7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ceap.data.real.prices import DailySeries

STALE_RUN = 5
BIG_MOVE = 0.40
AGREE_BPS = 10.0
# Known splits that a correctly adjusted series must absorb: (symbol, ex-date, ratio).
KNOWN_SPLITS: tuple[tuple[str, date, float], ...] = (
    ("AAPL", date(2014, 6, 9), 7.0),
    ("AAPL", date(2020, 8, 31), 4.0),
)


@dataclass
class SeriesReport:
    symbol: str
    n_days: int
    duplicate_dates: list[date] = field(default_factory=list)
    unordered: bool = False
    stale_runs: list[tuple[date, int]] = field(default_factory=list)
    zero_volume_days: int = 0
    off_calendar: list[date] = field(default_factory=list)
    missing_calendar_days: int = 0
    big_moves: list[tuple[date, float]] = field(default_factory=list)
    split_failures: list[date] = field(default_factory=list)

    @property
    def hard_fail(self) -> bool:
        """Defects that make the series unusable until fixed."""
        return bool(self.duplicate_dates or self.unordered or self.off_calendar or self.split_failures)


def check_series(s: DailySeries, calendar: set[date] | None = None) -> SeriesReport:
    rep = SeriesReport(s.symbol, len(s.dates))
    seen: set[date] = set()
    for d in s.dates:
        if d in seen:
            rep.duplicate_dates.append(d)
        seen.add(d)
    rep.unordered = any(b < a for a, b in zip(s.dates, s.dates[1:], strict=False))
    run = 1
    for i in range(1, len(s.close)):
        run = run + 1 if s.close[i] == s.close[i - 1] else 1
        if run == STALE_RUN:
            rep.stale_runs.append((s.dates[i - STALE_RUN + 1], STALE_RUN))
    rep.zero_volume_days = sum(1 for v in s.volume if v <= 0)
    if calendar is not None and s.dates:
        rep.off_calendar = sorted(d for d in seen if d not in calendar)
        span = {d for d in calendar if s.dates[0] <= d <= s.dates[-1]}
        rep.missing_calendar_days = len(span - seen)
    rets = s.returns()
    for d, r in sorted(rets.items()):
        if abs(r) > BIG_MOVE:
            rep.big_moves.append((d, r))
    for sym, ex, ratio in KNOWN_SPLITS:
        # An unadjusted series shows about 1/ratio - 1 (e.g. -86 % for 7:1) on the ex-date; an adjusted
        # one shows an ordinary day. Fail if the move is closer to the raw jump than to zero.
        if sym == s.symbol and ex in rets and abs(rets[ex] - (1.0 / ratio - 1.0)) < abs(rets[ex]):
            rep.split_failures.append(ex)
    return rep


@dataclass(frozen=True)
class CrossCheck:
    symbol: str
    common_days: int
    agree_days: int
    unconfirmed_big_moves: list[date]

    @property
    def agreement(self) -> float:
        return self.agree_days / self.common_days if self.common_days else 0.0


def cross_check(primary: DailySeries, secondary: DailySeries) -> CrossCheck:
    """Compare returns, not levels (vendors adjust levels differently)."""
    a, b = primary.returns(), secondary.returns()
    common = sorted(set(a) & set(b))
    agree = sum(1 for d in common if abs(a[d] - b[d]) * 1e4 <= AGREE_BPS)
    unconfirmed = [d for d in common if abs(a[d]) > BIG_MOVE and abs(a[d] - b[d]) * 1e4 > AGREE_BPS]
    return CrossCheck(primary.symbol, len(common), agree, unconfirmed)
