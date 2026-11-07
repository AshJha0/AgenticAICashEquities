"""The daily paper job: fill yesterday's orders, mark, stage today's target through the approval gateway.

``run_day(D)`` is idempotent per ``(book, D)``: each session is one SQLite transaction whose last insert is
the ``paper_days`` row keyed by ``(book, date)``, so a crash anywhere inside leaves nothing and a re-run
finishes the day; a re-run of a finished day is a no-op. Missed sessions between the last recorded day
and ``D`` are processed in order first (catch-up, flagged on the row).

Staging goes through the SAME :class:`~ceap.policy.approvals.QueuedApprovalGateway` the platform uses,
with R4's :class:`~ceap.persistence.repositories.SqlApprovalStore` and the hash-chained audit log. The
system principal ``paper-auto`` may decide only in ``CEAP_ENV=dev`` or with an explicit
``allow_paper_auto`` flag; both cases are audited. Otherwise the job refuses before writing anything.
The approval id is deterministic per (book, date), so after a crash the earlier decision is reused when
its content hash matches, and never silently replaced.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from ceap.analytics.backtest import (
    MIN_NAMES,
    daily_returns,
    risk_free_daily,
    rolling_vol_bps,
    target_weights,
)
from ceap.analytics.signals import compute_signal, cross_sectional_zscore
from ceap.mcp.execution.staging import PAPER_ACCOUNT, portfolio_hash
from ceap.paper.config import PaperConfig
from ceap.paper.feed import LABEL, DailyFeed, MarketPanel, get_feed
from ceap.paper.fills import EPS, BookState, DayResult, OpenOrder, simulate_day
from ceap.paper.ledger import FORWARD_BOOK, PaperLedger
from ceap.persistence.db import Database
from ceap.persistence.repositories import SqlApprovalStore, StagedOrderRepository
from ceap.policy.approvals import APPROVED, ApprovalRequest, QueuedApprovalGateway

PAPER_AUTO = "paper-auto"
REQUESTER = "paper-job"
SIMULATED_BOOK = "simulated"
VOL_LOOKBACK = 40


class PaperJobError(ValueError):
    """The request cannot be honoured (gap, wrong book, date outside the feed)."""


class PaperAutoRefusedError(PermissionError):
    """``paper-auto`` may not decide here: not ``CEAP_ENV=dev`` and no explicit flag."""


@dataclass(frozen=True)
class DayOutcome:
    book: str
    date: str
    status: str  # "done" | "noop"
    catch_up: bool = False
    orders_staged: int = 0
    approval_id: str | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)


def environment() -> str:
    return os.getenv("CEAP_ENV", "dev").lower()


class PaperJob:
    def __init__(
        self,
        db: Database,
        config: PaperConfig,
        feed: DailyFeed | None = None,
        *,
        allow_paper_auto: bool = False,
        risk_free: np.ndarray | None = None,
        approval_timeout_s: float = 30.0,
    ) -> None:
        self.db = db
        self.config = config
        self.feed = feed or get_feed(config.feed, scenario=config.scenario, forward_start=config.forward_start)
        self.panel: MarketPanel = self.feed.panel()
        self.params = config.fill_params()
        self.ledger = PaperLedger(db)
        self.staged = StagedOrderRepository(db)
        self.gateway = QueuedApprovalGateway(approval_timeout_s, store=SqlApprovalStore(db), audit=db.append_audit)
        self.allow_paper_auto = allow_paper_auto
        if risk_free is not None:
            self.rf, self.rf_source = np.asarray(risk_free, dtype=float), "supplied by the caller"
        else:
            self.rf, self.rf_source = risk_free_daily(self.panel.dates)

    # ------------------------------------------------------------- public
    def run_day(self, day: str, *, _crash: str | None = None) -> list[DayOutcome]:
        """Forward book: process every session from the last recorded one (or the forward start) to ``day``."""
        day = str(day)[:10]
        if day < self.config.forward_start:
            raise PaperJobError(
                f"{day} is before the forward start {self.config.forward_start}; the forward record is never "
                "backfilled - use `ceap paper backfill` (writes the separate simulated log)"
            )
        i = self._index(day)
        last = self.ledger.last_day(FORWARD_BOOK)
        if last is None:
            first = self._index(self.config.forward_start)
        else:
            li = self._index(last["date"])
            if i <= li:
                return [DayOutcome(FORWARD_BOOK, day, "noop")]
            first = li + 1
        return [self._session(FORWARD_BOOK, k, catch_up=k != i, crash=_crash) for k in range(first, i + 1)]

    def backfill(self, start: str, end: str, book: str = SIMULATED_BOOK) -> list[DayOutcome]:
        if book == FORWARD_BOOK:
            raise PaperJobError("backfill never writes the forward book; choose another --book")
        i0, i1 = self._index(start), self._index(end)
        if i1 < i0:
            raise PaperJobError("--to is before --from")
        last = self.ledger.last_day(book)
        if last is not None:
            li = self._index(last["date"])
            first = self.ledger.first_day(book)
            fi = self._index(first["date"]) if first else li
            if i0 < fi:
                raise PaperJobError(f"book {book!r} starts {self.panel.day(fi)}; use a new --book to start earlier")
            if i0 > li + 1:
                raise PaperJobError(f"gap: book {book!r} ends {last['date']}; continue from the next session")
        return [self._session(book, k, catch_up=False) for k in range(i0, i1 + 1)]

    # ------------------------------------------------------------ target
    def target_shares(self, i: int, equity: float) -> dict[str, float] | None:
        """Target shares at the close of session ``i`` from rows ``0..i`` only (no look-ahead)."""
        p = self.panel
        adj = p.adjusted_close(i)
        sig = compute_signal(self.config.signal, adj, p.volume[: i + 1])[i].copy()
        sig[~p.members[i]] = np.nan
        if np.isfinite(sig).sum() < MIN_NAMES or equity <= 0:
            return None
        cfg = self.config
        w = target_weights(cross_sectional_zscore(sig), cfg.long_short, cfg.top_fraction, cfg.weighting)
        w = w * cfg.gross_leverage
        w[~p.members[i]] = 0.0
        cap = self.params.caps.max_weight * cfg.gross_leverage
        w = np.clip(w, -cap, cap)
        out: dict[str, float] = {}
        for j, sym in enumerate(p.symbols):
            price = float(p.close[i, j])
            if w[j] == 0 or not np.isfinite(price) or price <= 0:
                continue
            shares = w[j] * equity / price
            shares = shares if cfg.fractional_shares else float(int(shares))
            if shares:
                out[sym] = shares
        return out

    def vol_row(self, i: int) -> np.ndarray:
        lo = max(0, i - VOL_LOOKBACK)
        adj = self.panel.adjusted_close(i)[lo:]
        return np.asarray(rolling_vol_bps(daily_returns(adj), self.params.costs.vol_window)[-1])

    # ----------------------------------------------------------- internals
    def _index(self, day: str) -> int:
        try:
            return self.panel.index_of(day)
        except KeyError as exc:
            raise PaperJobError(str(exc)) from exc

    def _auto_allowed(self) -> bool:
        return environment() == "dev" or self.allow_paper_auto

    def _session(self, book: str, i: int, *, catch_up: bool, crash: str | None = None) -> DayOutcome:
        day = self.panel.day(i)
        if self.ledger.has_day(book, day):
            return DayOutcome(book, day, "noop")
        state = self.ledger.state(book) or BookState.new(self.config.gross_notional)
        res = simulate_day(state, self.panel, i, self.params, float(self.rf[i]), self.vol_row(i))
        st, events = res.state, list(res.events)
        approval_id: str | None = None
        n_orders = 0
        staged_payload: dict[str, Any] | None = None
        if (i - st.first_index) % self.config.rebalance == 0:
            target = self.target_shares(i, st.equity)
            if target is not None:
                deltas = self._deltas(st, target)
                if deltas:
                    if not self._auto_allowed():
                        raise PaperAutoRefusedError(
                            f"CEAP_ENV={environment()!r}: paper-auto may decide only in dev or with "
                            "--allow-paper-auto; nothing was written"
                        )
                    approval_id, staged_payload = self._approve_and_stage(book, i, st, target, deltas, events)
                    n_orders = len(deltas)
        self._commit(book, i, res, st, events, staged_payload, catch_up, crash)
        return DayOutcome(book, day, "done", catch_up, n_orders, approval_id, res.snapshot)

    def _deltas(self, st: BookState, target: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for sym in sorted(set(target) | set(st.positions)):
            d = target.get(sym, 0.0) - st.positions.get(sym, 0.0)
            if abs(d) > EPS:
                out[sym] = d
        return out

    def _approve_and_stage(self, book: str, i: int, st: BookState, target: dict[str, float],
                           deltas: dict[str, float], events: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        day = self.panel.day(i)
        p_hash = portfolio_hash(day, [(sym, round(q)) for sym, q in target.items()])
        sid = "PAPER-STG-" + hashlib.sha256(f"{book}|{day}|{p_hash}".encode()).hexdigest()[:10]
        orders = [
            {"order_id": f"{sid}-{n:03d}", "symbol": sym, "quantity": q, "side": "BUY" if q > 0 else "SELL",
             "decision_price": float(self.panel.close[i, self.panel.col(sym)])}
            for n, (sym, q) in enumerate(sorted(deltas.items()), start=1)
        ]
        req = ApprovalRequest(
            id=f"APR-PAPER-{book}-{day}", task_id=f"paper:{book}:{day}", step_id="stage",
            tool_id="paper.stage_orders",
            arguments={"book": book, "date": day, "staging_id": sid, "portfolio_hash": p_hash,
                       "account": PAPER_ACCOUNT, "orders": [[o["symbol"], o["quantity"]] for o in orders]},
            reason=f"paper rebalance {day} ({LABEL})", requested_by=REQUESTER,
        )
        self._decide(req)
        for sym in sorted(st.orders):
            o = st.orders.pop(sym)
            events.append({"kind": "cancel", "symbol": sym, "quantity": o.quantity, "order_id": o.order_id,
                           "cash_delta": 0.0, "cost": 0.0, "detail": {"reason": "superseded", "by": sid}})
        for od in orders:
            st.orders[str(od["symbol"])] = OpenOrder(str(od["order_id"]), str(od["symbol"]), float(od["quantity"]), day)
            events.append({"kind": "order", "symbol": od["symbol"], "quantity": od["quantity"],
                           "price": od["decision_price"], "order_id": od["order_id"], "cash_delta": 0.0, "cost": 0.0,
                           "detail": {"staging_id": sid, "approval_id": req.id, "fills_from": "next session close"}})
        payload = {"staging_id": sid, "book": book, "as_of": day, "account": PAPER_ACCOUNT, "approval_id": req.id,
                   "portfolio_hash": p_hash, "orders": orders, "label": LABEL}
        return req.id, payload

    def _decide(self, req: ApprovalRequest) -> None:
        store = self.gateway.store
        status = store.status(req.id)
        if status is not None:  # a crashed earlier attempt of this very session
            row = store.get(req.id) if isinstance(store, SqlApprovalStore) else None
            if status == APPROVED and row is not None and row["content_hash"] == req.content_hash:
                self.db.append_audit(REQUESTER, "paper.approval_reused", req.id, {"content_hash": req.content_hash})
                return
            raise PaperJobError(f"approval {req.id} exists with status {status} and a different payload; "
                                "manual review needed")

        async def approve() -> bool:
            task = asyncio.ensure_future(self.gateway.request(req))
            for _ in range(10_000):
                if any(p.id == req.id for p in self.gateway.pending()):
                    break
                await asyncio.sleep(0)
            self.db.append_audit(PAPER_AUTO, "paper.auto_decide", req.id,
                                 {"env": environment(), "explicit_flag": self.allow_paper_auto})
            self.gateway.decide(req.id, True, PAPER_AUTO, "paper-auto: rule-based paper staging, no model")
            return (await task).approved

        if not asyncio.run(approve()):
            raise PaperJobError(f"approval {req.id} was not granted")

    def _commit(self, book: str, i: int, res: DayResult, st: BookState, events: list[dict[str, Any]],
                staged: dict[str, Any] | None, catch_up: bool, crash: str | None) -> None:
        day = self.panel.day(i)
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        snap = dict(res.snapshot, open_orders=len(st.orders))
        rf = float(self.rf[i])
        log_row = {
            "date": day, "book": book, "equity": snap["equity"], "daily_return": snap["daily_return"], "rf": rf,
            "excess_return": snap["daily_return"] - rf, "costs": snap["costs"], "borrow": snap["borrow"],
            "financing": snap["financing"], "turnover": snap["turnover"], "gross_exposure": snap["gross_exposure"],
            "net_exposure": snap["net_exposure"], "n_positions": snap["n_positions"], "catch_up": catch_up,
            "feed": self.panel.name, "label": LABEL,
        }

        def write(conn: Any) -> None:
            head = self.ledger.append_events(conn, book, day, events)
            if crash == "after_events":
                raise RuntimeError("injected crash after ledger events")
            if staged is not None:
                self.staged.save(staged["staging_id"], staged)
            self.ledger.append_log(conn, book, log_row, now)
            if crash == "before_day":
                raise RuntimeError("injected crash before the day row")
            self.ledger.append_day(conn, book, snap, st, head, now)
            self.db.append_audit(REQUESTER, "paper.day_closed", f"{book}:{day}",
                                 {"equity": snap["equity"], "ledger_head": head, "catch_up": catch_up})

        self.db.run(write)
