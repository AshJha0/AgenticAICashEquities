"""Order / execution domain objects and execution-quality metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        """+1 for buys, -1 for sells: positive cost = adverse for the trader."""
        return 1 if self is Side.BUY else -1


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class Order:
    order_id: str
    parent_order_id: str | None
    symbol: str
    side: Side
    quantity: int
    strategy: str
    venue: str | None
    timestamp: datetime
    limit_price: float | None = None
    status: OrderStatus = OrderStatus.NEW
    trader: str | None = None
    account: str | None = None
    end_time: datetime | None = None
    decision_price: float | None = None

    @property
    def is_parent(self) -> bool:
        return self.parent_order_id is None


@dataclass(frozen=True)
class Execution:
    execution_id: str
    order_id: str
    symbol: str
    side: Side
    quantity: int
    price: float
    venue: str
    timestamp: datetime
    parent_order_id: str | None = None
    latency_us: float | None = None
    liquidity_flag: str | None = None  # "ADD" | "REMOVE"

    @property
    def notional(self) -> float:
        return self.price * self.quantity


@dataclass(frozen=True)
class VenueStatistics:
    venue: str
    child_orders: int
    executions: int
    routed_quantity: int
    executed_quantity: int
    fill_rate: float
    average_slippage_bps: float
    average_latency_us: float
    reject_count: int
    reject_rate: float
    volume_share: float


@dataclass(frozen=True)
class ExecutionMetrics:
    """Deterministic execution-quality metrics for one symbol / window.

    Every number here is produced by ``ceap.analytics`` - never by an LLM.
    """

    symbol: str
    start: datetime
    end: datetime
    side: Side
    target_quantity: int
    executed_quantity: int
    fill_rate: float
    participation_rate: float
    vwap: float
    market_vwap: float
    arrival_price: float
    implementation_shortfall_bps: float
    slippage_vs_arrival_bps: float
    slippage_vs_vwap_bps: float
    average_spread_bps: float
    effective_spread_bps: float
    market_impact_bps: float
    temporary_impact_bps: float
    price_drift_bps: float
    realised_volatility_bps: float
    average_latency_us: float
    reject_rate: float
    execution_count: int
    child_order_count: int
    venue_statistics: dict[str, VenueStatistics] = field(default_factory=dict)
