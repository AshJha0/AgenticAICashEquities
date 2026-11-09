"""D10 (0.9.0): Shumway-style delisting returns by exit type, kept names with a missing close, band."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from ceap.data.real import momentum as mom


def _book(n_names: int = 110, n_days: int = 700, seed: int = 7):  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    days, d = [], date(2004, 1, 2)
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    drift = np.linspace(-0.001, 0.001, n_names)
    px = {f"S{i:03d}": 50 * np.exp(np.cumsum(drift[i] + 0.01 * rng.standard_normal(n_days))) for i in range(n_names)}
    return days, px


def _run(days, px, **kw):  # type: ignore[no-untyped-def]
    return mom.build_months(days, lambda t, _d: px[t], lambda _d: frozenset(px), days[260], days[-1], **kw)


def test_default_is_the_preregistered_book() -> None:
    days, px = _book()
    a, b = _run(days, px), _run(days, px, delisting="prereg")
    assert [m.gross for m in a] == [m.gross for m in b]


def test_performance_exit_takes_the_shumway_return_and_merger_does_not() -> None:
    days, px = _book()
    m = _run(days, px)[0]
    e = days.index(m.execution)
    victim = m.shorts[0]
    crash = px[victim].copy()
    crash[e + 1 : e + 4] = crash[e] * np.array([0.8, 0.6, 0.5])  # -50 %: a performance exit
    crash[e + 4 :] = np.nan
    px2 = dict(px) | {victim: crash}
    pre = _run(days, px2)[0]
    shu = _run(days, px2, delisting="shumway")[0]
    assert (victim, "performance") in shu.delisted
    n = len(m.shorts)
    assert shu.short_ret - pre.short_ret == pytest.approx((0.5 * 0.7 - 0.5) / n)
    # a quiet exit (flat path) is a merger: no extra return
    quiet = px[victim].copy()
    quiet[e + 4 :] = np.nan
    px3 = dict(px) | {victim: quiet}
    assert _run(days, px3, delisting="shumway")[0].short_ret == pytest.approx(_run(days, px3)[0].short_ret)
    assert (victim, "merger") in _run(days, px3)[0].delisted


def test_keep_missing_close_enters_at_the_last_close() -> None:
    days, px = _book()
    m = _run(days, px)[0]
    e = days.index(m.execution)
    name = m.longs[0]
    gap = px[name].copy()
    gap[e] = np.nan
    px2 = dict(px) | {name: gap}
    dropped = _run(days, px2)[0]
    kept = _run(days, px2, keep_missing_close=True)[0]
    assert name not in dropped.longs and name in kept.longs


def test_band_is_monotone_in_the_delisting_return() -> None:
    days, px = _book()
    m = _run(days, px)[0]
    e = days.index(m.execution)
    for victim in m.longs[:3]:
        p = px[victim].copy()
        p[e + 1 :] = np.nan
        p[e] = 0.5  # below MIN_PRICE: a performance exit
        px[victim] = p
    band = mom.delisting_band(days, lambda t, _d: px[t], lambda _d: frozenset(px), days[260], days[-1])
    vals = [band[k] for k in ("shumway_+0.00", "shumway_-0.30", "shumway_-0.55", "shumway_-1.00")]
    assert vals == sorted(vals, reverse=True)
    assert set(band) >= {"prereg"}


def test_unknown_rule_is_refused() -> None:
    days, px = _book()
    with pytest.raises(ValueError):
        _run(days, px, delisting="crsp")
