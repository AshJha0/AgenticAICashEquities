"""Paper order staging: the execution hook behind the research approval gate.

Staging converts an approved target portfolio into an order list held in
memory. Nothing is routed to a venue. Staging is idempotent per
``(signal, dataset, as_of, gross_notional, long_short)`` so a retried tool
call cannot create a second order set.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import date, datetime

from ceap.data.synthetic import LONDON
from ceap.domain.common import utc_now
from ceap.domain.execution import Order, OrderStatus, Side
from ceap.domain.research import PortfolioRiskReport

STAGING_HOUR = (14, 30)
PAPER_ACCOUNT = "PAPER_RESEARCH"
PAPER_STRATEGY = "VWAP"


def staging_id(signal: str, dataset: str, as_of: str, gross_notional: float, long_short: bool) -> str:
    key = f"{signal}|{dataset}|{as_of}|{gross_notional:.2f}|{int(long_short)}"
    return "STG-" + hashlib.sha256(key.encode()).hexdigest()[:10]


def build_paper_orders(report: PortfolioRiskReport, sid: str) -> tuple[Order, ...]:
    day = date.fromisoformat(report.as_of[:10])
    ts = datetime(day.year, day.month, day.day, *STAGING_HOUR, tzinfo=LONDON)
    orders: list[Order] = []
    for n, p in enumerate(report.positions, start=1):
        if p.quantity == 0:
            continue
        orders.append(
            Order(
                order_id=f"PAPER-{sid}-{n:03d}",
                parent_order_id=None,
                symbol=p.symbol,
                side=Side.BUY if p.quantity > 0 else Side.SELL,
                quantity=abs(int(p.quantity)),
                strategy=PAPER_STRATEGY,
                venue=None,
                timestamp=ts,
                status=OrderStatus.NEW,
                trader="paper",
                account=PAPER_ACCOUNT,
                decision_price=p.price,
            )
        )
    return tuple(orders)


@dataclass(frozen=True)
class StagedOrders:
    staging_id: str
    signal: str
    dataset: str
    as_of: str
    gross_notional: float
    long_short: bool
    created_at: datetime
    orders: tuple[Order, ...]
    status: str = "STAGED"

    @property
    def buy_notional(self) -> float:
        return round(
            sum(o.quantity * (o.decision_price or 0.0) for o in self.orders if o.side is Side.BUY), 2
        )

    @property
    def sell_notional(self) -> float:
        return round(
            sum(o.quantity * (o.decision_price or 0.0) for o in self.orders if o.side is Side.SELL), 2
        )


class StagedOrderBook:
    """In-memory, thread-safe store of staged paper order sets."""

    def __init__(self, keep: int = 500) -> None:
        self._keep = keep
        self._lock = threading.Lock()
        self._by_id: dict[str, StagedOrders] = {}

    def stage(self, report: PortfolioRiskReport) -> tuple[StagedOrders, bool]:
        sid = staging_id(report.signal, report.dataset, report.as_of, report.gross_notional, report.long_short)
        with self._lock:
            existing = self._by_id.get(sid)
            if existing is not None:
                return existing, True
            staged = StagedOrders(
                staging_id=sid,
                signal=report.signal,
                dataset=report.dataset,
                as_of=report.as_of,
                gross_notional=report.gross_notional,
                long_short=report.long_short,
                created_at=utc_now(),
                orders=build_paper_orders(report, sid),
            )
            self._by_id[sid] = staged
            while len(self._by_id) > self._keep:
                oldest = next(iter(self._by_id))
                self._by_id.pop(oldest)
            return staged, False

    def get(self, sid: str) -> StagedOrders | None:
        with self._lock:
            return self._by_id.get(sid)

    def list(self) -> list[StagedOrders]:
        with self._lock:
            return list(self._by_id.values())
