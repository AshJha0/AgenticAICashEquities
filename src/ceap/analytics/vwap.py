"""Volume-weighted average price."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def vwap(prices: Sequence[float] | np.ndarray, quantities: Sequence[float] | np.ndarray) -> float:
    """VWAP = sum(p * q) / sum(q).

    Returns ``nan`` for an empty input or zero total quantity so callers
    can distinguish "no data" from a genuine zero price.
    """
    p = np.asarray(prices, dtype=float)
    q = np.asarray(quantities, dtype=float)
    if p.size == 0 or q.size == 0:
        return float("nan")
    if p.shape != q.shape:
        raise ValueError("prices and quantities must have the same shape")
    total = q.sum()
    if total <= 0:
        return float("nan")
    return float((p * q).sum() / total)


def vwap_by_bucket(
    timestamps: np.ndarray, prices: np.ndarray, quantities: np.ndarray, bucket_seconds: int = 60
) -> list[tuple[float, float, float]]:
    """Return ``[(bucket_start_epoch, vwap, volume), ...]`` for fixed-width time buckets."""
    if len(timestamps) == 0:
        return []
    epoch = np.asarray(timestamps, dtype="datetime64[s]").astype("int64")
    buckets = (epoch // bucket_seconds) * bucket_seconds
    result: list[tuple[float, float, float]] = []
    for b in np.unique(buckets):
        mask = buckets == b
        result.append((float(b), vwap(prices[mask], quantities[mask]), float(quantities[mask].sum())))
    return result
