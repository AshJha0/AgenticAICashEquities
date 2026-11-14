"""Capacity sweep (2.6) and the per-signal profitability table (2.14).

* **Capacity.** The same backtest at gross notionals {10, 25, 50, 100, 250, 500} M
  with the participation cap active: impact grows with the square root of
  participation and capped trades leave the book short of its target. Reported:
  net Sharpe and its standard error per AUM (Lo 2002, iid:
  ``se = sqrt((1 + SR_d^2 / 2) / T) * sqrt(252)``), the breakeven AUM where
  the net excess return crosses zero and the AUM at which the net Sharpe halves
  relative to the smallest AUM (both log-linearly interpolated).
* **Profitability.** Breakeven trading cost per unit of turnover (the cost at
  which the net excess return would be zero), cost share of gross P&L at each
  AUM, drawdown depth and duration, calendar-year returns, the worst months,
  and alpha after the generator's factor model (OLS of daily excess returns on
  the generator's market factor, with its t-statistic).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np

from ceap.analytics.backtest import TRADING_DAYS_PER_YEAR, BacktestRun, simulate_backtest
from ceap.analytics.numerics import negligible_variation

if TYPE_CHECKING:  # pragma: no cover
    from ceap.data.historical import HistoricalDataset

CAPACITY_NOTIONALS: tuple[float, ...] = (10e6, 25e6, 50e6, 100e6, 250e6, 500e6)
WORST_MONTHS = 3


def sharpe_standard_error(sharpe_ann: float, n_days: int) -> float:
    if not math.isfinite(sharpe_ann) or n_days < 2:
        return float("nan")
    sr = sharpe_ann / math.sqrt(TRADING_DAYS_PER_YEAR)
    return math.sqrt((1.0 + 0.5 * sr * sr) / n_days) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _interp_log_crossing(xs: list[float], ys: list[float], level: float) -> float | None:
    """First AUM where ``ys`` falls through ``level`` (log-linear in AUM); ``None`` if it never does."""
    for (x0, y0), (x1, y1) in zip(zip(xs, ys, strict=True), zip(xs[1:], ys[1:], strict=True), strict=False):
        if math.isfinite(y0) and math.isfinite(y1) and y0 >= level > y1:
            f = (y0 - level) / (y0 - y1)
            return float(math.exp(math.log(x0) + f * (math.log(x1) - math.log(x0))))
    return None


def capacity_sweep(
    ds: HistoricalDataset, signal: str, notionals: tuple[float, ...] = CAPACITY_NOTIONALS, **kwargs: Any
) -> dict[str, Any]:
    rows = []
    for notional in notionals:
        run = simulate_backtest(ds, signal, gross_notional=notional, report_bounds=False, **kwargs)
        full = run.result.period("full")
        assert full is not None
        gross_pnl = full.gross_ann_return
        rows.append(
            {
                "gross_notional": notional,
                "net_sharpe": full.sharpe,
                "sharpe_se": sharpe_standard_error(full.sharpe, full.n_days),
                "gross_sharpe": full.gross_sharpe,
                "net_excess_ann_return": full.excess_ann_return,
                "cost_drag_bps_annual": full.cost_drag_bps_annual,
                "cost_share_of_gross": (full.cost_drag_bps_annual / 10_000.0) / gross_pnl
                if math.isfinite(gross_pnl) and gross_pnl > 0
                else float("nan"),
                "participation_capped_trades": run.result.event_counts.get("participation_cap", 0),
            }
        )
    xs = [r["gross_notional"] for r in rows]
    breakeven = _interp_log_crossing(xs, [r["net_excess_ann_return"] for r in rows], 0.0)
    base = rows[0]["net_sharpe"]
    half = _interp_log_crossing(xs, [r["net_sharpe"] for r in rows], base / 2.0) if base > 0 else None
    sharpes = [r["net_sharpe"] for r in rows]
    from ceap.analytics.signal_statistics import spearman

    return {
        "rows": rows,
        "spearman_notional_sharpe": spearman(np.array(xs), np.array(sharpes)),
        "breakeven_aum": breakeven,
        "breakeven_note": None if breakeven is not None else (
            "net excess return positive at every AUM tested" if rows[-1]["net_excess_ann_return"] > 0
            else "net excess return negative at every AUM tested"
        ),
        "half_sharpe_aum": half,
    }


def _drawdown(net: np.ndarray, dates: np.ndarray) -> dict[str, Any]:
    equity = np.cumprod(1.0 + net)
    peak = np.maximum.accumulate(equity)
    dd = 1.0 - equity / peak
    depth = float(dd.max()) if dd.size else float("nan")
    longest = cur = 0
    for under in dd > 1e-12:
        cur = cur + 1 if under else 0
        longest = max(longest, cur)
    trough = int(np.argmax(dd)) if dd.size else 0
    return {"max_drawdown": depth, "trough_date": str(dates[trough]) if dd.size else None,
            "longest_drawdown_days": int(longest)}


def _calendar(net: np.ndarray, dates: np.ndarray, unit: str) -> dict[str, float]:
    keys = dates.astype(f"datetime64[{unit}]").astype(str)
    out: dict[str, float] = {}
    for k in np.unique(keys):
        out[str(k)] = float(np.prod(1.0 + net[keys == k]) - 1.0)
    return out


def factor_alpha(excess: np.ndarray, factor: np.ndarray) -> dict[str, float]:
    """OLS ``excess = a + b * factor + e``: annualised alpha, beta and the alpha t-statistic."""
    ok = np.isfinite(excess) & np.isfinite(factor)
    y, x = excess[ok], factor[ok]
    n = y.size
    if n < 30:
        return {"alpha_ann": float("nan"), "beta": float("nan"), "alpha_t": float("nan")}
    xd = x - x.mean()
    sxx = float(xd @ xd)
    if negligible_variation(x, sxx):
        return {"alpha_ann": float("nan"), "beta": float("nan"), "alpha_t": float("nan"),
                "note": "beta undefined: the factor series is constant to within tolerance"}
    b = float(xd @ (y - y.mean())) / sxx
    a = float(y.mean() - b * x.mean())
    resid = y - a - b * x
    s2 = float(resid @ resid) / (n - 2)
    se_a = math.sqrt(s2 * (1.0 / n + x.mean() ** 2 / sxx))
    return {"alpha_ann": a * TRADING_DAYS_PER_YEAR, "beta": b, "alpha_t": a / se_a if se_a > 0 else float("nan")}


def profitability_from_run(run: BacktestRun, ds: HistoricalDataset, capacity: dict[str, Any] | None) -> dict[str, Any]:
    full = run.result.period("full")
    assert full is not None
    sl = slice(run.i0, run.i1 + 1)
    net, rf = run.net[sl], run.rf[sl]
    dates = ds.dates[sl]
    excess = net - rf
    breakdown = run.result.cost_breakdown_bps_annual
    trade_cost = (breakdown.get("spread", 0.0) + breakdown.get("impact", 0.0)) / 10_000.0
    turnover = full.turnover_annual
    pre_trade_cost_excess = full.excess_ann_return + trade_cost
    breakeven = pre_trade_cost_excess / turnover * 10_000.0 if turnover and turnover > 0 else float("nan")
    months = _calendar(net, dates, "M")
    worst = sorted(months.items(), key=lambda kv: kv[1])[:WORST_MONTHS]
    market = ds.market_return[sl] - rf if ds.market_return is not None else np.full(net.shape, np.nan)
    table: dict[str, Any] = {
        "signal": run.result.signal,
        "dataset": run.result.dataset,
        "gross_notional": run.result.gross_notional,
        "net_sharpe": full.sharpe,
        "net_excess_ann_return": full.excess_ann_return,
        "turnover_annual": turnover,
        "breakeven_cost_bps_per_unit_turnover": breakeven,
        "cost_breakdown_bps_annual": dict(breakdown),
        **_drawdown(net, dates),
        "calendar_year_returns": _calendar(net, dates, "Y"),
        "worst_months": [{"month": m, "return": r} for m, r in worst],
        "factor_model": {"factor": "generator market factor m_t", **factor_alpha(excess, market)},
    }
    if capacity is not None:
        table["capacity"] = capacity
        table["cost_share_by_aum"] = {
            f"{int(r['gross_notional'] / 1e6)}M": r["cost_share_of_gross"] for r in capacity["rows"]
        }
    return table


def profitability_table(
    ds: HistoricalDataset, signal: str, with_capacity: bool = True, **kwargs: Any
) -> dict[str, Any]:
    """The 2.14 table for one signal configuration (one command: ``ceap profitability``)."""
    run = simulate_backtest(ds, signal, report_bounds=False, **kwargs)
    sweep_kwargs = {k: v for k, v in kwargs.items() if k != "gross_notional"}
    capacity = capacity_sweep(ds, signal, **sweep_kwargs) if with_capacity else None
    return profitability_from_run(run, ds, capacity)


def json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    return value
