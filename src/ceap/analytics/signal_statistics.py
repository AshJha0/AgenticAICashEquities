"""Cross-sectional signal statistics: rank IC, decay, turnover and quantile spreads.

Pure numpy. The headline IC statistics use one-day (non-overlapping) forward
returns so their t-statistics are honest under the null; the horizon IC and
the decay profile use overlapping returns and are descriptive only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ceap.analytics.signals import compute_signal, get_signal
from ceap.domain.research import PeriodSignalStats, SignalStatistics

if TYPE_CHECKING:  # pragma: no cover
    from ceap.data.historical import HistoricalDataset

DECAY_HORIZONS: tuple[int, ...] = (1, 5, 21)
MIN_NAMES = 8
TRADING_DAYS_PER_YEAR = 252


def rank_average(x: np.ndarray) -> np.ndarray:
    """1-based average ranks of a 1-D array (ties share the mean rank); NaN stays NaN."""
    values = np.asarray(x, dtype=float)
    out = np.full(values.shape, np.nan)
    finite = np.isfinite(values)
    if not finite.any():
        return out
    _, inverse, counts = np.unique(values[finite], return_inverse=True, return_counts=True)
    starts = np.cumsum(counts) - counts + 1
    out[finite] = starts[inverse] + (counts[inverse] - 1) / 2.0
    return out


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rank correlation over the pairs where both values are finite; NaN if degenerate."""
    a = np.asarray(x, dtype=float)
    b = np.asarray(y, dtype=float)
    if a.shape != b.shape:
        raise ValueError("x and y must have the same shape")
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 3:
        return float("nan")
    ra = rank_average(a[mask])
    rb = rank_average(b[mask])
    ra -= ra.mean()
    rb -= rb.mean()
    denom = float(np.sqrt((ra * ra).sum() * (rb * rb).sum()))
    if denom <= 0.0:
        return float("nan")
    return float((ra * rb).sum() / denom)


def forward_returns(close: np.ndarray, horizon: int) -> np.ndarray:
    """``close[t+h] / close[t] - 1``; NaN for the last ``h`` rows."""
    c = np.asarray(close, dtype=float)
    out = np.full(c.shape, np.nan)
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if c.shape[0] > horizon:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[:-horizon] = c[horizon:] / c[:-horizon] - 1.0
    return out


def ic_series(signal: np.ndarray, close: np.ndarray, horizon: int = 1, min_names: int = MIN_NAMES) -> np.ndarray:
    """Per-date Spearman IC between the signal and the forward return; NaN when too few names."""
    s = np.asarray(signal, dtype=float)
    fwd = forward_returns(close, horizon)
    out = np.full(s.shape[0], np.nan)
    for t in range(s.shape[0]):
        pairs = np.isfinite(s[t]) & np.isfinite(fwd[t])
        if pairs.sum() >= min_names:
            out[t] = spearman(s[t], fwd[t])
    return out


def turnover_series(signal: np.ndarray, lag: int, min_names: int = MIN_NAMES) -> np.ndarray:
    """``1 - rank autocorrelation`` of the signal at ``lag`` days: how much the ranking changes."""
    s = np.asarray(signal, dtype=float)
    out = np.full(s.shape[0], np.nan)
    if lag <= 0:
        raise ValueError("lag must be positive")
    for t in range(lag, s.shape[0]):
        pairs = np.isfinite(s[t]) & np.isfinite(s[t - lag])
        if pairs.sum() >= min_names:
            rho = spearman(s[t], s[t - lag])
            out[t] = 1.0 - rho if np.isfinite(rho) else np.nan
    return out


