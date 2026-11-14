"""Research risk and robustness toolkit (0.10 D6, D13, D16, D17, D18, D19).

Every function takes plain arrays (daily or monthly returns, weights, factor returns), so it works the
same on a synthetic backtest and on the real-data study. Each states its formula and source.

* :func:`ledoit_wolf` - shrinkage covariance toward a scaled identity (Ledoit and Wolf 2004, *A
  well-conditioned estimator for large-dimensional covariance matrices*, JMVA 88). With 30 names and a
  year of data the sample covariance is badly conditioned; shrinkage fixes the smallest eigenvalues.
* :func:`factor_regression` - regression of book returns on factor returns (FF5 + UMD in the real study)
  with Newey-West standard errors: loadings, R^2 and the **alpha t after factors** (D16).
* :func:`residualise` - cross-sectional residuals of a signal on exposures (industry dummies, betas):
  factor-neutral research asks whether a signal predicts what the factors do not (D16).
* :func:`multi_factor_stress` - P&L of a book under joint factor shocks (D13).
* :func:`tail_report` - drawdown, historical CVaR, skew, kurtosis and the worst months (D19).
* :func:`regime_split` - Sharpe in calm and stressed volatility regimes (D19, D6).
* :func:`turnover_penalised_weights` - mean-variance weights with an L2 turnover penalty in closed form,
  ``w = (gamma S + tau I)^-1 (mu + tau w_prev)`` (D18), and :func:`cost_frontier` for the net-Sharpe
  against turnover frontier (D17).
* :func:`robustness_report` - one verdict's stability across halves, sub-periods, volatility regimes,
  momentum lookback and skip neighbours and cost multipliers 0.5/1/2/3 (D6).

Evidence class: method. Nothing here is fitted to the synthetic generator.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ceap.analytics.numerics import negligible_variation

TRADING_DAYS = 252


# ---------------------------------------------------------------- D13 covariance
def ledoit_wolf(returns: np.ndarray) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf (2004) shrinkage of the sample covariance toward ``mean(diag) * I``: ``(Sigma, delta)``."""
    x = np.asarray(returns, dtype=float)
    x = x[np.isfinite(x).all(axis=1)]
    t, n = x.shape
    if t < 2:
        raise ValueError("need at least two complete rows")
    xc = x - x.mean(axis=0)
    s = xc.T @ xc / t
    mu = float(np.trace(s)) / n
    target = mu * np.eye(n)
    d2 = float(((s - target) ** 2).sum())
    b2_bar = sum(float(((np.outer(r, r) - s) ** 2).sum()) for r in xc) / (t * t)
    b2 = min(b2_bar, d2)
    delta = b2 / d2 if d2 > 0 else 1.0
    return delta * target + (1.0 - delta) * s, float(delta)


# ---------------------------------------------------------------- D13 / D16 factor regression
def factor_regression(y: np.ndarray, factors: np.ndarray, names: tuple[str, ...], lags: int | None = None,
                      periods_per_year: float = 12.0) -> dict[str, Any]:
    """OLS of ``y`` on an intercept and ``factors`` with Newey-West (Bartlett) standard errors."""
    yv = np.asarray(y, dtype=float)
    f = np.asarray(factors, dtype=float).reshape(len(yv), -1)
    ok = np.isfinite(yv) & np.isfinite(f).all(axis=1)
    yv, f = yv[ok], f[ok]
    n, k = f.shape
    if n <= k + 2:
        raise ValueError("too few observations for the regression")
    x = np.column_stack([np.ones(n), f])
    xtx_inv = np.linalg.inv(x.T @ x)
    beta = xtx_inv @ x.T @ yv
    e = yv - x @ beta
    L = int(math.floor(4 * (n / 100.0) ** (2.0 / 9.0))) if lags is None else int(lags)  # noqa: N806
    xe = x * e[:, None]
    meat = xe.T @ xe
    for lag in range(1, L + 1):
        w = 1.0 - lag / (L + 1.0)
        g = xe[lag:].T @ xe[:-lag]
        meat += w * (g + g.T)
    cov = xtx_inv @ meat @ xtx_inv
    se = np.sqrt(np.maximum(np.diag(cov), 0.0))
    tss = float(((yv - yv.mean()) ** 2).sum())
    flat = negligible_variation(yv, tss)
    return {
        "alpha_per_period": float(beta[0]), "alpha_ann": float(beta[0] * periods_per_year),
        "alpha_t": float(beta[0] / se[0]) if se[0] > 0 else float("nan"),
        "loadings": {nm: float(b) for nm, b in zip(names, beta[1:], strict=True)},
        "loading_t": {nm: float(b / s) if s > 0 else float("nan") for nm, b, s in zip(names, beta[1:], se[1:],
                                                                                        strict=True)},
        "r2": float("nan") if flat else 1.0 - float(e @ e) / tss, "n": n, "nw_lags": L,
        **({"r2_note": "R-squared undefined: the dependent series is constant to within tolerance"} if flat else {}),
    }


