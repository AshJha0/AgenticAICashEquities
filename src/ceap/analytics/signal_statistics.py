"""Cross-sectional signal statistics: rank IC, decay, rank-autocorrelation turnover, quantile spreads.

0.4.0 conventions (2.7):

* The **gate statistic** is the t-statistic of the *non-overlapping* horizon-h
  rank IC, where ``h`` is the holding period (the rebalance interval) and the
  forward return starts after the execution lag: ``close[t+L+h] / close[t+L] - 1``.
  Non-overlapping samples make the plain t honest under the null.
* The overlapping (daily-sampled) horizon-h IC is reported with a Newey-West
  t (Bartlett kernel, ``h - 1`` lags) and is descriptive.
* In-sample statistics drop the last ``L + h`` in-sample dates so no in-sample
  forward return reads an out-of-sample price.
* ``ic_ir_annualised = mean / sd * sqrt(252 / h)`` - descriptive only (0.10 D23): it assumes independent
  periods and is never a gate; the tests use the non-overlapping t and the Newey-West t.
* ``rank_autocorrelation_turnover`` is ``1 - rank autocorrelation`` of the
  signal at the rebalance lag (range 0 to 2). It is *not* portfolio turnover;
  the backtest reports that.
* Non-members (2.13) are NaN in the signal and never ranked.
* 0.10 (Q4, finding R-20): ``ic_t_stat_factor_neutral`` is the gate statistic on the same
  non-overlapping dates with the signal ranks and the forward return residualised, date by date, on an
  intercept and each name's trailing five-year market beta (:func:`ceap.analytics.risk_research.factor_neutral_ic`). The alpha gate
  applies the same hurdle to it.

Rank ICs are computed row-wise in a vectorised way with ordinal ranks; with
continuous prices ties have probability zero, and :func:`spearman` (average
ranks) is kept for single cross-sections.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from ceap.analytics.signals import dataset_signal, get_signal
from ceap.domain.research import DEFAULT_EXECUTION_LAG_DAYS, PeriodSignalStats, SignalStatistics

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


def forward_returns(close: np.ndarray, horizon: int, lag: int = 0) -> np.ndarray:
    """``close[t+lag+h] / close[t+lag] - 1``; NaN where the window runs past the data."""
    c = np.asarray(close, dtype=float)
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if lag < 0:
        raise ValueError("lag must be >= 0")
    out = np.full(c.shape, np.nan)
    span = lag + horizon
    if c.shape[0] > span:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[: c.shape[0] - span] = c[span:] / c[lag : c.shape[0] - horizon] - 1.0
    return out


def _row_ranks(x: np.ndarray) -> np.ndarray:
    """Ordinal 1-based ranks per row; NaN stays NaN."""
    filled = np.where(np.isfinite(x), x, np.inf)
    order = np.argsort(filled, axis=1, kind="stable")
    ranks = np.empty_like(order, dtype=float)
    rows = np.arange(x.shape[0])[:, None]
    ranks[rows, order] = np.arange(1, x.shape[1] + 1, dtype=float)[None, :]
    ranks[~np.isfinite(x)] = np.nan
    return ranks


def rowwise_rank_corr(a: np.ndarray, b: np.ndarray, min_names: int = MIN_NAMES) -> np.ndarray:
    """Spearman correlation of each row of ``a`` with the same row of ``b`` over jointly finite entries."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    mask = np.isfinite(a) & np.isfinite(b)
    ra = _row_ranks(np.where(mask, a, np.nan))
    rb = _row_ranks(np.where(mask, b, np.nan))
    cnt = mask.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        ma = np.nansum(ra, axis=1) / cnt
        mb = np.nansum(rb, axis=1) / cnt
        da = np.where(mask, ra - ma[:, None], 0.0)
        db = np.where(mask, rb - mb[:, None], 0.0)
        num = (da * db).sum(axis=1)
        den = np.sqrt((da * da).sum(axis=1) * (db * db).sum(axis=1))
        out: np.ndarray = num / den
    out[(cnt < min_names) | ~np.isfinite(out)] = np.nan
    return out


def ic_series(
    signal: np.ndarray, close: np.ndarray, horizon: int = 1, min_names: int = MIN_NAMES, lag: int = 0
) -> np.ndarray:
    """Per-date Spearman IC between the signal and the forward return; NaN when too few names."""
    return rowwise_rank_corr(np.asarray(signal, dtype=float), forward_returns(close, horizon, lag), min_names)


def turnover_series(signal: np.ndarray, lag: int, min_names: int = MIN_NAMES) -> np.ndarray:
    """``1 - rank autocorrelation`` of the signal at ``lag`` days: how much the ranking changes."""
    s = np.asarray(signal, dtype=float)
    if lag <= 0:
        raise ValueError("lag must be positive")
    out = np.full(s.shape[0], np.nan)
    if s.shape[0] > lag:
        rho = rowwise_rank_corr(s[lag:], s[:-lag], min_names)
        out[lag:] = 1.0 - rho
    return out


