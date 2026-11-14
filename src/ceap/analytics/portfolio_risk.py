"""Portfolio-level risk for a signal's target portfolio: exposure, concentration, limits, stress.

The target portfolio is *recomputed* from ``(signal, dataset, as_of)`` so the
risk view never depends on a backtest's output.

0.4.0 (2.12, 2.13):

* **Beta** of each name against the equal-weight market *excluding that name*
  (no self-inclusion bias), shrunk toward 1 with the Vasicek (1973) weight
  ``w_i = s2_prior / (s2_prior + se_i^2)`` where ``s2_prior`` is the
  cross-sectional variance of the raw betas net of the mean sampling variance.
* **Stress** adds a generator-factor shock (the signal's factor reverses by
  ``factor_shock`` per unit of cross-sectional z-score), a short squeeze (+20 %
  on every short) and the cost of liquidating at 10 % of ADV (half spread plus
  square-root impact) with the days it takes.
* **Limits over history**: the limits are evaluated at every rebalance date of
  the out-of-sample period and the worst date per limit is reported, not only
  the as-of view.
* Non-members are never ranked or held.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import numpy as np

from ceap.analytics.backtest import daily_returns, rolling_vol_bps, target_weights
from ceap.analytics.numerics import negligible_variation
from ceap.analytics.signals import cross_sectional_zscore, dataset_signal
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
MIN_BETA_OBS = 60
PORTFOLIO = "PORTFOLIO"
FACTOR_SHOCK = 0.05  # return per unit z-score when the signal's factor reverses
SHORT_SQUEEZE = 0.20
LIQUIDATION_PARTICIPATION = 0.10
IMPACT_COEFFICIENT = 0.6


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


def raw_betas_ex_self(window: np.ndarray, min_obs: int = MIN_BETA_OBS) -> tuple[np.ndarray, np.ndarray]:
    """OLS beta of each column on the equal-weight mean of the *other* columns, and its standard error."""
    r = np.asarray(window, dtype=float)
    finite = np.isfinite(r)
    vals = np.where(finite, r, 0.0)
    total = vals.sum(axis=1, keepdims=True)
    count = finite.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        mkt = np.where(count - finite > 0, (total - vals) / (count - finite), np.nan)  # market excluding the name
    m = r.shape[1]
    beta = np.ones(m)
    se = np.full(m, np.inf)
    for j in range(m):
        ok = finite[:, j] & np.isfinite(mkt[:, j])
        n = int(ok.sum())
        if n < min_obs:
            continue
        x = mkt[ok, j]
        y = r[ok, j]
        xd = x - x.mean()
        sxx = float(xd @ xd)
        if negligible_variation(x, sxx):
            continue  # near-constant market: no estimate; se stays inf, which flags it to shrink_betas
        b = float(xd @ (y - y.mean())) / sxx
        resid = y - y.mean() - b * xd
        beta[j] = b
        se[j] = float(np.sqrt((resid @ resid) / (n - 2) / sxx))
    return beta, se


def shrink_betas(beta: np.ndarray, se: np.ndarray, target: float = 1.0) -> np.ndarray:
    """Vasicek shrinkage toward ``target``; names without an estimate get the target."""
    est = np.isfinite(se)
    out = np.full(beta.shape, target)
    if est.sum() < 2:
        out[est] = beta[est]
        return out
    b = beta[est]
    s2 = se[est] ** 2
    prior = max(float(b.var(ddof=1) - s2.mean()), 1e-6)
    w = prior / (prior + s2)
    out[est] = w * b + (1.0 - w) * target
    return out


def market_betas(close: np.ndarray, end_index: int, lookback: int = BETA_LOOKBACK) -> np.ndarray:
    """Shrunk beta of each name to the equal-weight market excluding itself, over ``lookback`` days."""
    ret = daily_returns(close)
    lo = max(1, end_index - lookback + 1)
    window = ret[lo : end_index + 1]
    if window.shape[0] < 20:
        return np.ones(close.shape[1])
    beta, se = raw_betas_ex_self(window, min(MIN_BETA_OBS, max(20, window.shape[0] // 2)))
    return shrink_betas(beta, se)


def _report_at(
    ds: HistoricalDataset,
    sig: np.ndarray,
    t: int,
    signal: str,
    long_short: bool,
    gross_notional: float,
    top_fraction: float,
    weighting: str,
    betas: np.ndarray | None = None,
) -> PortfolioRiskReport:
    row = sig[t]
    if np.isfinite(row).sum() < MIN_NAMES:
        raise ValueError(f"insufficient signal history for {signal} at {ds.dates[t]}")
    w = target_weights(cross_sectional_zscore(row), long_short, top_fraction, weighting)
    w[~ds.member_mask[t]] = 0.0
    price = ds.close[t]
    notional = w * gross_notional
    with np.errstate(divide="ignore", invalid="ignore"):
        participation = np.abs(notional) / (ds.adv_20[t] * price)
    participation = np.where(np.isfinite(participation), participation, 0.0)
    betas = market_betas(ds.close, t) if betas is None else betas
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
    sig = dataset_signal(ds, signal)
    return _report_at(ds, sig, t, signal, long_short, gross_notional, top_fraction, weighting)


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


def history_limit_checks(
    ds: HistoricalDataset,
    signal: str,
    long_short: bool = True,
    gross_notional: float = DEFAULT_GROSS_NOTIONAL,
    rebalance_days: int = 21,
    start_index: int | None = None,
    end_index: int | None = None,
    limits: PortfolioLimits | None = None,
) -> tuple[dict[str, Any], ...]:
    """Worst date per portfolio limit over the out-of-sample rebalance dates (2.12)."""
    sig = dataset_signal(ds, signal)
    a = ds.in_sample_end_index + 1 if start_index is None else start_index
    b = ds.n_days - 1 if end_index is None else end_index
    worst: dict[str, dict[str, Any]] = {}
    for t in range(a, b + 1, max(1, rebalance_days)):
        try:
            report = _report_at(ds, sig, t, signal, long_short, gross_notional, 1 / 3, "equal")
        except ValueError:
            continue
        for c in check_limits(report, limits):
            name = c.limit_name if c.symbol == PORTFOLIO else "adv_participation"
            cur = worst.get(name)
            if cur is None or c.utilisation > cur["utilisation"]:
                worst[name] = {
                    "limit_name": name,
                    "symbol": c.symbol,
                    "date": report.as_of,
                    "limit_value": c.limit_value,
                    "observed_value": c.observed_value,
                    "utilisation": round(float(c.utilisation), 6),
                    "breached": bool(c.breached),
                }
    return tuple(worst[k] for k in sorted(worst))


def with_checks(report: PortfolioRiskReport, limits: PortfolioLimits | None = None) -> PortfolioRiskReport:
    return replace(report, checks=check_limits(report, limits))


def stress(
    report: PortfolioRiskReport,
    shock_bps: float = -500.0,
    ds: HistoricalDataset | None = None,
    factor_shock: float = FACTOR_SHOCK,
) -> PortfolioStressResult:
    """Market shock through beta and net, worst single name; with ``ds`` also the factor shock, short
    squeeze and liquidation cost at 10 % ADV."""
    n = report.gross_notional
    pnl_factor = pnl_squeeze = liq_cost = liq_days = float("nan")
    weights = {p.symbol: p.weight for p in report.positions}
    pnl_squeeze = round(n * sum(w for w in weights.values() if w < 0) * SHORT_SQUEEZE, 2)
    if ds is not None and report.positions:
        t = ds.index_at_or_before(report.as_of)
        sig = dataset_signal(ds, report.signal)
        z = np.nan_to_num(cross_sectional_zscore(sig[t]), nan=0.0)
        vol = rolling_vol_bps(daily_returns(ds.close))[t]
        cost = 0.0
        days = 0.0
        factor = 0.0
        for p in report.positions:
            j = ds.col(p.symbol)
            factor += p.weight * (-factor_shock * z[j])
            dollars = abs(p.notional)
            adv_dollars = float(ds.adv_20[t, j] * ds.close[t, j])
            if not np.isfinite(adv_dollars) or adv_dollars <= 0:
                days = float("inf")
                continue
            days = max(days, dollars / (LIQUIDATION_PARTICIPATION * adv_dollars))
            v = vol[j] if np.isfinite(vol[j]) else 150.0
            hs = ds.spread_bps[t, j] / 2.0 if np.isfinite(ds.spread_bps[t, j]) else 0.0
            cost += dollars * (hs + IMPACT_COEFFICIENT * v * np.sqrt(LIQUIDATION_PARTICIPATION)) / 10_000.0
        pnl_factor = round(n * factor, 2)
        liq_cost = round(cost, 2)
        liq_days = round(days, 3)
    return PortfolioStressResult(
        shock_bps=float(shock_bps),
        pnl_market=round(n * report.beta * shock_bps / 10_000.0, 2),
        pnl_flat=round(n * report.net * shock_bps / 10_000.0, 2),
        worst_single_name=round(-n * report.max_abs_weight * abs(shock_bps) / 10_000.0, 2),
        pnl_factor=pnl_factor,
        pnl_short_squeeze=pnl_squeeze,
        liquidation_cost=liq_cost,
        liquidation_days=liq_days,
    )


def with_stress(
    report: PortfolioRiskReport, shock_bps: float = -500.0, ds: HistoricalDataset | None = None
) -> PortfolioRiskReport:
    return replace(report, stress=stress(report, shock_bps, ds))
