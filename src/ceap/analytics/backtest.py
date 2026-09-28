"""Event-driven daily backtest with a transaction-cost model and a walk-forward split.

At every rebalance date the target portfolio is rebuilt from the signal's
cross-sectional z-scores and traded at the close; the cost of the trade
(half spread plus square-root impact on ADV participation) is charged that
day. Weights are held until the next rebalance. Statistics are reported for
the in-sample, out-of-sample and full periods.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ceap.analytics.signals import compute_signal, cross_sectional_zscore, get_signal
from ceap.domain.research import DEFAULT_GROSS_NOTIONAL, BacktestPeriodStats, BacktestResult

if TYPE_CHECKING:  # pragma: no cover
    from ceap.data.historical import HistoricalDataset

TRADING_DAYS_PER_YEAR = 252
MIN_NAMES = 8
EQUITY_CURVE_POINTS = 24
TOP_CONTRIBUTORS = 5


@dataclass(frozen=True)
class CostModel:
    impact_coefficient: float = 0.6
    vol_window: int = 20
    default_vol_bps: float = 150.0

    def trade_cost_bps(
        self, half_spread_bps: np.ndarray, daily_vol_bps: np.ndarray, participation: np.ndarray
    ) -> np.ndarray:
        vol = np.where(np.isfinite(daily_vol_bps), daily_vol_bps, self.default_vol_bps)
        part = np.clip(np.nan_to_num(np.asarray(participation, dtype=float), nan=0.0), 0.0, None)
        return np.asarray(half_spread_bps, dtype=float) + self.impact_coefficient * vol * np.sqrt(part)


def target_weights(
    z_row: np.ndarray, long_short: bool = True, top_fraction: float = 1 / 3, weighting: str = "equal"
) -> np.ndarray:
    """Portfolio weights from one date's z-scores; ``sum(|w|) == 1`` when the cross-section is usable."""
    z = np.asarray(z_row, dtype=float)
    w = np.zeros(z.shape)
    valid = np.flatnonzero(np.isfinite(z))
    if valid.size < 2:
        return w
    k = max(1, int(round(valid.size * top_fraction)))
    k = min(k, valid.size // 2) if long_short else min(k, valid.size)
    order = valid[np.argsort(z[valid], kind="stable")]
    longs = order[-k:]
    shorts = order[:k]
    if weighting == "rank":
        long_w = np.arange(1, k + 1, dtype=float)
        short_w = np.arange(k, 0, -1, dtype=float)
    else:
        long_w = np.ones(k)
        short_w = np.ones(k)
    if long_short:
        w[longs] = 0.5 * long_w / long_w.sum()
        w[shorts] = -0.5 * short_w / short_w.sum()
    else:
        w[longs] = long_w / long_w.sum()
    return w


def daily_returns(close: np.ndarray) -> np.ndarray:
    c = np.asarray(close, dtype=float)
    out = np.full(c.shape, np.nan)
    if c.shape[0] > 1:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[1:] = c[1:] / c[:-1] - 1.0
    return out


def rolling_vol_bps(returns: np.ndarray, window: int = 20) -> np.ndarray:
    r = np.asarray(returns, dtype=float)
    out = np.full(r.shape, np.nan)
    if r.shape[0] > window:
        windows = np.lib.stride_tricks.sliding_window_view(r[1:], window, axis=0)
        out[window:] = windows.std(axis=-1, ddof=1) * 10_000.0
    return out


def _downsample_indices(n: int, max_points: int) -> np.ndarray:
    if n <= max_points:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, max_points).round().astype(int))


def period_stats(
    name: str,
    dates: np.ndarray,
    i0: int,
    i1: int,
    net: np.ndarray,
    gross: np.ndarray,
    turnover: np.ndarray,
    n_rebalances: int,
) -> BacktestPeriodStats:
    sl = slice(i0, i1 + 1)
    r = net[sl]
    g = gross[sl]
    n = int(r.size)
    nan = float("nan")
    cagr = ann_return = ann_vol = sharpe = sortino = max_dd = hit = nan
    gross_sharpe = gross_ann = nan
    if n >= 2:
        ann_return = float(r.mean() * TRADING_DAYS_PER_YEAR)
        gross_ann = float(g.mean() * TRADING_DAYS_PER_YEAR)
        growth = 1.0 + r
        cagr = float(np.prod(growth) ** (TRADING_DAYS_PER_YEAR / n) - 1.0) if (growth > 0).all() else -1.0
        std = float(r.std(ddof=1))
        gstd = float(g.std(ddof=1))
        ann_vol = std * np.sqrt(TRADING_DAYS_PER_YEAR)
        if std > 0:
            sharpe = float(r.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))
        if gstd > 0:
            gross_sharpe = float(g.mean() / gstd * np.sqrt(TRADING_DAYS_PER_YEAR))
        downside = float(np.sqrt(np.mean(np.minimum(r, 0.0) ** 2)))
        if downside > 0:
            sortino = float(r.mean() / downside * np.sqrt(TRADING_DAYS_PER_YEAR))
        equity = np.cumprod(growth)
        max_dd = float((1.0 - equity / np.maximum.accumulate(equity)).max())
        hit = float((r > 0).mean())
    years = n / TRADING_DAYS_PER_YEAR if n else nan
    return BacktestPeriodStats(
        name=name,
        start=str(dates[i0]),
        end=str(dates[i1]),
        n_days=n,
        cagr=cagr,
        ann_return=ann_return,
        ann_vol=float(ann_vol),
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_dd,
        turnover_annual=float(turnover[sl].sum() / years) if years and years > 0 else nan,
        hit_rate=hit,
        gross_sharpe=gross_sharpe,
        gross_ann_return=gross_ann,
        cost_drag_bps_annual=float((gross_ann - ann_return) * 10_000.0) if n >= 2 else nan,
        n_rebalances=n_rebalances,
    )


