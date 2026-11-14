"""Shared numerical tolerances (review finding Q-14, plan item Q3).

``negligible_variation`` decides when a series is too close to constant for a regression on it to
mean anything. An exact ``== 0`` test misses the common case where floating-point noise leaves a
sum of squares of order 1e-30 on a constant input, and the result then divides by noise (an R-squared
or a beta of arbitrary size). A tolerance relative to the mean fails for a zero-mean series, so the
scale used here is ``max|y|``:

    sum((y - mean(y))**2) <= n * max((REL_TOL * max|y|)**2, ABS_FLOOR**2)

that is, the root-mean-square deviation is at most ``REL_TOL`` of the largest magnitude, or at most
``ABS_FLOOR`` in absolute terms (which covers an all-zero series, where ``max|y|`` is zero).
"""

from __future__ import annotations

import numpy as np

REL_TOL = 1e-9
ABS_FLOOR = 1e-12


def negligible_variation(values: np.ndarray, ss: float | None = None) -> bool:
    """True when ``values`` is constant to within the tolerance above (non-finite entries ignored).

    ``ss`` may pass a sum of squared deviations the caller has already computed on the same values.
    """
    v = np.asarray(values, dtype=float).ravel()
    v = v[np.isfinite(v)]
    n = v.size
    if n == 0:
        return True
    if ss is None:
        ss = float(((v - v.mean()) ** 2).sum())
    scale = float(np.abs(v).max())
    return bool(ss <= n * max((REL_TOL * scale) ** 2, ABS_FLOOR**2))


def sharpe_standard_error(sharpe_ann: float, n_days: int, periods_per_year: int = 252) -> float:
    """Standard error of an annualised Sharpe ratio estimated from ``n_days`` iid daily returns.

    Lo (2002), "The Statistics of Sharpe Ratios", Financial Analysts Journal 58(4), eq. (9): for the
    per-period ratio ``SR_d`` estimated from ``T`` observations, ``SE(SR_d) = sqrt((1 + SR_d**2 / 2) / T)``.
    The squared term is the *per-period* (daily) Sharpe, so the annual figure is divided by
    ``periods_per_year`` before squaring; the result is scaled back by ``sqrt(periods_per_year)``.
    Using the annual Sharpe in the squared term (the 0.9.0 form, review finding R-15 / Q-01) widens the
    interval by about 22 % at Sharpe 1 over one year. iid assumption: serial correlation needs Lo's
    eq. (13) adjustment, which this function does not make. Evidence class: method.
    """
    if sharpe_ann is None or not np.isfinite(sharpe_ann) or n_days < 2:
        return float("nan")
    sr_d = sharpe_ann / np.sqrt(periods_per_year)
    return float(np.sqrt((1.0 + 0.5 * sr_d * sr_d) / n_days) * np.sqrt(periods_per_year))
