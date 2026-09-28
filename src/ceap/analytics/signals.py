"""Cross-sectional signal library.

Matrices are ``(n_days, n_symbols)``; row ``t`` of a signal uses closes up to
and including row ``t`` only, and is NaN during the warm-up.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

SignalFn = Callable[[np.ndarray, np.ndarray | None], np.ndarray]


@dataclass(frozen=True)
class SignalSpec:
    name: str
    description: str
    lookback_days: int
    default_horizon_days: int
    default_rebalance_days: int
    fn: SignalFn

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "lookback_days": self.lookback_days,
            "default_horizon_days": self.default_horizon_days,
            "default_rebalance_days": self.default_rebalance_days,
        }


def _empty(close: np.ndarray) -> np.ndarray:
    return np.full(np.asarray(close, dtype=float).shape, np.nan)


def momentum_12_1(close: np.ndarray, volume: np.ndarray | None = None) -> np.ndarray:
    """12-month return skipping the most recent month: ``close[t-21] / close[t-252] - 1``."""
    c = np.asarray(close, dtype=float)
    out = _empty(c)
    n = c.shape[0]
    if n > 252:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[252:] = c[231 : n - 21] / c[: n - 252] - 1.0
    return out


def reversal_5(close: np.ndarray, volume: np.ndarray | None = None) -> np.ndarray:
    """Short-term reversal: minus the trailing five-day return."""
    c = np.asarray(close, dtype=float)
    out = _empty(c)
    if c.shape[0] > 5:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[5:] = -(c[5:] / c[:-5] - 1.0)
    return out


def low_vol_60(close: np.ndarray, volume: np.ndarray | None = None) -> np.ndarray:
    """Low volatility: minus the 60-day standard deviation of daily log returns."""
    c = np.asarray(close, dtype=float)
    out = _empty(c)
    n = c.shape[0]
    if n > 60:
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.diff(np.log(c), axis=0)
        windows = np.lib.stride_tricks.sliding_window_view(r, 60, axis=0)
        out[60:] = -windows.std(axis=-1, ddof=1)
    return out


SIGNALS: dict[str, SignalSpec] = {
    s.name: s
    for s in (
        SignalSpec(
            "momentum_12_1",
            "Twelve-month price momentum skipping the most recent month",
            lookback_days=252,
            default_horizon_days=21,
            default_rebalance_days=21,
            fn=momentum_12_1,
        ),
        SignalSpec(
            "reversal_5",
            "Short-term (five-day) return reversal",
            lookback_days=5,
            default_horizon_days=5,
            default_rebalance_days=5,
            fn=reversal_5,
        ),
        SignalSpec(
            "low_vol_60",
            "Low realised volatility over sixty days",
            lookback_days=60,
            default_horizon_days=21,
            default_rebalance_days=21,
            fn=low_vol_60,
        ),
    )
}


def get_signal(name: str) -> SignalSpec:
    try:
        return SIGNALS[name]
    except KeyError as exc:
        raise KeyError(f"unknown signal {name!r}; available: {sorted(SIGNALS)}") from exc


def compute_signal(name: str, close: np.ndarray, volume: np.ndarray | None = None) -> np.ndarray:
    return get_signal(name).fn(np.asarray(close, dtype=float), volume)


def cross_sectional_zscore(row: np.ndarray, clip: float = 3.0) -> np.ndarray:
    """Z-score one date's cross-section; NaN inputs stay NaN, a degenerate cross-section is all zero."""
    x = np.asarray(row, dtype=float)
    out = np.full(x.shape, np.nan)
    finite = np.isfinite(x)
    if finite.sum() < 2:
        out[finite] = 0.0
        return out
    values = x[finite]
    std = float(values.std())
    if std <= 0.0 or not np.isfinite(std):
        out[finite] = 0.0
        return out
    z = (values - float(values.mean())) / std
    out[finite] = np.clip(z, -clip, clip)
    return out


def zscore_matrix(signal: np.ndarray, clip: float = 3.0) -> np.ndarray:
    s = np.asarray(signal, dtype=float)
    return np.vstack([cross_sectional_zscore(row, clip) for row in s]) if s.size else s.copy()
