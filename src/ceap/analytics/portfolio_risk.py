"""Portfolio-level risk for a signal's target portfolio: exposure, concentration, limits, stress.

The target portfolio is *recomputed* from ``(signal, dataset, as_of)`` so the
risk view never depends on a backtest's output.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import numpy as np

from ceap.analytics.backtest import daily_returns, target_weights
from ceap.analytics.signals import compute_signal, cross_sectional_zscore
from ceap.domain.research import (
    DEFAULT_GROSS_NOTIONAL,
    PortfolioPosition,
    PortfolioRiskReport,
    PortfolioStressResult,
)
from ceap.domain.risk import LimitCheck

if TYPE_CHECKING:  # pragma: no cover
    from ceap.data.historical import HistoricalDataset

MIN_NAMES = 8
BETA_LOOKBACK = 252
PORTFOLIO = "PORTFOLIO"


@dataclass(frozen=True)
class PortfolioLimits:
    max_gross: float = 1.05
    max_net: float = 0.25
    max_abs_weight: float = 0.15
    max_adv_participation: float = 0.10
    max_abs_beta: float = 0.5
    max_hhi: float = 0.15

    @staticmethod
    def for_book(long_short: bool) -> PortfolioLimits:
        return PortfolioLimits() if long_short else PortfolioLimits(max_net=1.05, max_abs_beta=1.5)


def market_betas(close: np.ndarray, end_index: int, lookback: int = BETA_LOOKBACK) -> np.ndarray:
    """OLS beta of each name to the equal-weight market over the ``lookback`` days ending at ``end_index``."""
    ret = daily_returns(close)
    lo = max(1, end_index - lookback + 1)
    window = ret[lo : end_index + 1]
    m = close.shape[1]
    if window.shape[0] < 20:
        return np.ones(m)
    market = np.nanmean(window, axis=1)
    mkt = market - market.mean()
    var = float((mkt * mkt).sum())
    if var <= 0:
        return np.ones(m)
    centred = np.nan_to_num(window - np.nanmean(window, axis=0), nan=0.0)
    betas = (centred * mkt[:, None]).sum(axis=0) / var
    return np.where(np.isfinite(betas), betas, 1.0)


def target_portfolio(
    ds: HistoricalDataset,
    signal: str,
    as_of: str | None = None,
    long_short: bool = True,
    gross_notional: float = DEFAULT_GROSS_NOTIONAL,
    top_fraction: float = 1 / 3,
    weighting: str = "equal",
) -> PortfolioRiskReport:
    if gross_notional <= 0:
        raise ValueError("gross_notional must be positive")
    t = ds.index_at_or_before(as_of) if as_of else ds.n_days - 1
    row = compute_signal(signal, ds.close, ds.volume)[t]
    if np.isfinite(row).sum() < MIN_NAMES:
        raise ValueError(f"insufficient signal history for {signal} at {ds.dates[t]}")
    w = target_weights(cross_sectional_zscore(row), long_short, top_fraction, weighting)
    price = ds.close[t]
    notional = w * gross_notional
    with np.errstate(divide="ignore", invalid="ignore"):
        participation = np.abs(notional) / (ds.adv_20[t] * price)
    participation = np.where(np.isfinite(participation), participation, 0.0)
    betas = market_betas(ds.close, t)
    gross = float(np.abs(w).sum())
    hhi = float((w * w).sum() / gross**2) if gross > 0 else float("nan")
    held = np.flatnonzero(w != 0)
    held = held[np.argsort(-np.abs(w[held]), kind="stable")]
    positions = tuple(
        PortfolioPosition(
            symbol=ds.symbols[j],
            weight=round(float(w[j]), 6),
            notional=round(float(notional[j]), 2),
            quantity=int(round(notional[j] / price[j])) if price[j] > 0 else 0,
            price=round(float(price[j]), 4),
            adv_participation=round(float(participation[j]), 6),
        )
        for j in held
    )
    return PortfolioRiskReport(
        signal=signal,
        dataset=ds.scenario.id,
        as_of=str(ds.dates[t]),
        long_short=long_short,
        gross_notional=float(gross_notional),
        positions=positions,
        gross=round(gross, 6),
        net=round(float(w.sum()), 6),
        max_abs_weight=round(float(np.abs(w).max()) if w.size else 0.0, 6),
        hhi=round(hhi, 6),
        effective_names=round(1.0 / hhi, 3) if hhi and np.isfinite(hhi) and hhi > 0 else float("nan"),
        beta=round(float((w * betas).sum()), 4),
        max_adv_participation=round(float(participation[held].max()) if held.size else 0.0, 6),
    )


def check_limits(report: PortfolioRiskReport, limits: PortfolioLimits | None = None) -> tuple[LimitCheck, ...]:
    lim = limits or PortfolioLimits.for_book(report.long_short)

    def check(name: str, limit: float, observed: float, symbol: str = PORTFOLIO) -> LimitCheck:
        util = abs(observed) / limit if limit else float("inf")
        return LimitCheck(symbol, name, limit, observed, abs(observed) > limit, util)

    checks = [
        check("max_gross", lim.max_gross, report.gross),
        check("max_net", lim.max_net, report.net),
        check("max_abs_weight", lim.max_abs_weight, report.max_abs_weight),
        check("max_abs_beta", lim.max_abs_beta, report.beta),
        check("max_hhi", lim.max_hhi, report.hhi),
        check("max_adv_participation", lim.max_adv_participation, report.max_adv_participation),
    ]
    checks.extend(
        check("adv_participation", lim.max_adv_participation, p.adv_participation, symbol=p.symbol)
        for p in report.positions
        if p.adv_participation > lim.max_adv_participation
    )
    return tuple(checks)


def with_checks(report: PortfolioRiskReport, limits: PortfolioLimits | None = None) -> PortfolioRiskReport:
    return replace(report, checks=check_limits(report, limits))


def stress(report: PortfolioRiskReport, shock_bps: float = -500.0) -> PortfolioStressResult:
    n = report.gross_notional
    return PortfolioStressResult(
        shock_bps=float(shock_bps),
        pnl_market=round(n * report.beta * shock_bps / 10_000.0, 2),
        pnl_flat=round(n * report.net * shock_bps / 10_000.0, 2),
        worst_single_name=round(-n * report.max_abs_weight * abs(shock_bps) / 10_000.0, 2),
    )


def with_stress(report: PortfolioRiskReport, shock_bps: float = -500.0) -> PortfolioRiskReport:
    return replace(report, stress=stress(report, shock_bps))