def quantile_spread_series(
    signal: np.ndarray, close: np.ndarray, horizon: int, min_names: int = MIN_NAMES, lag: int = 0
) -> np.ndarray:
    """Top-tercile minus bottom-tercile mean forward return per date."""
    s = np.asarray(signal, dtype=float)
    fwd = forward_returns(close, horizon, lag)
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


def andrews_bandwidth(x: np.ndarray) -> float:
    """Andrews (1991) AR(1) plug-in bandwidth for the Bartlett kernel: ``1.1447 (alpha n)^(1/3)`` with
    ``alpha = 4 rho^2 / ((1 - rho)^2 (1 + rho)^2)``; ``rho`` is the lag-1 autocorrelation over finite pairs.
    Evidence class: method (Andrews 1991, Econometrica 59(3))."""
    v = np.asarray(x, dtype=float)
    ok = np.isfinite(v)
    n = int(ok.sum())
    if n < 4:
        return 0.0
    d = np.where(ok, v - v[ok].mean(), 0.0)
    pair = ok[1:] & ok[:-1]
    den = float((d[ok] ** 2).sum())
    rho = float((d[1:] * d[:-1])[pair].sum()) / den if den > 0 else 0.0
    rho = max(min(rho, 0.97), -0.97)
    alpha = 4.0 * rho * rho / ((1.0 - rho) ** 2 * (1.0 + rho) ** 2)
    bandwidth: float = 1.1447 * (alpha * n) ** (1.0 / 3.0)
    return bandwidth


def newey_west_t(x: np.ndarray, lags: int | None) -> float:
    """t-statistic of the mean of ``x`` with a Newey-West (Bartlett) long-run variance.

    0.10 (D11): missing values are kept in place as gaps; the lag-k autocovariance uses only pairs whose
    two ends are both observed and ``k`` days apart in the original series (dropping NaNs first would
    treat observations either side of a gap as neighbours). ``lags=None`` picks the Andrews (1991)
    automatic bandwidth, which grows for persistent series where a fixed ``h - 1`` is too short.
    """
    v = np.asarray(x, dtype=float)
    ok = np.isfinite(v)
    n = int(ok.sum())
    if n < 3:
        return float("nan")
    bw = andrews_bandwidth(v) if lags is None else float(lags)
    L = int(math.floor(bw)) if lags is None else int(lags)  # noqa: N806
    mean = float(v[ok].mean())
    d = np.where(ok, v - mean, 0.0)
    lrv = float(d @ d) / n
    for k in range(1, min(L, v.size - 1) + 1):
        w = 1.0 - k / (bw + 1.0)
        if w <= 0:
            break
        lrv += 2.0 * w * float(d[k:] @ d[:-k]) / n  # unobserved ends contribute zero (masked)
    if lrv <= 0:
        return float("nan")
    return float(mean / math.sqrt(lrv / n))


def _nanmean(x: np.ndarray) -> float:
    v = x[np.isfinite(x)]
    return float(v.mean()) if v.size else float("nan")


def _t(v: np.ndarray) -> tuple[float, float, float, int]:
    v = v[np.isfinite(v)]
    n = int(v.size)
    if n < 2:
        return float("nan"), float("nan"), float("nan"), n
    mean, sd = float(v.mean()), float(v.std(ddof=1))
    t = mean / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    return mean, sd, t, n


def period_signal_stats(
    name: str,
    dates: np.ndarray,
    i0: int,
    i1: int,
    ics_by_horizon: dict[int, np.ndarray],
    turnover: np.ndarray,
    spread: np.ndarray,
    horizon: int,
    lag: int = 0,
    ic_1d: np.ndarray | None = None,
) -> PeriodSignalStats:
    """Statistics over decision dates ``[i0, i1]`` (callers trim the forward-looking tail)."""
    nan = float("nan")
    if i1 < i0:
        return PeriodSignalStats(name, str(dates[i0]), str(dates[i0]), 0, nan, nan, nan, nan, nan, nan, nan, nan,
                                 {}, horizon_days=horizon, execution_lag_days=lag)
    sl = slice(i0, i1 + 1)
    overlap = ics_by_horizon[horizon][sl]
    finite = np.flatnonzero(np.isfinite(overlap))
    sample = overlap[finite[0] :: horizon] if finite.size else overlap[:0]
    mean_ic, ic_std, t_stat, n = _t(sample)
    ic_ir = mean_ic / ic_std if n >= 2 and ic_std > 0 else nan
    hit = float((sample[np.isfinite(sample)] > 0).mean()) if n else nan
    one = (ic_1d if ic_1d is not None else ics_by_horizon[1])[sl]
    m1, _s1, t1, _n1 = _t(one)
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
        rank_autocorrelation_turnover=_nanmean(turnover[sl]),
        mean_ic_horizon=_nanmean(overlap),
        quantile_spread_bps_annual=_nanmean(spread[sl]) * TRADING_DAYS_PER_YEAR / horizon * 10_000.0,
        # each horizon trimmed by its own tail so no in-sample forward return reads out-of-sample prices
        decay={
            str(h): _nanmean(ics_by_horizon[h][i0 : max(i0, i1 + horizon - h) + 1])
            for h in DECAY_HORIZONS
            if h in ics_by_horizon
        },
        ic_t_stat_nw=newey_west_t(overlap, None if andrews_bandwidth(overlap) > horizon - 1 else horizon - 1),
        ic_ir_annualised=float(ic_ir * math.sqrt(TRADING_DAYS_PER_YEAR / horizon)) if math.isfinite(ic_ir) else nan,
        mean_ic_1d=m1,
        ic_t_stat_1d=t1,
        horizon_days=horizon,
        execution_lag_days=lag,
    )


