"""Slippage against a benchmark price."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ceap.domain.execution import Side


def slippage_bps(side: Side, execution_price: float, benchmark_price: float) -> float:
    """Signed slippage in bps; positive means the fill was worse than the benchmark."""
    if benchmark_price <= 0 or np.isnan(execution_price):
        return float("nan")
    return float(side.sign * (execution_price - benchmark_price) / benchmark_price * 10_000.0)


def per_fill_slippage_bps(
    side: Side, exec_prices: Sequence[float] | np.ndarray, benchmark_prices: Sequence[float] | np.ndarray
) -> np.ndarray:
    """Vectorised slippage of each fill against the benchmark prevailing at that fill."""
    p = np.asarray(exec_prices, dtype=float)
    b = np.asarray(benchmark_prices, dtype=float)
    if p.shape != b.shape:
        raise ValueError("exec_prices and benchmark_prices must have the same shape")
    with np.errstate(divide="ignore", invalid="ignore"):
        out = side.sign * (p - b) / b * 10_000.0
    return np.where(b > 0, out, np.nan)


def effective_spread_bps(
    exec_prices: Sequence[float] | np.ndarray, mids: Sequence[float] | np.ndarray, side: Side
) -> float:
    """Effective spread = 2 * |exec - mid| / mid, quantity-unweighted mean in bps."""
    p = np.asarray(exec_prices, dtype=float)
    m = np.asarray(mids, dtype=float)
    if p.size == 0:
        return float("nan")
    with np.errstate(divide="ignore", invalid="ignore"):
        eff = 2.0 * side.sign * (p - m) / m * 10_000.0
    eff = eff[np.isfinite(eff)]
    return float(eff.mean()) if eff.size else float("nan")
