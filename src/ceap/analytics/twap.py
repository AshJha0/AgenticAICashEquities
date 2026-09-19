"""Time-weighted average price."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import numpy as np


def twap(prices: Sequence[float], timestamps: Sequence[datetime] | None = None) -> float:
    """TWAP.

    Without timestamps this is the simple mean of the sampled prices. With
    timestamps each price is weighted by the time it prevailed (until the
    next observation); the final observation carries the median interval.
    """
    p = np.asarray(prices, dtype=float)
    if p.size == 0:
        return float("nan")
    if timestamps is None or p.size == 1:
        return float(p.mean())
    t = np.asarray([ts.timestamp() for ts in timestamps], dtype=float)
    if t.shape != p.shape:
        raise ValueError("prices and timestamps must have the same shape")
    order = np.argsort(t)
    t, p = t[order], p[order]
    gaps = np.diff(t)
    last = float(np.median(gaps)) if gaps.size else 1.0
    weights = np.append(gaps, max(last, 1e-9))
    if weights.sum() <= 0:
        return float(p.mean())
    return float((p * weights).sum() / weights.sum())
