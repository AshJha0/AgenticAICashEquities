"""Market impact decomposition.

* permanent (information) impact: move in the mid from arrival to a
  post-trade reference, signed by side.
* temporary impact: execution VWAP vs the post-trade mid - the part of the
  cost that reverts once we stop trading.
"""

from __future__ import annotations

import numpy as np

from ceap.domain.execution import Side


def market_impact_bps(side: Side, arrival_mid: float, post_trade_mid: float) -> float:
    if arrival_mid <= 0:
        return float("nan")
    return float(side.sign * (post_trade_mid - arrival_mid) / arrival_mid * 10_000.0)


def temporary_impact_bps(side: Side, exec_vwap: float, post_trade_mid: float) -> float:
    if post_trade_mid <= 0 or np.isnan(exec_vwap):
        return float("nan")
    return float(side.sign * (exec_vwap - post_trade_mid) / post_trade_mid * 10_000.0)


def square_root_impact_estimate_bps(
    participation_rate: float, daily_volatility_bps: float, coefficient: float = 0.6
) -> float:
    """Almgren-style square-root impact model estimate: c * sigma * sqrt(Q/V).

    Used as an *expectation* against which realised impact can be compared;
    it is not a replacement for measured impact.
    """
    if participation_rate < 0:
        return float("nan")
    return float(coefficient * daily_volatility_bps * np.sqrt(participation_rate))