def gate_dates(overlap: np.ndarray, i0: int, i1: int, horizon: int) -> np.ndarray:
    """Indices of the non-overlapping gate sample in ``[i0, i1]``: every ``horizon``-th date from the first
    finite IC, exactly as :func:`period_signal_stats` samples them."""
    if i1 < i0:
        return np.zeros(0, dtype=int)
    finite = np.flatnonzero(np.isfinite(overlap[i0 : i1 + 1]))
    return np.arange(i0 + int(finite[0]), i1 + 1, horizon) if finite.size else np.zeros(0, dtype=int)


def _with_factor_neutral(
    ds: HistoricalDataset,
    signal: np.ndarray,
    overlap: np.ndarray,
    ranges: dict[str, tuple[int, int]],
    periods: dict[str, PeriodSignalStats],
    horizon: int,
    lag: int,
) -> dict[str, PeriodSignalStats]:
    from dataclasses import replace

    from ceap.analytics.risk_research import factor_neutral_ic, rolling_betas

    betas = rolling_betas(ds.close)
    fwd = forward_returns(ds.close, horizon, lag)
    out = {}
    for label, (a, b) in ranges.items():
        ic = factor_neutral_ic(signal, fwd, betas, gate_dates(overlap, a, b, horizon))
        mean, _sd, t, _n = _t(ic)
        out[label] = replace(periods[label], mean_ic_factor_neutral=mean, ic_t_stat_factor_neutral=float(t))
    return out


def evaluate_signal(
    ds: HistoricalDataset,
    name: str,
    start: str | None = None,
    end: str | None = None,
    in_sample_end: str | None = None,
    horizon_days: int | None = None,
    rebalance_days: int | None = None,
    execution_lag_days: int = DEFAULT_EXECUTION_LAG_DAYS,
) -> SignalStatistics:
    """Rank-IC statistics for the signal over in-sample, out-of-sample and full periods.

    The gate horizon is the holding period: ``rebalance_days`` (or the signal's default) unless
    ``horizon_days`` is given explicitly.
    """
    spec = get_signal(name)
    rebalance = int(spec.default_rebalance_days if rebalance_days is None else rebalance_days)
    horizon = int(rebalance if horizon_days is None else horizon_days)
    lag = int(execution_lag_days)
    if horizon <= 0 or rebalance <= 0:
        raise ValueError("horizon_days and rebalance_days must be positive")
    if lag < 0:
        raise ValueError("execution_lag_days must be >= 0")
    i0, i1 = ds.index_range(start, end)
    split = ds.locked_split(in_sample_end, i0, i1)

    signal = dataset_signal(ds, name)
    horizons = sorted({1, horizon, *DECAY_HORIZONS})
    ics = {h: ic_series(signal, ds.close, h, lag=lag) for h in horizons}
    turnover = turnover_series(signal, rebalance)
    spread = quantile_spread_series(signal, ds.close, horizon, lag=lag)

    tail = lag + horizon
    ranges = {
        "in_sample": (i0, min(split - tail, i1)),
        "out_of_sample": (min(split + 1, i1), i1 - tail),
        "full": (i0, i1 - tail),
    }
    periods = {
        label: period_signal_stats(label, ds.dates, a, b, ics, turnover, spread, horizon, lag)
        for label, (a, b) in ranges.items()
    }
    periods = _with_factor_neutral(ds, signal, ics[horizon], ranges, periods, horizon, lag)
    return SignalStatistics(
        signal=name,
        dataset=ds.scenario.id,
        horizon_days=horizon,
        universe_size=ds.n_symbols,
        periods=periods,
    )
