"""Backtest-overfitting diagnostics (0.10 D9): PBO by CSCV, and stationary block bootstrap intervals.

**Probability of backtest overfitting (PBO)** by combinatorially symmetric cross-validation (Bailey,
Borwein, Lopez de Prado and Zhu 2017, *The Probability of Backtest Overfitting*, J. Computational Finance
20(4)). Plain idea: if the configuration that looks best in-sample is usually below the median
out-of-sample, the selection process is fitting noise. Formally, the ``T x N`` matrix of daily returns of
the ``N`` trials is cut into ``S`` equal blocks of rows; for every way of choosing ``S / 2`` blocks as the
in-sample set ``J`` (the rest is the out-of-sample set), take ``n* = argmax_n SR_J(n)``, its relative
rank ``w`` among the out-of-sample Sharpe ratios and the logit ``l = log(w / (1 - w))``. PBO is the share
of splits with ``l <= 0``. Worked example: ``S = 16`` gives ``C(16, 8) = 12870`` splits; a PBO of 0.5 is
what pure noise produces, 0.1 means the in-sample winner is usually a genuine out-of-sample winner.

**Stationary bootstrap** (Politis and Romano 1994, JASA 89(428)): resample blocks of random geometric
length with mean ``b`` so serial dependence (overlapping IC, volatility clustering) survives in the
resamples. Used for the out-of-sample Sharpe and mean IC intervals, and (paired) for the difference of
two strategies on the same dates.

Evidence class: method (published estimators; this module implements them, it does not validate them on
real data).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from itertools import combinations
from typing import Any

import numpy as np

TRADING_DAYS = 252


def _sharpe_cols(r: np.ndarray) -> np.ndarray:
    mu = np.nanmean(r, axis=0)
    sd = np.nanstd(r, axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 0, mu / sd, np.nan)


def pbo_cscv(returns: np.ndarray, n_blocks: int = 16) -> dict[str, Any]:
    """PBO of selecting the best in-sample column of ``returns`` (``T x N``), by CSCV with ``n_blocks``."""
    r = np.asarray(returns, dtype=float)
    if r.ndim != 2 or r.shape[1] < 2:
        raise ValueError("returns must be T x N with at least two trials")
    if n_blocks < 2 or n_blocks % 2:
        raise ValueError("n_blocks must be an even number >= 2")
    t = (r.shape[0] // n_blocks) * n_blocks
    if t < 2 * n_blocks:
        raise ValueError("too few rows for the number of blocks")
    # 0.10 Q4: per-block sums make each split's Sharpe two matrix products, so the paper's S = 16
    # (12,870 splits) runs in well under a second; identical to the per-split nanmean / nanstd (ddof 1).
    blocks = r[:t].reshape(n_blocks, t // n_blocks, r.shape[1])
    fin = np.isfinite(blocks)
    xb = np.where(fin, blocks, 0.0)
    cnt, s1, s2 = fin.sum(axis=1).astype(float), xb.sum(axis=1), (xb * xb).sum(axis=1)
    combos = np.array(list(combinations(range(n_blocks), n_blocks // 2)))
    member = np.zeros((combos.shape[0], n_blocks))
    member[np.arange(combos.shape[0])[:, None], combos] = 1.0

    def split_sharpe(m: np.ndarray) -> np.ndarray:
        c, a, b = m @ cnt, m @ s1, m @ s2
        with np.errstate(divide="ignore", invalid="ignore"):
            mu = a / c
            sd = np.sqrt(np.maximum(b - c * mu * mu, 0.0) / (c - 1.0))
            return np.where((c >= 2) & (sd > 0), mu / sd, np.nan)

    sr_is_all, sr_oos_all = split_sharpe(member), split_sharpe(1.0 - member)
    n = r.shape[1]
    keep = np.isfinite(sr_is_all).any(axis=1)
    sr_is_all, sr_oos_all = sr_is_all[keep], sr_oos_all[keep]
    best = np.nanargmax(sr_is_all, axis=1) if sr_is_all.size else np.zeros(0, dtype=int)
    rows = np.arange(best.size)
    oos_best = sr_oos_all[rows, best]
    with np.errstate(invalid="ignore"):
        rank = (sr_oos_all < oos_best[:, None]).sum(axis=1).astype(float)  # rank 0 = worst; NaN never counts
    w = (rank + 1.0) / (n + 1.0)  # relative rank in (0, 1)
    lam = list(np.log(w / (1.0 - w)))
    degradations = list(zip(sr_is_all[rows, best].tolist(), oos_best.tolist(), strict=True))
    lam_arr = np.array(lam)
    deg = np.array(degradations)
    slope = float(np.polyfit(deg[:, 0], deg[:, 1], 1)[0]) if deg.shape[0] > 2 else float("nan")
    return {
        "pbo": float((lam_arr <= 0).mean()) if lam_arr.size else float("nan"),
        "n_splits": int(lam_arr.size),
        "n_blocks": n_blocks,
        "n_trials": n,
        "median_logit": float(np.median(lam_arr)) if lam_arr.size else float("nan"),
        "oos_on_is_slope": slope,  # performance degradation: OOS Sharpe regressed on IS Sharpe of the winner
        "prob_oos_loss": float((deg[:, 1] < 0).mean()) if deg.size else float("nan"),
    }


def stationary_bootstrap_indices(n: int, mean_block: float, rng: np.random.Generator) -> np.ndarray:
    """One resample of ``0..n-1`` (Politis-Romano): blocks start uniformly, lengths geometric(1/b), wrap."""
    p = 1.0 / max(mean_block, 1.0)
    idx = np.empty(n, dtype=int)
    i = int(rng.integers(n))
    starts = rng.random(n) < p
    fresh = rng.integers(0, n, n)
    for k in range(n):
        if k > 0:
            i = int(fresh[k]) if starts[k] else (i + 1) % n
        idx[k] = i
    return idx


def stationary_bootstrap_ci(
    x: np.ndarray,
    stat: Callable[[np.ndarray], float],
    mean_block: float = 20.0,
    n_boot: int = 1000,
    level: float = 0.95,
    seed: int = 0,
) -> dict[str, float]:
    """Percentile interval of ``stat`` under the stationary bootstrap; NaNs are dropped first."""
    v = np.asarray(x, dtype=float)
    v = v[np.isfinite(v)]
    if v.size < 10:
        return {"estimate": float("nan"), "low": float("nan"), "high": float("nan"), "n": float(v.size)}
    rng = np.random.default_rng(seed)
    draws = np.array([stat(v[stationary_bootstrap_indices(v.size, mean_block, rng)]) for _ in range(n_boot)])
    a = (1.0 - level) / 2.0
    return {"estimate": float(stat(v)), "low": float(np.nanquantile(draws, a)),
            "high": float(np.nanquantile(draws, 1.0 - a)), "n": float(v.size), "mean_block": float(mean_block)}


def annualised_sharpe(r: np.ndarray, periods_per_year: float = TRADING_DAYS) -> float:
    sd = float(np.std(r, ddof=1)) if r.size > 1 else 0.0
    return float(np.mean(r) / sd * math.sqrt(periods_per_year)) if sd > 0 else float("nan")


def sharpe_ci(r: np.ndarray, mean_block: float = 20.0, n_boot: int = 1000, seed: int = 0) -> dict[str, float]:
    """Stationary-bootstrap 95 % interval for an annualised daily Sharpe ratio."""
    return stationary_bootstrap_ci(r, annualised_sharpe, mean_block, n_boot, seed=seed)


def mean_ci(x: np.ndarray, mean_block: float = 6.0, n_boot: int = 1000, seed: int = 0) -> dict[str, float]:
    """Stationary-bootstrap 95 % interval for a mean (for example the mean IC of overlapping periods)."""
    return stationary_bootstrap_ci(x, lambda v: float(np.mean(v)), mean_block, n_boot, seed=seed)


def paired_sharpe_difference(
    a: np.ndarray, b: np.ndarray, mean_block: float = 20.0, n_boot: int = 1000, seed: int = 0
) -> dict[str, float]:
    """Paired stationary bootstrap of ``SR(a) - SR(b)`` on the same dates (both resampled with one index),
    with a two-sided bootstrap p-value for "no difference"."""
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if x.size < 10:
        return {"difference": float("nan"), "low": float("nan"), "high": float("nan"), "p_value": float("nan")}
    rng = np.random.default_rng(seed)
    d = []
    for _ in range(n_boot):
        i = stationary_bootstrap_indices(x.size, mean_block, rng)
        d.append(annualised_sharpe(x[i]) - annualised_sharpe(y[i]))
    arr = np.array(d)
    est = annualised_sharpe(x) - annualised_sharpe(y)
    centred = arr - arr.mean()
    p = float((np.abs(centred) >= abs(est)).mean())
    return {"difference": est, "low": float(np.quantile(arr, 0.025)), "high": float(np.quantile(arr, 0.975)),
            "p_value": p}


def diebold_mariano(loss_a: np.ndarray, loss_b: np.ndarray, lags: int | None = None) -> dict[str, float]:
    """Diebold-Mariano test of equal expected loss, ``d = loss_a - loss_b``, with a Newey-West variance
    (Andrews bandwidth when ``lags`` is None). Negative statistic: ``a`` has the lower loss."""
    from statistics import NormalDist

    from ceap.analytics.signal_statistics import newey_west_t

    d = np.asarray(loss_a, dtype=float) - np.asarray(loss_b, dtype=float)
    t = newey_west_t(d, lags)
    p = 2.0 * (1.0 - NormalDist().cdf(abs(t))) if math.isfinite(t) else float("nan")
    return {"statistic": float(t), "p_value": float(p), "n": float(np.isfinite(d).sum())}
