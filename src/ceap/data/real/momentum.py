"""Gross 12-1 momentum decile long-short on the point-in-time universe (preregistration section 2).

Builds the gross monthly book only. Costs (``ceap.analytics.real_costs``) and the primary test are
applied in item 5.6; this module never computes the Newey-West statistic.

Delisting returns (0.9.0, item D10). The preregistered rule (``delisting="prereg"``, the default and
the only setting the primary test uses) earns cash after a name's last close. Two departures are
offered as *sensitivity analyses*, never as a replacement for the registered result:

* ``delisting="shumway"``: a name whose price path ends inside the holding month earns, on top of its
  return to the last close, a delisting return chosen by exit type (Shumway 1997, J. Finance 52(1),
  which found the CRSP-missing performance delisting returns average about -30 % on NYSE/AMEX;
  Shumway and Warther 1999 put Nasdaq near -55 %; evidence class: published papers as recalled).
  Free daily data carry no delisting code, so the exit type is inferred from the path: a
  ``performance`` exit when the last close is below ``MIN_PRICE`` or the name fell by at least
  ``PERF_DRAWDOWN`` over the ``PERF_LOOKBACK`` sessions before it, a ``merger`` exit (0 %) otherwise.
* ``keep_missing_close=True``: a selected name with no close on the execution day is entered at its
  last close within ``MAX_MISSING`` sessions before it instead of being dropped.

:func:`delisting_band` reruns the book over a grid of performance-delisting returns (0, -30 %, -55 %,
-100 %), the honest way to show how much the unknowable exit return could matter.

Worked example. A short-leg name closes at 2.00 on day 10 of a 21-day holding month, 45 % below its
close 60 sessions earlier, and never prints again: the prereg rule books ``2.00 / p_e - 1``; the
shumway rule books ``(2.00 / p_e) * (1 - 0.30) - 1``, which helps a short leg.
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
PERF_LOOKBACK = 60
PERF_DRAWDOWN = 0.30
SHUMWAY_PERFORMANCE = -0.30
DELISTING_RULES = ("prereg", "shumway")


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
    delisted: tuple[tuple[str, str], ...] = ()  # (ticker, exit type) of names whose path ended in the month

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
    delisting: str = "prereg",
    perf_delist_return: float = SHUMWAY_PERFORMANCE,
    keep_missing_close: bool = False,
) -> list[Month]:
    """``prices(ticker, formation)`` returns the adjusted closes aligned to ``days`` (NaN = missing).

    ``delisting``, ``perf_delist_return`` and ``keep_missing_close`` are the D10 sensitivity options
    (module docstring); the defaults reproduce the preregistered book exactly."""
    if delisting not in DELISTING_RULES:
        raise ValueError(f"delisting must be one of {DELISTING_RULES}")
    opts = _LegOptions(delisting, perf_delist_return, keep_missing_close)
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
        gone: list[tuple[str, str]] = []
        lr, sr = _leg(longs, paths, e, x, opts, gone), _leg(shorts, paths, e, x, opts, gone)
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
                tuple(gone),
            )
        )
    return out


@dataclass(frozen=True)
class _LegOptions:
    delisting: str = "prereg"
    perf_delist_return: float = SHUMWAY_PERFORMANCE
    keep_missing_close: bool = False


def exit_type(p: np.ndarray, last: int) -> str:
    """``performance`` or ``merger`` for a path whose last close is at index ``last`` (D10 rule)."""
    if p[last] < MIN_PRICE:
        return "performance"
    back = p[max(0, last - PERF_LOOKBACK) : last + 1]
    back = back[~np.isnan(back)]
    if back.size and p[last] / back[0] - 1.0 <= -PERF_DRAWDOWN:
        return "performance"
    return "merger"


def _leg(names: list[str], paths: dict[str, np.ndarray], e: int, x: int, opts: _LegOptions | None = None,
         gone: list[tuple[str, str]] | None = None) -> tuple[float, list[str]]:
    """Equal-weight leg return; no execution close drops the name; delisting earns cash after the
    last close (preregistration section 2). ``opts`` switches on the D10 sensitivity rules."""
    opts = opts or _LegOptions()
    rets, kept = [], []
    for t in names:
        p = paths[t]
        entry = p[e]
        if math.isnan(entry):
            if not opts.keep_missing_close:
                continue
            prior = p[max(0, e - MAX_MISSING) : e]
            prior = prior[~np.isnan(prior)]
            if not prior.size:
                continue
            entry = prior[-1]
        seg = p[e : x + 1]
        valid = np.where(~np.isnan(seg))[0]
        if not valid.size:  # kept with a missing close and never printed again in the month
            rets.append(0.0)
            kept.append(t)
            continue
        growth = seg[valid[-1]] / entry
        last = e + int(valid[-1])
        ended = last < x and bool(np.all(np.isnan(p[last + 1 :])))
        if ended:
            kind = exit_type(p, last)
            if gone is not None:
                gone.append((t, kind))
            if opts.delisting == "shumway" and kind == "performance":
                growth *= 1.0 + opts.perf_delist_return
        rets.append(growth - 1.0)
        kept.append(t)
    return (float(np.mean(rets)) if rets else float("nan")), kept


def delisting_band(
    days: list[date],
    prices: Callable[[str, date], np.ndarray],
    members: Callable[[date], frozenset[str]],
    first_formation: date,
    last_holding_end: date,
    grid: tuple[float, ...] = (0.0, -0.30, -0.55, -1.0),
    keep_missing_close: bool = True,
) -> dict[str, float]:
    """Mean monthly gross return of the book under each performance-delisting return in ``grid``,
    plus the preregistered rule, so a report can show the band (D10 sensitivity, never the primary)."""
    def mean_gross(months: list[Month]) -> float:
        g = [m.gross for m in months if np.isfinite(m.gross)]
        return float(np.mean(g)) if g else float("nan")

    out = {"prereg": mean_gross(build_months(days, prices, members, first_formation, last_holding_end))}
    for r in grid:
        out[f"shumway_{r:+.2f}"] = mean_gross(build_months(
            days, prices, members, first_formation, last_holding_end, delisting="shumway",
            perf_delist_return=r, keep_missing_close=keep_missing_close))
    return out


def coverage(months: list[Month]) -> tuple[float, float]:
    """(overall usable name-months / member name-months, worst single month)."""
    mem = sum(m.members for m in months)
    use = sum(m.usable for m in months)
    worst = min((m.usable / m.members for m in months if m.members), default=0.0)
    return (use / mem if mem else 0.0), worst