def residualise(signal_row: np.ndarray, exposures: np.ndarray) -> np.ndarray:
    """Cross-sectional OLS residual of one date's signal on ``exposures`` (names x k, intercept added)."""
    s = np.asarray(signal_row, dtype=float)
    x = np.asarray(exposures, dtype=float).reshape(len(s), -1)
    ok = np.isfinite(s) & np.isfinite(x).all(axis=1)
    out = np.full(s.shape, np.nan)
    if ok.sum() <= x.shape[1] + 1:
        return out
    xm = np.column_stack([np.ones(int(ok.sum())), x[ok]])
    coef, *_ = np.linalg.lstsq(xm, s[ok], rcond=None)
    out[ok] = s[ok] - xm @ coef
    return out


def multi_factor_stress(loadings: dict[str, float], scenarios: dict[str, dict[str, float]]) -> dict[str, float]:
    """Book return under each joint factor shock: ``sum_f loading_f * shock_f`` (linear, no convexity)."""
    return {name: float(sum(loadings.get(f, 0.0) * v for f, v in shock.items())) for name, shock in scenarios.items()}


# Joint monthly factor shocks (decimal), each from a named historical month in the Ken French library.
# Evidence class: data (values rounded from the library's monthly files).
STRESS_SCENARIOS: dict[str, dict[str, float]] = {
    "2008-10 crisis": {"mkt_rf": -0.17, "smb": -0.02, "hml": -0.02, "umd": 0.08},
    "2009-04 momentum crash": {"mkt_rf": 0.10, "smb": 0.07, "hml": 0.06, "umd": -0.34},
    "2020-03 covid": {"mkt_rf": -0.13, "smb": -0.05, "hml": -0.14, "umd": 0.07},
    "2020-11 vaccine rotation": {"mkt_rf": 0.12, "smb": 0.06, "hml": 0.02, "umd": -0.12},
}


# ---------------------------------------------------------------- D19 tails and regimes
def tail_report(daily: np.ndarray, dates: np.ndarray | None = None, level: float = 0.95) -> dict[str, Any]:
    """Max drawdown, historical VaR/CVaR at ``level``, skew, kurtosis and the five worst 21-day spells."""
    r = np.asarray(daily, dtype=float)
    r = np.where(np.isfinite(r), r, 0.0)
    nav = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(nav)
    dd = nav / peak - 1.0
    q = float(np.quantile(r, 1.0 - level))
    tail = r[r <= q]
    sd = float(r.std())
    z = (r - r.mean()) / sd if sd > 0 else np.zeros_like(r)
    roll = np.convolve(r, np.ones(21), "valid")
    worst_idx = np.argsort(roll)[:5]
    spells = [{"start": str(dates[i]) if dates is not None else int(i), "return": float(roll[i])} for i in worst_idx]
    return {"max_drawdown": float(dd.min()), "var": -q, "cvar": -float(tail.mean()) if tail.size else float("nan"),
            "skew": float((z**3).mean()), "kurtosis": float((z**4).mean()), "worst_21d": spells, "level": level}


def regime_split(daily: np.ndarray, market: np.ndarray, window: int = 63, quantile: float = 0.8) -> dict[str, float]:
    """Annualised Sharpe of ``daily`` on days whose trailing market vol is below / above its ``quantile``.
    The trailing window ends the day before, so the regime label uses no same-day information."""
    r = np.asarray(daily, dtype=float)
    m = np.asarray(market, dtype=float)
    vol = np.full(m.shape, np.nan)
    for t in range(window + 1, m.size):
        vol[t] = np.nanstd(m[t - window - 1 : t - 1])
    ok = np.isfinite(vol) & np.isfinite(r)
    cut = float(np.quantile(vol[ok], quantile))

    def sr(x: np.ndarray) -> float:
        s = float(x.std(ddof=1)) if x.size > 1 else 0.0
        return float(x.mean() / s * math.sqrt(TRADING_DAYS)) if s > 0 else float("nan")

    calm, stressed = r[ok & (vol <= cut)], r[ok & (vol > cut)]
    return {"calm_sharpe": sr(calm), "stressed_sharpe": sr(stressed), "calm_days": float(calm.size),
            "stressed_days": float(stressed.size), "vol_cut": cut}


