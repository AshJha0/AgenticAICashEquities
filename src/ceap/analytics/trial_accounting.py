"""Trial accounting for the deflated Sharpe ratio (0.10 D8).

The deflated Sharpe ratio (Bailey and Lopez de Prado 2014) needs two numbers about the search that produced
a strategy: how many *independent* trials were run, and the variance of their Sharpe ratios. Both are easy
to get wrong in the direction that flatters the result:

* **Too few trials.** A ledger only knows the configurations that were actually backtested on this dataset.
  The search the agent *could* have run is the enumerated search space, and a study that was preregistered
  with ``k`` variants has at least ``k`` trials whatever the ledger says. So the count is floored at the
  preregistered (or enumerated) variants.
* **Correlated trials counted as independent.** Twenty-four configurations built from three signals are not
  twenty-four independent draws: long-only and long-short books of the same signal correlate strongly. The
  effective number of trials is estimated from the correlation matrix of the trials' return series, either
  by the participation ratio of its eigenvalues, ``N_eff = (sum l)^2 / sum l^2``, or by counting clusters
  of trials joined when their correlation is at least a threshold (single linkage).
* **Variance on the wrong horizon.** The expected-maximum Sharpe ``SR0`` must be on the same horizon as the
  sample length ``T`` in the DSR formula. :func:`per_period_sharpe_variance` converts annualised Sharpe
  ratios to per-period ones before taking the variance.

Evidence class: method (published formula; Bailey and Lopez de Prado 2014, *The Deflated Sharpe Ratio*).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def effective_trials_eigen(corr: np.ndarray) -> float:
    """Participation ratio of the correlation matrix's eigenvalues: 1 for identical trials, N for independent."""
    c = np.asarray(corr, dtype=float)
    if c.ndim != 2 or c.shape[0] != c.shape[1] or c.shape[0] == 0:
        raise ValueError("corr must be a non-empty square matrix")
    lam = np.clip(np.linalg.eigvalsh((c + c.T) / 2.0), 0.0, None)
    total = float(lam.sum())
    denom = float((lam**2).sum())
    return total * total / denom if denom > 0 else 1.0


def effective_trials_cluster(corr: np.ndarray, threshold: float = 0.7) -> int:
    """Number of single-linkage clusters when trials with correlation >= ``threshold`` are joined."""
    c = np.asarray(corr, dtype=float)
    n = c.shape[0]
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if math.isfinite(c[i, j]) and c[i, j] >= threshold:
                parent[find(i)] = find(j)
    return len({find(i) for i in range(n)})


def trial_correlation(returns: np.ndarray) -> np.ndarray:
    """Correlation of trial return series (columns), on the dates where every trial has a finite return."""
    r = np.asarray(returns, dtype=float)
    ok = np.isfinite(r).all(axis=1)
    r = r[ok]
    if r.shape[0] < 3:
        return np.eye(r.shape[1])
    sd = r.std(axis=0)
    sd[sd == 0] = np.inf
    z = (r - r.mean(axis=0)) / sd
    return np.clip(z.T @ z / r.shape[0], -1.0, 1.0)


def deflation_trial_count(
    ledger_trials: int, effective_trials: float | None = None, floor: int = 1
) -> int:
    """Trial count for the DSR: the effective count when known (else the ledger count), floored at ``floor``
    (the preregistered or enumerated variants). Never below one."""
    base = effective_trials if effective_trials is not None and math.isfinite(effective_trials) else ledger_trials
    return max(1, int(math.ceil(base - 1e-9)), int(floor))


def per_period_sharpe_variance(sharpes_ann: Sequence[float], periods_per_year: float) -> float:
    """Cross-trial variance of per-period Sharpe ratios from annualised ones (NaN below two finite values)."""
    x = np.array([s for s in sharpes_ann if s is not None and math.isfinite(s)], dtype=float)
    if x.size < 2:
        return float("nan")
    return float((x / math.sqrt(periods_per_year)).var(ddof=1))