def run_backtest(
    ds: HistoricalDataset,
    signal: str,
    start: str | None = None,
    end: str | None = None,
    in_sample_end: str | None = None,
    rebalance_days: int | None = None,
    long_short: bool = True,
    cost_multiplier: float = 1.0,
    gross_notional: float = DEFAULT_GROSS_NOTIONAL,
    weighting: str = "equal",
    costs: CostModel | None = None,
    top_fraction: float = 1 / 3,
) -> BacktestResult:
    spec = get_signal(signal)
    rebalance = int(spec.default_rebalance_days if rebalance_days is None else rebalance_days)
    if rebalance <= 0:
        raise ValueError("rebalance_days must be positive")
    if gross_notional <= 0:
        raise ValueError("gross_notional must be positive")
    costs = costs or CostModel()
    i0, i1 = ds.index_range(start, end)
    split = ds.index_at_or_before(in_sample_end) if in_sample_end else ds.in_sample_end_index
    split = min(max(split, i0), i1)

    sig = compute_signal(signal, ds.close, ds.volume)
    ret = daily_returns(ds.close)
    vol_bps = rolling_vol_bps(ret, costs.vol_window)
    n, m = ds.close.shape
    w = np.zeros(m)
    gross = np.zeros(n)
    cost = np.zeros(n)
    turnover = np.zeros(n)
    contrib = np.zeros((n, m))
    rebalances: list[int] = []

    for t in range(i0, i1 + 1):
        if t > i0:
            r_t = np.nan_to_num(ret[t], nan=0.0)
            contrib[t] = w * r_t
            gross[t] = float(contrib[t].sum())
        if (t - i0) % rebalance == 0:
            row = sig[t]
            if np.isfinite(row).sum() >= MIN_NAMES:
                w_new = target_weights(cross_sectional_zscore(row), long_short, top_fraction, weighting)
                dw = np.abs(w_new - w)
                with np.errstate(divide="ignore", invalid="ignore"):
                    participation = dw * gross_notional / (ds.adv_20[t] * ds.close[t])
                cost_bps = costs.trade_cost_bps(ds.spread_bps[t] / 2.0, vol_bps[t], participation)
                cost[t] = float((dw * cost_bps).sum() / 10_000.0 * cost_multiplier)
                turnover[t] = float(dw.sum())
                rebalances.append(t)
                w = w_new
    net = gross - cost

    def count(a: int, b: int) -> int:
        return sum(1 for t in rebalances if a <= t <= b)

    oos0 = min(split + 1, i1)
    periods = {
        "in_sample": period_stats("in_sample", ds.dates, i0, split, net, gross, turnover, count(i0, split)),
        "out_of_sample": period_stats(
            "out_of_sample", ds.dates, oos0, i1, net, gross, turnover, count(oos0, i1)
        ),
        "full": period_stats("full", ds.dates, i0, i1, net, gross, turnover, len(rebalances)),
    }
    is_sharpe = periods["in_sample"].sharpe
    oos_sharpe = periods["out_of_sample"].sharpe
    ratio = float(oos_sharpe / is_sharpe) if np.isfinite(is_sharpe) and is_sharpe > 0 and np.isfinite(oos_sharpe) else float("nan")

    equity = np.cumprod(1.0 + net[i0 : i1 + 1])
    idx = _downsample_indices(equity.size, EQUITY_CURVE_POINTS)
    curve = tuple((str(ds.dates[i0 + i]), round(float(equity[i]), 6)) for i in idx)

    contributions = contrib[i0 : i1 + 1].sum(axis=0)
    order = np.argsort(contributions)[::-1]
    top = tuple((ds.symbols[j], round(float(contributions[j]), 6)) for j in order[:TOP_CONTRIBUTORS])
    positive = contributions[contributions > 0]
    concentration = float(np.sort(positive)[::-1][:3].sum() / positive.sum()) if positive.size else float("nan")

    return BacktestResult(
        signal=signal,
        dataset=ds.scenario.id,
        rebalance_days=rebalance,
        long_short=long_short,
        gross_notional=float(gross_notional),
        periods=periods,
        sharpe_ratio_oos_is=ratio,
        equity_curve=curve,
        top_contributors=top,
        pnl_concentration_top3=concentration,
    )
