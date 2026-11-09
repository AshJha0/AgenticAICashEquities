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
from dataclasses import dataclass

import numpy as np

from ceap.domain.execution import Side


def implementation_shortfall_bps(
    side: Side,
    decision_price: float,
    exec_prices: Sequence[float] | np.ndarray,
    exec_quantities: Sequence[float] | np.ndarray,
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


# --------------------------------------------------------------------------- R2T (2T.3, 2T.4)
@dataclass(frozen=True)
class FeeSchedule:
    """Per-share explicit costs in dollars. Positive = cost, negative = rebate.

    Representative lit-venue numbers (access fee cap 0.30 c/share for removing, a 0.20 c/share
    maker rebate, 0.10 c/share broker commission); the auction charges no access fee.
    """

    commission_per_share: float = 0.0010
    remove_fee_per_share: float = 0.0030
    add_rebate_per_share: float = 0.0020
    auction_fee_per_share: float = 0.0

    def per_share(self, liquidity_flag: str | None) -> float:
        flag = (liquidity_flag or "REMOVE").upper()
        if flag == "ADD":
            venue = -self.add_rebate_per_share
        elif flag == "AUCTION":
            venue = self.auction_fee_per_share
        else:
            venue = self.remove_fee_per_share
        return self.commission_per_share + venue


DEFAULT_FEES = FeeSchedule()


@dataclass(frozen=True)
class ShortfallComponents:
    """Perold (1988) implementation shortfall with a delay leg and explicit fees.

    All legs are in bps of the decision notional ``P_d * Q_target`` and positive = adverse::

        delay       = s (P_arr - P_d) Q_target        (decision-to-arrival drift on the whole order)
        spread      = s sum q_i (p_i - m_i)          (fill vs prevailing mid)
        timing      = s sum q_i (m_i - P_arr)        (mid drift between arrival and each fill)
        opportunity = s (P_final - P_arr) Q_unfilled
        fees        = sum q_i f_i                    (commission + access fee - rebate)
        total       = delay + spread + timing + opportunity + fees

    ``spread + timing`` is the classic execution (trading) leg ``s (sum p_i q_i - P_arr Q_filled)``.
    """

    delay_bps: float
    spread_bps: float
    timing_bps: float
    opportunity_bps: float
    fees_bps: float
    fill_rate: float = float("nan")
    opportunity_priced: bool = True

    @property
    def trading_bps(self) -> float:
        return self.spread_bps + self.timing_bps

    @property
    def total_bps(self) -> float:
        return self.delay_bps + self.spread_bps + self.timing_bps + self.opportunity_bps + self.fees_bps

    @property
    def arrival_bps(self) -> float:
        """Shortfall measured from arrival: everything except the (pre-arrival) delay leg."""
        return self.total_bps - self.delay_bps

    def as_dict(self) -> dict[str, float | bool]:
        return {
            "delay_bps": self.delay_bps,
            "spread_bps": self.spread_bps,
            "timing_bps": self.timing_bps,
            "opportunity_bps": self.opportunity_bps,
            "fees_bps": self.fees_bps,
            "total_bps": self.total_bps,
            "fill_rate": self.fill_rate,
            "opportunity_priced": self.opportunity_priced,
        }


def shortfall_components(
    side: Side,
    decision_price: float,
    arrival_price: float,
    exec_prices: Sequence[float] | np.ndarray,
    exec_quantities: Sequence[float] | np.ndarray,
    fill_mids: Sequence[float] | np.ndarray,
    target_quantity: float,
    final_price: float | None = None,
    fees_per_share: Sequence[float] | np.ndarray | None = None,
    opportunity_quantity: float | None = None,
) -> ShortfallComponents:
    """Perold IS split into delay, spread, timing, opportunity and fees (see the class docstring).

    ``fill_mids`` is the mid prevailing at each fill; a fill without a finite mid has its whole
    execution cost booked as spread (``m_i := P_arr``) so the legs still sum to the total.
    ``opportunity_quantity`` (default ``target_quantity``) is the quantity the unfilled leg is
    measured against, e.g. the pro-rated target of a window shorter than the parent; the
    normalisation always uses the full ``target_quantity``.
    """
    nan = float("nan")
    if decision_price <= 0 or target_quantity <= 0 or not np.isfinite(arrival_price):
        return ShortfallComponents(nan, nan, nan, nan, nan)
    s = float(side.sign)
    p = np.asarray(exec_prices, dtype=float)
    q = np.asarray(exec_quantities, dtype=float)
    m = np.asarray(fill_mids, dtype=float)
    m = np.where(np.isfinite(m) & (m > 0), m, arrival_price)
    f = np.zeros_like(q) if fees_per_share is None else np.asarray(fees_per_share, dtype=float)
    filled = float(q.sum()) if q.size else 0.0
    owed = target_quantity if opportunity_quantity is None else opportunity_quantity
    unfilled = max(owed - filled, 0.0)
    norm = decision_price * target_quantity / 10_000.0
    delay = s * (arrival_price - decision_price) * (filled + unfilled)
    spread = s * float(((p - m) * q).sum()) if q.size else 0.0
    timing = s * float(((m - arrival_price) * q).sum()) if q.size else 0.0
    opportunity = 0.0
    priced = True
    if final_price is not None and np.isfinite(final_price) and unfilled > 0:
        opportunity = s * (final_price - arrival_price) * unfilled
    else:
        delay = s * (arrival_price - decision_price) * filled  # no final price: no unfilled legs
        # A partial fill with no final price cannot price the unfilled leg. The opportunity leg is
        # left at zero so the other legs still sum, but it is flagged: total_bps then covers only the
        # filled quantity and must be read together with fill_rate.
        priced = unfilled <= 0
    fees = float((f * q).sum()) if q.size else 0.0
    fill_rate = min(filled / owed, 1.0) if owed > 0 else float("nan")
    return ShortfallComponents(
        delay / norm, spread / norm, timing / norm, opportunity / norm, fees / norm, fill_rate, priced
    )


def schedule_price_variance(segments: Sequence[tuple[float, float, float]]) -> float:
    """Variance multiplier of the price-risk term of IS for a piecewise-linear holding schedule.

    Each segment is ``(duration_days, a, b)``: the fraction of the parent still to trade falls
    linearly from ``a`` to ``b`` over ``duration_days``. With a random-walk price of daily variance
    ``sigma^2`` the price term of IS, ``int x(t) dP(t)``, has variance
    ``sigma^2 * sum T_k (a_k^2 + a_k b_k + b_k^2) / 3``. A single linear schedule (1 -> 0 over T)
    gives ``T/3``; an overnight hold of half the order is ``(T_on, 0.5, 0.5)``.
    """
    return float(sum(t * (a * a + a * b + b * b) / 3.0 for t, a, b in segments if t > 0))


def is_standard_error_bps(
    sigma_daily_bps: float,
    duration_days: float,
    execution_noise_bps: float = 0.0,
    segments: Sequence[tuple[float, float, float]] | None = None,
) -> float:
    """Analytic SE of a parent's IS: ``sqrt(sigma^2 * T/3 + s_exec^2)`` for a linear schedule.

    ``execution_noise_bps`` is the non-price dispersion (spread capture, fill luck) estimated from
    peer orders or the pre-trade model's calibration; ``segments`` replaces ``T/3`` for
    non-linear or multi-session schedules (:func:`schedule_price_variance`).
    """
    if not np.isfinite(sigma_daily_bps) or sigma_daily_bps < 0:
        return float("nan")
    mult = schedule_price_variance(segments) if segments else max(duration_days, 0.0) / 3.0
    return float(np.sqrt(sigma_daily_bps**2 * mult + execution_noise_bps**2))