def quantile_spread_series(
    signal: np.ndarray, close: np.ndarray, horizon: int, min_names: int = MIN_NAMES
) -> np.ndarray:
    """Top-tercile minus bottom-tercile mean forward return per date."""
    s = np.asarray(signal, dtype=float)
    fwd = forward_returns(close, horizon)
    out = np.full(s.shape[0], np.nan)
    for t in range(s.shape[0]):
        pairs = np.isfinite(s[t]) & np.isfinite(fwd[t])
        n = int(pairs.sum())
        if n < min_names:
            continue
        idx = np.flatnonzero(pairs)
        order = idx[np.argsort(s[t][idx])]
        k = max(1, n // 3)
        out[t] = float(fwd[t][order[-k:]].mean() - fwd[t][order[:k]].mean())
    return out


def _nanmean(x: np.ndarray) -> float:
    v = x[np.isfinite(x)]
    return float(v.mean()) if v.size else float("nan")


def period_signal_stats(
    name: str,
    dates: np.ndarray,
    i0: int,
    i1: int,
    ics_by_horizon: dict[int, np.ndarray],
    turnover: np.ndarray,
    spread: np.ndarray,
    horizon: int,
) -> PeriodSignalStats:
    """Statistics over the inclusive index range ``[i0, i1]``."""
    sl = slice(i0, i1 + 1)
    daily = ics_by_horizon[1][sl]
    valid = daily[np.isfinite(daily)]
    n = int(valid.size)
    mean_ic = ic_std = t_stat = ic_ir = hit = float("nan")
    if n >= 2:
        mean_ic = float(valid.mean())
        ic_std = float(valid.std(ddof=1))
        if ic_std > 0:
            t_stat = mean_ic / (ic_std / np.sqrt(n))
            ic_ir = mean_ic / ic_std
        hit = float((valid > 0).mean())
    return PeriodSignalStats(
        name=name,
        start=str(dates[i0]),
        end=str(dates[i1]),
        n_dates=n,
        mean_ic=mean_ic,
        ic_std=ic_std,
        ic_t_stat=float(t_stat),
        ic_ir=float(ic_ir),
        hit_rate=hit,
        turnover=_nanmean(turnover[sl]),
        mean_ic_horizon=_nanmean(ics_by_horizon[horizon][sl]),
        quantile_spread_bps_annual=_nanmean(spread[sl]) * TRADING_DAYS_PER_YEAR / horizon * 10_000.0,
        decay={str(h): _nanmean(ics_by_horizon[h][sl]) for h in DECAY_HORIZONS},
    )


def evaluate_signal(
    ds: HistoricalDataset,
    name: str,
    start: str | None = None,
    end: str | None = None,
    in_sample_end: str | None = None,
    horizon_days: int | None = None,
    rebalance_days: int | None = None,
) -> SignalStatistics:
    """Rank-IC statistics for the signal over in-sample, out-of-sample and full periods."""
    spec = get_signal(name)
    horizon = int(spec.default_horizon_days if horizon_days is None else horizon_days)
    rebalance = int(spec.default_rebalance_days if rebalance_days is None else rebalance_days)
    if horizon <= 0 or rebalance <= 0:
        raise ValueError("horizon_days and rebalance_days must be positive")
    i0, i1 = ds.index_range(start, end)
    split = ds.index_at_or_before(in_sample_end) if in_sample_end else ds.in_sample_end_index
    split = min(max(split, i0), i1)

    signal = compute_signal(name, ds.close, ds.volume)
    horizons = sorted({1, horizon, *DECAY_HORIZONS})
    ics = {h: ic_series(signal, ds.close, h) for h in horizons}
    turnover = turnover_series(signal, rebalance)
    spread = quantile_spread_series(signal, ds.close, horizon)

    ranges = {"in_sample": (i0, split), "out_of_sample": (min(split + 1, i1), i1), "full": (i0, i1)}
    periods = {
        label: period_signal_stats(label, ds.dates, a, b, ics, turnover, spread, horizon)
        for label, (a, b) in ranges.items()
    }
    return SignalStatistics(
        signal=name,
        dataset=ds.scenario.id,
        horizon_days=horizon,
        universe_size=ds.n_symbols,
        periods=periods,
    )
