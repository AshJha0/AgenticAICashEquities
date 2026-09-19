"""Risk domain objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int
    average_price: float
    as_of: datetime
    book: str = "CASH_EQ_1"

    def market_value(self, price: float) -> float:
        return self.quantity * price


@dataclass(frozen=True)
class RiskLimit:
    symbol: str
    max_position: int
    max_order_quantity: int
    max_participation_rate: float
    max_notional: float


@dataclass(frozen=True)
class LimitCheck:
    symbol: str
    limit_name: str
    limit_value: float
    observed_value: float
    breached: bool
    utilisation: float


@dataclass(frozen=True)
class StressResult:
    symbol: str
    shock_bps: float
    position_quantity: int
    reference_price: float
    pnl_impact: float
