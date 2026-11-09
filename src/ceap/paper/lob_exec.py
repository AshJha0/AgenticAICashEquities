"""Paper fills priced by the event-driven limit order book (F11, paper-execution part; 0.9.0).

Plain idea. The default paper fill pays the square-root cost model at the close. With
``execution.engine = "lob"`` the cost of each fill is instead *measured*: a seeded
:class:`~ceap.execution.lob.LOBSimulator` starts at the fill price, our order is cut into equal
market slices over a short window, and the cost is the signed gap between the volume-weighted
price we got and the mid just before each slice was sent (slippage per child, so the
efficient price's drift over the window does not enter the cost).

Formal statement. For a fill of ``x`` shares (side ``s = sign(x)``) at participation
``rho = |x| / ADV``, the simulator trades ``q = max(100, round(rho * V_w))`` shares, where
``V_w = market_rate * mean_size * window`` is the background volume the simulator is expected to
print over the window. Cost in bps::

    c = s * sum_k sum_{f in slice k} (p_f - mid_k) q_f / (sum_f q_f * mid_0) * 1e4

and the paper ledger charges ``|x| * p * c / 1e4`` (fills stay at the close ``p``, so the daily
identity ``d equity = mark_pnl + financing - borrow - costs`` still holds). The seed is a CRC-32
of ``symbol|date``, so a session replays exactly.

Worked example. ``rho = 0.05`` and the default parameters (1.2 market orders a second, 100 shares
each, a 30 s window) give ``V_w = 3,600`` and ``q = 180`` shares in ten slices of 18 against a book
seeded with 300 shares a level: each slice takes the touch, so ``c`` is about a half spread (half a
cent on a $200 stock is 0.25 bps) plus whatever the touch has moved to.

Limits (stated, not hidden): the simulator is a stylised book (``ceap.execution.lob``), not a
calibrated one; one window stands in for a whole day's work; the negative cost a lucky window can
produce is kept, not floored, so the measured costs are unbiased draws of a noisy quantity.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

from ceap.execution.lob import BUY, SELL, LOBSimulator, SimulatorParams


@dataclass(frozen=True)
class LobCost:
    bps: float
    sim_shares: int
    filled: int
    vwap: float
    arrival_mid: float


def lob_cost_bps(symbol: str, day: str, price: float, quantity: float, participation: float,
                 window_s: int = 30, slices: int = 10) -> LobCost | None:
    """Measured cost of a paper fill in bps, or ``None`` when the simulator filled nothing."""
    if price <= 0 or quantity == 0:
        return None
    params = SimulatorParams(start_price=float(price))
    seed = zlib.crc32(f"{symbol}|{day}".encode())
    sim = LOBSimulator(params, seed=seed)
    sim.run_until(1_000)  # let the book settle for a second
    mid0 = sim.to_price(sim.mid_ticks())
    v_window = params.market_rate_per_s * params.mean_size * window_s
    q = max(100, round(participation * v_window))
    side = BUY if quantity > 0 else SELL
    step_ms = max(1, window_s * 1000 // slices)
    base, extra = divmod(q, slices)
    t = 1_000
    paid = filled = 0.0
    slip = 0.0  # sum over slices of side * (fill price - mid just before the slice) * shares
    for k in range(slices):
        size = base + (1 if k < extra else 0)
        mid_k = sim.to_price(sim.mid_ticks())
        n0 = len(sim.agent_fills)
        if size > 0:
            sim.submit(side, size)
        t += step_ms
        sim.run_until(t)
        for f in sim.agent_fills[n0:]:
            px = sim.to_price(f.price)
            paid += px * f.quantity
            filled += f.quantity
            slip += side * (px - mid_k) * f.quantity
    if filled == 0 or mid0 <= 0:
        return None
    bps = slip / filled / mid0 * 10_000.0
    return LobCost(float(bps), int(q), int(filled), float(paid / filled), float(mid0))
