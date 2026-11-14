"""Search diagnostics on the 24-trial matrix (0.10.0 item Q4, findings R-17, R-20).

The research flow chooses one configuration from the enumerated search space (2.8, 24 configurations).
Two questions about that search have published estimators which, before 0.10.0, had no caller outside
the tests:

* **How many independent trials was it?** Configurations built from three signals are not 24 independent
  draws. :mod:`ceap.analytics.trial_accounting` estimates the effective count from the correlation of
  the trials' daily returns, by the participation ratio of its eigenvalues and by single-linkage
  clusters at correlation 0.7. The eigenvalue count is written to the trial record as
  ``n_trials_effective``; the deflated Sharpe already reads it, floored at the 24 enumerated variants
  (so it can explain the deflation, never relax it).
* **Does selecting the in-sample winner overfit?** The probability of backtest overfitting by
  combinatorially symmetric cross-validation (Bailey, Borwein, Lopez de Prado and Zhu 2017),
  :func:`ceap.analytics.overfitting.pbo_cscv`, with 16 blocks (12,870 splits) as in the paper.

Basis: the in-sample net daily excess returns of all 24 configurations on the request's dataset, window,
execution lag and fill, the period the agent selects on. A PBO near 0.5 is what a search over noise
produces; near 0 means the in-sample winner is usually an out-of-sample winner too. One run's PBO is a
description of this search on this dataset, not a probability for the chosen strategy (finding Q-13).

Evidence class: method (published estimators applied to the platform's own backtests).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ceap.analytics.overfitting import pbo_cscv
from ceap.analytics.trial_accounting import (
    effective_trials_cluster,
    effective_trials_eigen,
    trial_correlation,
)

PBO_BLOCKS = 16
CLUSTER_THRESHOLD = 0.7


def search_return_matrix(ds: Any, **window: Any) -> tuple[list[str], np.ndarray]:
    """In-sample net daily excess returns (``T x 24``) of every configuration in the search space."""
    from ceap.analytics.backtest import simulate_backtest
    from ceap.domain.research import SEARCH_TOP_FRACTION, search_space

    labels: list[str] = []
    cols: list[np.ndarray] = []
    for cfg in search_space():
        run = simulate_backtest(
            ds, cfg["signal"], rebalance_days=cfg["rebalance_days"], long_short=cfg["long_short"],
            weighting=cfg["weighting"], top_fraction=SEARCH_TOP_FRACTION, report_bounds=False, **window,
        )
        labels.append(f"{cfg['signal']}/{cfg['rebalance_days']}/{'ls' if cfg['long_short'] else 'lo'}/{cfg['weighting']}")
        cols.append((run.net - run.rf)[run.i0 : run.split + 1])
    return labels, np.column_stack(cols)


def search_diagnostics(ds: Any, **window: Any) -> dict[str, Any]:
    """PBO and effective trial count of the full search on ``ds`` (window keywords as for the backtest)."""
    labels, r = search_return_matrix(ds, **window)
    corr = trial_correlation(r)
    out: dict[str, Any] = {
        "basis": "in-sample net daily excess returns of the 24 enumerated configurations",
        "n_trials": len(labels),
        "n_days": int(r.shape[0]),
        "n_trials_effective": round(effective_trials_eigen(corr), 4),
        "n_trials_effective_cluster": effective_trials_cluster(corr, CLUSTER_THRESHOLD),
        "cluster_threshold": CLUSTER_THRESHOLD,
    }
    try:
        pbo = pbo_cscv(r, PBO_BLOCKS)
        out.update({"pbo": round(pbo["pbo"], 6), "pbo_n_blocks": PBO_BLOCKS, "pbo_n_splits": pbo["n_splits"],
                    "pbo_median_logit": round(pbo["median_logit"], 6),
                    "pbo_oos_on_is_slope": round(pbo["oos_on_is_slope"], 6)})
    except ValueError as exc:  # too few in-sample days for 16 blocks
        out.update({"pbo": float("nan"), "pbo_note": str(exc)})
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in out.items()}
