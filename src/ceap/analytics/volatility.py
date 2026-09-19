"""Realised volatility from a sampled mid-price path."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

SECONDS_PER_TRADING_DAY = 6.5 * 3600
TRADING_DAYS_PER_YEAR = 252


def log_returns(prices: Sequence[float]) -> np.ndarray:
    p = np.asarray(prices, dtype=float)
    p = p[p > 0]
    if p.size < 2:
        return np.array([], dtype=float)
    return np.diff(np.log(p))


def realised_volatility_bps(
    prices: Sequence[float], sample_seconds: float = 1.0, horizon_seconds: float = 60.0
) -> float:
    """Std-dev of log returns scaled to ``horizon_seconds``, in bps.

    e.g. with 1-second mids and ``horizon_seconds=60`` the result is the
    typical one-minute move in bps - an intuitive intraday vol measure.
    """
    r = log_returns(prices)
    if r.size < 2:
        return float("nan")
    per_sample = float(r.std(ddof=1))
    scale = np.sqrt(horizon_seconds / sample_seconds)
    return per_sample * scale * 10_000.0


def annualise_volatility(prices: Sequence[float], sample_seconds: float = 1.0) -> float:
    """Annualised volatility (decimal) from intraday sampled prices."""
    r = log_returns(prices)
    if r.size < 2:
        return float("nan")
    per_sample = float(r.std(ddof=1))
    samples_per_day = SECONDS_PER_TRADING_DAY / sample_seconds
    return per_sample * np.sqrt(samples_per_day * TRADING_DAYS_PER_YEAR)


def price_drift_bps(prices: Sequence[float]) -> float:
    p = np.asarray(prices, dtype=float)
    if p.size < 2 or p[0] <= 0:
        return float("nan")
    return float((p[-1] - p[0]) / p[0] * 10_000.0)


def max_abs_move_bps(prices: Sequence[float], window: int = 60) -> float:
    """Largest absolute move over any ``window`` samples, in bps (jump detector)."""
    p = np.asarray(prices, dtype=float)
    if p.size <= window:
        return abs(price_drift_bps(p)) if p.size >= 2 else float("nan")
    moves = (p[window:] - p[:-window]) / p[:-window] * 10_000.0
    return float(np.abs(moves).max())
