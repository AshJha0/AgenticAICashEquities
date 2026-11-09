"""Market impact decomposition.

* ``market_impact_bps`` / ``temporary_impact_bps``: the single-order arrival-to-post drift and
  VWAP-to-post reversion of v0.4. On one order these are dominated by price noise (sigma*sqrt(T)
  is 15 to 25 bps an hour), so since 2T.5 the TCA reports the *expected* square-root impact
  as ``market_impact_bps`` and measures realised impact with size-weighted, beta-adjusted
  markouts (``ceap.analytics.slippage``). These helpers remain for callers that want the raw drift.

F14 (0.8.0): ``market_impact_bps`` and ``temporary_impact_bps`` are deprecated and warn on use.
Nothing in CEAP calls them any more. For realised impact use the markouts in
:mod:`ceap.execution.markouts`; for a fitted permanent/temporary split use
:func:`ceap.execution.impact.fit_propagator`. They will be removed in 1.0.
"""

from __future__ import annotations

import warnings

import numpy as np

from ceap.domain.execution import Side

_DEPRECATED = (
    "{name} is deprecated (one order's drift is mostly price noise); use ceap.execution.markouts "
    "or ceap.execution.impact.fit_propagator. It will be removed in 1.0."
)


def market_impact_bps(side: Side, arrival_mid: float, post_trade_mid: float) -> float:
    """Deprecated: signed arrival-to-post mid drift in bps (see the module docstring)."""
    warnings.warn(_DEPRECATED.format(name="market_impact_bps"), DeprecationWarning, stacklevel=2)
    if arrival_mid <= 0:
        return float("nan")
    return float(side.sign * (post_trade_mid - arrival_mid) / arrival_mid * 10_000.0)


def temporary_impact_bps(side: Side, exec_vwap: float, post_trade_mid: float) -> float:
    """Deprecated: signed VWAP-to-post reversion in bps (see the module docstring)."""
    warnings.warn(_DEPRECATED.format(name="temporary_impact_bps"), DeprecationWarning, stacklevel=2)
    if post_trade_mid <= 0 or np.isnan(exec_vwap):
        return float("nan")
    return float(side.sign * (exec_vwap - post_trade_mid) / post_trade_mid * 10_000.0)


def square_root_impact_estimate_bps(
    order_quantity: float, adv_shares: float, daily_volatility_bps: float, coefficient: float = 0.6
) -> float:
    """Square-root impact estimate ``c * sigma_daily * sqrt(Q / ADV)`` in bps (2T.5 unit fix).

    ``Q`` is the parent quantity and ``ADV`` the average *daily* volume, so the participation term
    is on the same daily clock as ``sigma_daily``. v0.4 multiplied daily sigma by the square root
    of the *interval* participation rate, which mixes clocks and overstates impact for any order
    shorter than a day. Used as the pre-trade expectation the realised cost is compared against.
    """
    if order_quantity < 0 or adv_shares <= 0 or not np.isfinite(daily_volatility_bps):
        return float("nan")
    return float(coefficient * daily_volatility_bps * np.sqrt(order_quantity / adv_shares))
