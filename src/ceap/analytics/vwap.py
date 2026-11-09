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
    first = timestamps[0]
    if hasattr(first, "timestamp"):  # datetime objects (aware or naive)
        epoch = np.array([int(t.timestamp()) for t in timestamps], dtype="int64")
    else:  # numpy datetime64 or epoch seconds
        epoch = (
            np.asarray(timestamps).astype("datetime64[s]").astype("int64")
            if np.asarray(timestamps).dtype.kind == "M"
            else np.asarray(timestamps, dtype="int64")
        )
    buckets = (epoch // bucket_seconds) * bucket_seconds
    # F14: one pass with bincount instead of a boolean mask per bucket (O(n) rather than O(n*k))
    starts, inverse = np.unique(buckets, return_inverse=True)
    p = np.asarray(prices, dtype=float)
    q = np.asarray(quantities, dtype=float)
    volume = np.bincount(inverse, weights=q, minlength=starts.size)
    notional = np.bincount(inverse, weights=p * q, minlength=starts.size)
    with np.errstate(invalid="ignore", divide="ignore"):
        bucket_vwap = np.where(volume > 0, notional / np.where(volume > 0, volume, 1.0), np.nan)
    return [(float(b), float(v), float(vol)) for b, v, vol in zip(starts, bucket_vwap, volume, strict=True)]
