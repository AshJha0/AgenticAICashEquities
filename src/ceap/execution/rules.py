"""US equity trading rules the simulator and child orders respect (F12: tick, lot and LULD parts).

Evidence class for every rule below: **regulation text as recalled at the time of writing**, not
re-checked against the current rulebook in this repo. The numbers are the published ones as far as
we know; check the current text before relying on them for anything real.

* **Tick size** - SEC Regulation NMS Rule 612 (17 CFR 242.612): no quote at or above $1.00 in an
  increment below $0.01; below $1.00 the minimum is $0.0001. The 2024 amendments (Release
  34-101070) add a $0.005 tick for stocks at or above $1.00 whose time-weighted average quoted
  spread was $0.015 or less in the evaluation period; ``tight_spread=True`` selects it.
* **Round lots** - the same 2024 release brought forward the Market Data Infrastructure round-lot
  definition: 100 shares up to $250.00, 40 up to $1,000.00, 10 up to $10,000.00, 1 above, by the
  prior month's average closing price. ``classic=True`` gives the old fixed 100.
* **Limit Up-Limit Down** - the NMS Plan to Address Extraordinary Market Volatility, Appendix A.
  Bands are a percentage around a reference price (the mean of eligible trades over the prior five
  minutes): Tier 1 5 % and Tier 2 10 % above $3.00; 20 % from $0.75 to $3.00; below $0.75 the lesser
  of $0.15 or 75 %. Bands are doubled from 9:30 to 9:45 and from 15:35 to 16:00. A stock whose
  quote sits on a band enters a Limit State; if it does not leave within 15 seconds trading pauses
  for five minutes. Whether the closing doubling applies to every Tier 2 price band has changed
  across plan amendments; ``double_at_close`` makes it explicit rather than guessed.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum


def tick_size(price: float, tight_spread: bool = False) -> float:
    """Minimum price increment for a quote at ``price`` (Rule 612)."""
    if price < 0 or not math.isfinite(price):
        raise ValueError("price must be finite and non-negative")
    if price < 1.0:
        return 0.0001
    return 0.005 if tight_spread else 0.01


def round_to_tick(price: float, side: int, tight_spread: bool = False) -> float:
    """Round a limit price onto the grid, never in the trader's favour (buys down, sells up)."""
    t = tick_size(price, tight_spread)
    units = price / t
    n = math.floor(units + 1e-9) if side > 0 else math.ceil(units - 1e-9)
    return round(n * t, 6)


def on_tick(price: float, tight_spread: bool = False) -> bool:
    t = tick_size(price, tight_spread)
    return abs(price / t - round(price / t)) < 1e-6


def round_lot_size(price: float, classic: bool = False) -> int:
    """Shares in one round lot at an average closing price of ``price``."""
    if classic or price <= 250.0:
        return 100
    if price <= 1_000.0:
        return 40
    if price <= 10_000.0:
        return 10
    return 1


def lot_type(quantity: int, price: float, classic: bool = False) -> str:
    """``ODD`` (below one round lot), ``ROUND`` (whole lots) or ``MIXED``."""
    lot = round_lot_size(price, classic)
    if quantity < lot:
        return "ODD"
    return "ROUND" if quantity % lot == 0 else "MIXED"


class Tier(str, Enum):
    TIER_1 = "TIER_1"  # S&P 500, Russell 1000 and some ETPs
    TIER_2 = "TIER_2"


OPEN_DOUBLE_END_S = 15 * 60  # 9:30-9:45, seconds since the open
CLOSE_DOUBLE_START_S = 6 * 3600 + 5 * 60  # 15:35, seconds since the open
SESSION_S = 6 * 3600 + 30 * 60


def luld_band_fraction(reference: float, tier: Tier = Tier.TIER_1) -> float:
    """Half-width of the band as a fraction of ``reference`` outside the doubling windows."""
    if reference > 3.0:
        return 0.05 if tier is Tier.TIER_1 else 0.10
    if reference >= 0.75:
        return 0.20
    return min(0.15 / reference, 0.75) if reference > 0 else 0.75


def luld_bands(
    reference: float,
    seconds_since_open: float,
    tier: Tier = Tier.TIER_1,
    double_at_close: bool = True,
) -> tuple[float, float]:
    """``(lower, upper)`` price bands, rounded to the cent (Appendix A, as recalled)."""
    pct = luld_band_fraction(reference, tier)
    if seconds_since_open < OPEN_DOUBLE_END_S or (
        double_at_close and seconds_since_open >= CLOSE_DOUBLE_START_S
    ):
        pct *= 2.0
    return round(reference * (1.0 - pct), 2), round(reference * (1.0 + pct), 2)


class LULDState(str, Enum):
    NORMAL = "NORMAL"
    LIMIT = "LIMIT"
    PAUSED = "PAUSED"


@dataclass
class LULDMonitor:
    """Tracks the five-minute reference price, Limit States and trading pauses for one symbol.

    Feed it trades (``on_trade``) and the national best bid and offer (``on_quote``) in time order,
    with times in seconds since the open. The reference price only moves when the new five-minute
    mean differs from it by 1 % or more, as the plan specifies.
    """

    tier: Tier = Tier.TIER_1
    double_at_close: bool = True
    window_s: float = 300.0
    limit_state_s: float = 15.0
    pause_s: float = 300.0
    reference: float | None = None
    state: LULDState = LULDState.NORMAL
    _trades: deque[tuple[float, float]] = field(default_factory=deque)
    _limit_since: float | None = None
    _paused_until: float = -math.inf

    def on_trade(self, t_s: float, price: float) -> None:
        self._trades.append((t_s, price))
        while self._trades and self._trades[0][0] < t_s - self.window_s:
            self._trades.popleft()
        mean = sum(p for _, p in self._trades) / len(self._trades)
        if self.reference is None or abs(mean / self.reference - 1.0) >= 0.01:
            self.reference = mean
        self._tick(t_s)

    def bands(self, t_s: float) -> tuple[float, float] | None:
        if self.reference is None:
            return None
        return luld_bands(self.reference, t_s, self.tier, self.double_at_close)

    def on_quote(self, t_s: float, bid: float, ask: float) -> LULDState:
        self._tick(t_s)
        if self.state is LULDState.PAUSED:
            return self.state
        b = self.bands(t_s)
        if b is None:
            return self.state
        lo, hi = b
        at_band = bid >= hi or ask <= lo
        if at_band:
            if self._limit_since is None:
                self._limit_since = t_s
                self.state = LULDState.LIMIT
            elif t_s - self._limit_since >= self.limit_state_s:
                self.state = LULDState.PAUSED
                self._paused_until = t_s + self.pause_s
                self._limit_since = None
        else:
            self._limit_since = None
            self.state = LULDState.NORMAL
        return self.state

    def _tick(self, t_s: float) -> None:
        if self.state is LULDState.PAUSED and t_s >= self._paused_until:
            self.state = LULDState.NORMAL

    def can_trade(self, t_s: float) -> bool:
        self._tick(t_s)
        return self.state is not LULDState.PAUSED

    def executable(self, t_s: float, price: float, side: int) -> bool:
        """A buy above the upper band or a sell below the lower band may not execute."""
        if not self.can_trade(t_s):
            return False
        b = self.bands(t_s)
        if b is None:
            return True
        lo, hi = b
        return price <= hi if side > 0 else price >= lo