# ---------------------------------------------------------------- D18 / D17 construction and frontier
def turnover_penalised_weights(mu: np.ndarray, cov: np.ndarray, w_prev: np.ndarray, gamma: float = 1.0,
                               tau: float = 0.0) -> np.ndarray:
    """Maximise ``mu'w - gamma/2 w'Sw - tau/2 |w - w_prev|^2``: ``w = (gamma S + tau I)^-1 (mu + tau w_prev)``.
    ``tau = 0`` is plain mean-variance; ``tau -> inf`` keeps the old book. An L2 penalty is a smooth stand-in
    for proportional costs (Garleanu and Pedersen 2013 use the same quadratic form)."""
    s = np.asarray(cov, dtype=float)
    n = s.shape[0]
    return np.linalg.solve(gamma * s + tau * np.eye(n), np.asarray(mu, dtype=float) + tau * np.asarray(w_prev))


def cost_frontier(points: list[dict[str, float]]) -> list[dict[str, float]]:
    """The efficient subset of ``{"turnover", "net_sharpe", ...}`` points: no other point has lower turnover
    and a higher net Sharpe. Sorted by turnover."""
    pts = sorted(points, key=lambda p: (p["turnover"], -p["net_sharpe"]))
    out: list[dict[str, float]] = []
    best = -math.inf
    for p in pts:
        if p["net_sharpe"] > best:
            out.append(p)
            best = p["net_sharpe"]
    return out


# ---------------------------------------------------------------- D6 robustness
def _momentum_variant(lookback: int, skip: int) -> Any:
    def fn(close: np.ndarray, volume: np.ndarray | None = None) -> np.ndarray:
        c = np.asarray(close, dtype=float)
        out = np.full(c.shape, np.nan)
        if c.shape[0] > lookback:
            with np.errstate(divide="ignore", invalid="ignore"):
                out[lookback:] = c[lookback - skip : c.shape[0] - skip] / c[: c.shape[0] - lookback] - 1.0
        return out

    return fn


def robustness_report(ds: Any, signal: str = "momentum_12_1", rebalance_days: int = 21,
                      cost_multipliers: tuple[float, ...] = (0.5, 1.0, 2.0, 3.0),
                      neighbours: tuple[tuple[int, int], ...] = ((189, 21), (252, 21), (315, 21), (252, 5),
                                                                 (252, 42))) -> dict[str, Any]:
    """Net Sharpe of one configuration across cost multipliers, halves of the full period, three equal
    sub-periods, calm / stressed volatility regimes and (momentum only) lookback / skip neighbours."""
    from ceap.analytics.backtest import simulate_backtest
    from ceap.analytics.signals import SIGNALS, SignalSpec

    def sr(x: np.ndarray) -> float:
        s = float(x.std(ddof=1)) if x.size > 1 else 0.0
        return float(x.mean() / s * math.sqrt(TRADING_DAYS)) if s > 0 else float("nan")

    costs = {}
    base = None
    for k in cost_multipliers:
        run = simulate_backtest(ds, signal, rebalance_days=rebalance_days, cost_multiplier=k, report_bounds=False)
        full = run.result.period("full")
        costs[f"x{k:g}"] = float(full.sharpe) if full is not None else float("nan")
        if k == 1.0:
            base = run
    assert base is not None
    net = (base.net - base.rf)[base.i0 : base.i1 + 1]
    half = net.size // 2
    thirds = np.array_split(net, 3)
    out: dict[str, Any] = {
        "signal": signal, "rebalance_days": rebalance_days, "cost_multiplier": costs,
        "halves": [sr(net[:half]), sr(net[half:])], "sub_periods": [sr(x) for x in thirds],
        "regimes": regime_split(net, np.asarray(ds.market_return)[base.i0 : base.i1 + 1]),
    }
    if signal == "momentum_12_1":
        nb = {}
        for lb, sk in neighbours:
            name = f"_robust_mom_{lb}_{sk}"
            SIGNALS[name] = SignalSpec(name, "momentum neighbour", lb, 21, 21, _momentum_variant(lb, sk))
            try:
                run = simulate_backtest(ds, name, rebalance_days=rebalance_days, report_bounds=False)
            finally:
                del SIGNALS[name]
            full = run.result.period("full")
            nb[f"{lb}-{sk}"] = float(full.sharpe) if full is not None else float("nan")
        out["lookback_skip"] = nb
    values = [v for v in [*costs.values(), *out["halves"], *out["sub_periods"]] if math.isfinite(v)]
    out["sign_stable"] = bool(values) and (all(v > 0 for v in values) or all(v <= 0 for v in values))
    return out
