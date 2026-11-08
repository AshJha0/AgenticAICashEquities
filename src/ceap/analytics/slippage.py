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


def _weighted_mean(x: np.ndarray, w: np.ndarray | None) -> float:
    ok = np.isfinite(x) if w is None else (np.isfinite(x) & np.isfinite(w) & (w > 0))
    if not ok.any():
        return float("nan")
    if w is None:
        return float(x[ok].mean())
    return float((x[ok] * w[ok]).sum() / w[ok].sum())


def effective_spread_bps(
    exec_prices: Sequence[float] | np.ndarray,
    mids: Sequence[float] | np.ndarray,
    side: Side,
    quantities: Sequence[float] | np.ndarray | None = None,
) -> float:
    """Effective spread ``2 s (p - m) / m`` in bps, size-weighted when ``quantities`` is given.

    Size weighting is the market standard (SEC Rule 605 style); the unweighted mean of v0.4 is
    what you get with ``quantities=None``.
    """
    p = np.asarray(exec_prices, dtype=float)
    m = np.asarray(mids, dtype=float)
    if p.size == 0:
        return float("nan")
    with np.errstate(divide="ignore", invalid="ignore"):
        eff = 2.0 * side.sign * (p - m) / m * 10_000.0
    w = None if quantities is None else np.asarray(quantities, dtype=float)
    return _weighted_mean(eff, w)


def realised_spread_bps(
    side: Side,
    exec_prices: Sequence[float] | np.ndarray,
    fill_mids: Sequence[float] | np.ndarray,
    later_mids: Sequence[float] | np.ndarray,
    quantities: Sequence[float] | np.ndarray,
) -> float:
    """Size-weighted realised spread ``2 s (p - m_{t+h}) / m_t`` in bps.

    Effective spread = realised spread + price impact, with price impact ``2 s (m_{t+h} - m_t)/m_t``.
    A liquidity taker pays the realised spread to the provider; NaN later mids (horizon past the
    data) are dropped, never clamped to the last quote.
    """
    p = np.asarray(exec_prices, dtype=float)
    m0 = np.asarray(fill_mids, dtype=float)
    mh = np.asarray(later_mids, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = 2.0 * side.sign * (p - mh) / m0 * 10_000.0
    return _weighted_mean(rs, np.asarray(quantities, dtype=float))


def markout_bps(
    side: Side,
    exec_prices: Sequence[float] | np.ndarray,
    later_mids: Sequence[float] | np.ndarray,
    quantities: Sequence[float] | np.ndarray,
    index_returns: Sequence[float] | np.ndarray | None = None,
    beta: float = 1.0,
) -> float:
    """Size-weighted markout ``s (m_{t+h} - p) / p`` in bps; positive = the price moved our way.

    With ``index_returns`` (the index's simple return over the same ``[t, t+h]`` per fill) the
    market component ``beta * r_index`` is removed, so a market-wide move is not read as our edge
    or our impact.
    """
    p = np.asarray(exec_prices, dtype=float)
    mh = np.asarray(later_mids, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = (mh - p) / p
    if index_returns is not None:
        r = r - beta * np.asarray(index_returns, dtype=float)
    return _weighted_mean(side.sign * r * 10_000.0, np.asarray(quantities, dtype=float))


def price_improvement_bps(
    side: Side,
    exec_prices: Sequence[float] | np.ndarray,
    far_touch: Sequence[float] | np.ndarray,
    quantities: Sequence[float] | np.ndarray,
) -> float:
    """Size-weighted price improvement versus the far touch (ask for a buy, bid for a sell).

    ``s (touch - p) / touch`` in bps: positive = filled inside the quote we could have hit;
    a passive fill at the near touch shows a full spread of improvement, a book walk is negative.
    """
    p = np.asarray(exec_prices, dtype=float)
    t = np.asarray(far_touch, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        pi = side.sign * (t - p) / t * 10_000.0
    return _weighted_mean(pi, np.asarray(quantities, dtype=float))
