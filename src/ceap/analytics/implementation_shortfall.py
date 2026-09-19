"""Implementation shortfall (Perold, 1988) in basis points.

For a BUY:

    IS = Executed Cost - Decision Price x Target Quantity
       = [ sum(p_i q_i) - P_d * Q_filled ]            (execution cost)
       + [ (P_final - P_d) * Q_unfilled ]             (opportunity cost)

Normalised by ``P_d * Q_target`` and expressed in bps. Sells flip the sign so
that a positive number is always *adverse* for the trader.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ceap.domain.execution import Side


def implementation_shortfall_bps(
    side: Side,
    decision_price: float,
    exec_prices: Sequence[float],
    exec_quantities: Sequence[float],
    target_quantity: float,
    final_price: float | None = None,
) -> float:
    """Return implementation shortfall in bps (positive = adverse).

    ``final_price`` is used to price the opportunity cost of any unfilled
    quantity; when omitted the opportunity-cost leg is ignored (pure
    execution-cost IS).
    """
    if decision_price <= 0 or target_quantity <= 0:
        return float("nan")
    p = np.asarray(exec_prices, dtype=float)
    q = np.asarray(exec_quantities, dtype=float)
    filled = float(q.sum()) if q.size else 0.0
    executed_cost = float((p * q).sum()) if q.size else 0.0
    execution_leg = side.sign * (executed_cost - decision_price * filled)
    unfilled = max(target_quantity - filled, 0.0)
    opportunity_leg = 0.0
    if final_price is not None and unfilled > 0:
        opportunity_leg = side.sign * (final_price - decision_price) * unfilled
    total = execution_leg + opportunity_leg
    return float(total / (decision_price * target_quantity) * 10_000.0)


def execution_cost_bps(side: Side, decision_price: float, exec_vwap: float) -> float:
    """Execution-cost component only: sign * (exec VWAP - decision) / decision."""
    if decision_price <= 0 or np.isnan(exec_vwap):
        return float("nan")
    return float(side.sign * (exec_vwap - decision_price) / decision_price * 10_000.0)
