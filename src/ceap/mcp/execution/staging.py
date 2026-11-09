"""Paper order staging: the execution hook behind the research approval gate.

Staging converts an approved target portfolio into an order list. Nothing is
routed to a venue. Staging is idempotent per
``(signal, dataset, as_of, gross_notional, long_short)`` so a retried tool
call cannot create a second order set.

0.8.0 (item F11): when the book is given a :class:`~ceap.persistence.db.Database`, every staged set is
written to the ``staged_orders`` table (``staging_id`` is its primary key, so a retry, a second
process or a restart finds the same set instead of staging another), and staging refuses while the
DB-backed kill switch is set. The in-memory dict is then only a cache. Pre-trade checks run before
staging (``ceap.paper.pretrade.check_staged_orders``, called from the MCP tool).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from ceap.data.synthetic import LONDON
from ceap.domain.common import utc_now
from ceap.domain.execution import Order, OrderStatus, Side
from ceap.domain.research import PortfolioRiskReport
from ceap.util.canonical import content_hash

if TYPE_CHECKING:
    from ceap.persistence.db import Database

STAGING_HOUR = (14, 30)
PAPER_ACCOUNT = "PAPER_RESEARCH"
PAPER_STRATEGY = "VWAP"


class StagingRefusedError(ValueError):
    """The portfolio recomputed at staging time is not the one the approver signed off (TOCTOU, 4.5),
    or the kill switch is set."""


def portfolio_hash(as_of: str | None, positions: Iterable[tuple[str, int]]) -> str:
    """Content hash of a target portfolio: ``as_of`` plus the sorted non-zero (symbol, quantity) pairs.

    The approval payload carries this hash for the portfolio the approver saw; ``stage_orders``
    recomputes the portfolio and refuses when the hashes differ (R4 item 4.5).
    """
    legs = sorted((str(sym), int(qty)) for sym, qty in positions if int(qty) != 0)
    return content_hash({"as_of": (as_of or "")[:10], "positions": [list(leg) for leg in legs]})


def report_hash(report: PortfolioRiskReport) -> str:
    return portfolio_hash(report.as_of, ((p.symbol, p.quantity) for p in report.positions))


def staging_id(signal: str, dataset: str, as_of: str, gross_notional: float, long_short: bool) -> str:
    key = f"{signal}|{dataset}|{as_of}|{gross_notional:.2f}|{int(long_short)}"
    return "STG-" + hashlib.sha256(key.encode()).hexdigest()[:10]


def build_paper_orders(
    report: PortfolioRiskReport, sid: str, quantities: Mapping[str, float] | None = None
) -> tuple[Order, ...]:
    """Orders for ``report``; ``quantities`` (signed, per symbol) replaces the report's quantities after
    the pre-trade check clipped or dropped some (a symbol missing from it is not traded)."""
    day = date.fromisoformat(report.as_of[:10])
    ts = datetime(day.year, day.month, day.day, *STAGING_HOUR, tzinfo=LONDON)
    orders: list[Order] = []
    for n, p in enumerate(report.positions, start=1):
        qty = p.quantity if quantities is None else int(quantities.get(p.symbol, 0))
        if qty == 0:
            continue
        orders.append(
            Order(
                order_id=f"PAPER-{sid}-{n:03d}",
                parent_order_id=None,
                symbol=p.symbol,
                side=Side.BUY if qty > 0 else Side.SELL,
                quantity=abs(int(qty)),
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
    risk_blocks: tuple[dict[str, Any], ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "staging_id": self.staging_id, "signal": self.signal, "dataset": self.dataset, "as_of": self.as_of,
            "gross_notional": self.gross_notional, "long_short": self.long_short,
            "created_at": self.created_at.isoformat(), "status": self.status,
            "risk_blocks": [dict(b) for b in self.risk_blocks],
            "orders": [
                {"order_id": o.order_id, "symbol": o.symbol, "side": o.side.value, "quantity": o.quantity,
                 "strategy": o.strategy, "timestamp": o.timestamp.isoformat(), "trader": o.trader,
                 "account": o.account, "decision_price": o.decision_price}
                for o in self.orders
            ],
        }

    @staticmethod
    def from_payload(d: Mapping[str, Any]) -> StagedOrders:
        orders = tuple(
            Order(order_id=o["order_id"], parent_order_id=None, symbol=o["symbol"], side=Side(o["side"]),
                  quantity=int(o["quantity"]), strategy=o["strategy"], venue=None,
                  timestamp=datetime.fromisoformat(o["timestamp"]), status=OrderStatus.NEW, trader=o["trader"],
                  account=o["account"], decision_price=o["decision_price"])
            for o in d["orders"]
        )
        return StagedOrders(
            staging_id=d["staging_id"], signal=d["signal"], dataset=d["dataset"], as_of=d["as_of"],
            gross_notional=float(d["gross_notional"]), long_short=bool(d["long_short"]),
            created_at=datetime.fromisoformat(d["created_at"]), orders=orders, status=d.get("status", "STAGED"),
            risk_blocks=tuple(d.get("risk_blocks", ())),
        )

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
    """Thread-safe store of staged paper order sets: SQLite-backed when given a database, else in memory."""

    def __init__(self, keep: int = 500, db: Database | None = None) -> None:
        self._keep = keep
        self._lock = threading.Lock()
        self._by_id: dict[str, StagedOrders] = {}
        self.db = db

    def halt_reason(self) -> str | None:
        if self.db is None:
            return None
        from ceap.persistence.killswitch import KillSwitch

        return KillSwitch(self.db).reason()

    def _load(self, sid: str) -> StagedOrders | None:
        if self.db is None:
            return None
        rows = self.db.query("SELECT payload_json FROM staged_orders WHERE staging_id=?", (sid,))
        if not rows:
            return None
        payload = json.loads(rows[0]["payload_json"])
        if "orders" not in payload or "signal" not in payload:
            return None  # a paper-job staging row (different payload shape), not an MCP order set
        return StagedOrders.from_payload(payload)

    def _save(self, staged: StagedOrders) -> bool:
        """Insert once; False when the staging id is already in the database (another caller won)."""
        assert self.db is not None
        from ceap.persistence.repositories import StagedOrderRepository

        return StagedOrderRepository(self.db).save(staged.staging_id, staged.to_payload())

    def stage(
        self,
        report: PortfolioRiskReport,
        quantities: Mapping[str, float] | None = None,
        risk_blocks: Iterable[dict[str, Any]] = (),
    ) -> tuple[StagedOrders, bool]:
        sid = staging_id(report.signal, report.dataset, report.as_of, report.gross_notional, report.long_short)
        with self._lock:
            existing = self._by_id.get(sid) or self._load(sid)
            if existing is not None:
                self._by_id[sid] = existing
                return existing, True
            halt = self.halt_reason()
            if halt:
                raise StagingRefusedError(f"staging refused: the kill switch is set ({halt})")
            staged = StagedOrders(
                staging_id=sid,
                signal=report.signal,
                dataset=report.dataset,
                as_of=report.as_of,
                gross_notional=report.gross_notional,
                long_short=report.long_short,
                created_at=utc_now(),
                orders=build_paper_orders(report, sid, quantities),
                risk_blocks=tuple(risk_blocks),
            )
            if self.db is not None and not self._save(staged):
                stored = self._load(sid)  # a concurrent process staged it first: return that set
                if stored is not None:
                    self._by_id[sid] = stored
                    return stored, True
            self._by_id[sid] = staged
            while len(self._by_id) > self._keep:
                oldest = next(iter(self._by_id))
                self._by_id.pop(oldest)
            return staged, False

    def get(self, sid: str) -> StagedOrders | None:
        with self._lock:
            found = self._by_id.get(sid) or self._load(sid)
            if found is not None:
                self._by_id[sid] = found
            return found

    def list(self) -> list[StagedOrders]:
        with self._lock:
            if self.db is None:
                return list(self._by_id.values())
            out: list[StagedOrders] = []
            for row in self.db.query("SELECT staging_id FROM staged_orders ORDER BY created_at, staging_id"):
                found = self._by_id.get(row["staging_id"]) or self._load(row["staging_id"])
                if found is not None:
                    out.append(found)
            return out[-self._keep:]
