"""Gross 12-1 momentum decile long-short on the point-in-time universe (preregistration section 2).

Builds the gross monthly book only. Costs (``ceap.analytics.real_costs``) and the primary test are
applied in item 5.6; this module never computes the Newey-West statistic.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import numpy as np

LAG_SKIP = 21
LAG_LOOKBACK = 252
MAX_MISSING = 5
MIN_PRICE = 1.0
MIN_BREADTH = 100


@dataclass(frozen=True)
class Month:
    formation: date
    execution: date
    exit: date
    eligible: int
    members: int
    usable: int
    long_ret: float
    short_ret: float
    turnover: float
    longs: tuple[str, ...]
    shorts: tuple[str, ...]

    @property
    def gross(self) -> float:
        return self.long_ret - self.short_ret


def master_days(series: dict[str, dict[date, float]], min_share: float = 0.5) -> list[date]:
    """Trading days present in at least ``min_share`` of the busiest day's names."""
    counts: dict[date, int] = {}
    for s in series.values():
        for d in s:
            counts[d] = counts.get(d, 0) + 1
    if not counts:
        return []
    top = max(counts.values())
    return sorted(d for d, c in counts.items() if c >= min_share * top)


def month_end_indices(days: list[date]) -> list[int]:
    return [i for i in range(len(days)) if i + 1 == len(days) or days[i + 1].month != days[i].month]


def build_months(
    days: list[date],
    prices: Callable[[str, date], np.ndarray],
    members: Callable[[date], frozenset[str]],
    first_formation: date,
    last_holding_end: date,
) -> list[Month]:
    """``prices(ticker, formation)`` returns the adjusted closes aligned to ``days`` (NaN = missing)."""
    ends = [i for i in month_end_indices(days) if days[i] >= first_formation and days[i] < last_holding_end]
    last_idx = max(i for i, d in enumerate(days) if d <= last_holding_end)
    out: list[Month] = []
    prev_w: dict[str, float] = {}
    for k, f in enumerate(ends):
        e = f + 1
        if e > last_idx or f < LAG_LOOKBACK:
            continue
        x = ends[k + 1] + 1 if k + 1 < len(ends) else last_idx
        x = min(x, last_idx)
        mem = members(days[f])
        sig: dict[str, float] = {}
        paths: dict[str, np.ndarray] = {}
        usable = 0
        for t in sorted(mem):
            p = prices(t, days[f])
            if p.size != len(days):
                continue
            if not math.isnan(p[e]) and np.any(~np.isnan(p[e + 1 : x + 1])):
                usable += 1
            window = p[f - LAG_LOOKBACK : f + 1]
            if (
                np.isnan(p[f])
                or np.isnan(p[f - LAG_SKIP])
                or np.isnan(p[f - LAG_LOOKBACK])
                or int(np.isnan(window).sum()) > MAX_MISSING
                or p[f] < MIN_PRICE
                or p[f - LAG_LOOKBACK] <= 0
            ):
                continue
            sig[t] = p[f - LAG_SKIP] / p[f - LAG_LOOKBACK] - 1.0
            paths[t] = p
        n = len(sig) // 10
        ranked = sorted(sig, key=lambda t: (-sig[t], t))
        longs, shorts = ranked[:n], ranked[::-1][:n]
        lr, sr = _leg(longs, paths, e, x), _leg(shorts, paths, e, x)
        w = (
            {t: 1.0 / len(lr[1]) for t in lr[1]} | {t: -1.0 / len(sr[1]) for t in sr[1]}
            if lr[1] and sr[1]
            else {}
        )
        turnover = sum(abs(w.get(t, 0.0) - prev_w.get(t, 0.0)) for t in set(w) | set(prev_w))
        prev_w = w
        out.append(
            Month(
                days[f],
                days[e],
                days[x],
                len(sig),
                len(mem),
                usable,
                lr[0],
                sr[0],
                turnover,
                tuple(lr[1]),
                tuple(sr[1]),
            )
        )
    return out


def _leg(names: list[str], paths: dict[str, np.ndarray], e: int, x: int) -> tuple[float, list[str]]:
    """Equal-weight leg return; no execution close drops the name; delisting earns cash after the
    last close (preregistration section 2)."""
    rets, kept = [], []
    for t in names:
        p = paths[t]
        if math.isnan(p[e]):
            continue
        seg = p[e : x + 1]
        valid = np.where(~np.isnan(seg))[0]
        rets.append(seg[valid[-1]] / p[e] - 1.0)
        kept.append(t)
    return (float(np.mean(rets)) if rets else float("nan")), kept


def coverage(months: list[Month]) -> tuple[float, float]:
    """(overall usable name-months / member name-months, worst single month)."""
    mem = sum(m.members for m in months)
    use = sum(m.usable for m in months)
    worst = min((m.usable / m.members for m in months if m.members), default=0.0)
    return (use / mem if mem else 0.0), worst
