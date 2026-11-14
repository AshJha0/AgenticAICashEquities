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

