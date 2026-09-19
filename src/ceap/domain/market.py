"""Cash-equity market data domain objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Quote:
    symbol: str
    timestamp: datetime
    bid: float
    bid_size: int
    ask: float
    ask_size: int
    last_price: float | None = None
    last_size: int | None = None
    volume: int | None = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_bps(self) -> float:
        mid = self.mid
        return (self.spread / mid) * 10_000.0 if mid else 0.0

    @property
    def is_crossed(self) -> bool:
        return self.bid > self.ask

    @property
    def is_locked(self) -> bool:
        return self.bid == self.ask


@dataclass(frozen=True)
class Trade:
    symbol: str
    timestamp: datetime
    price: float
    quantity: int
    venue: str
    trade_id: str | None = None


@dataclass(frozen=True)
class OrderBookLevel:
    side: str  # "BID" | "ASK"
    level: int  # 1 = top of book
    price: float
    quantity: int


@dataclass(frozen=True)
class OrderBookSnapshot:
    symbol: str
    timestamp: datetime
    levels: tuple[OrderBookLevel, ...]

    def bids(self) -> list[OrderBookLevel]:
        return sorted((lv for lv in self.levels if lv.side == "BID"), key=lambda lv: lv.level)

    def asks(self) -> list[OrderBookLevel]:
        return sorted((lv for lv in self.levels if lv.side == "ASK"), key=lambda lv: lv.level)

    def displayed_depth(self, side: str | None = None, levels: int = 5) -> int:
        chosen = [lv for lv in self.levels if lv.level <= levels and (side is None or lv.side == side)]
        return sum(lv.quantity for lv in chosen)

    def imbalance(self, levels: int = 5) -> float:
        bid = self.displayed_depth("BID", levels)
        ask = self.displayed_depth("ASK", levels)
        total = bid + ask
        return (bid - ask) / total if total else 0.0


@dataclass(frozen=True)
class MarketSnapshot:
    """Convenience bundle handed to agents: quotes + trades + books for a window."""

    symbol: str
    start: datetime
    end: datetime
    quotes: tuple[Quote, ...] = ()
    trades: tuple[Trade, ...] = ()
    order_books: tuple[OrderBookSnapshot, ...] = ()


@dataclass(frozen=True)
class MarketStatistics:
    """Deterministic summary statistics of a market window."""

    symbol: str
    start: datetime
    end: datetime
    open_mid: float
    close_mid: float
    high: float
    low: float
    vwap: float
    traded_volume: int
    trade_count: int
    average_spread_bps: float
    realised_volatility_bps: float
    annualised_volatility: float
    average_displayed_depth: float
    average_top_of_book_size: float
    price_drift_bps: float
    stale_quote_fraction: float
    crossed_quote_count: int
    quote_count: int
    venue_volume: dict[str, int] = field(default_factory=dict)
