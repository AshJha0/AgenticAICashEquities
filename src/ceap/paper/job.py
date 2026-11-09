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
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
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
from ceap.data.calendar import is_early_close, is_trading_day
from ceap.mcp.execution.staging import PAPER_ACCOUNT, portfolio_hash
from ceap.paper.config import PaperConfig
from ceap.paper.feed import LABEL, DailyFeed, MarketPanel, get_feed
from ceap.paper.fills import EPS, BookState, DayResult, OpenOrder, simulate_day
from ceap.paper.ledger import FORWARD_BOOK, PaperLedger
from ceap.paper.pretrade import check_orders, halt_status
from ceap.persistence.db import Database
from ceap.persistence.repositories import SqlApprovalStore, StagedOrderRepository
from ceap.policy.approvals import APPROVED, ApprovalRequest, QueuedApprovalGateway

PAPER_AUTO = "paper-auto"
REQUESTER = "paper-job"
SIMULATED_BOOK = "simulated"
VOL_LOOKBACK = 40


class PaperJobError(ValueError):
    """The request cannot be honoured (gap, wrong book, date outside the feed)."""


class NotASessionError(PaperJobError):
    """The date is not an NYSE session (weekend or holiday) or not in the feed: nothing to do."""


class PaperReconciliationError(RuntimeError):
    """End-of-day reconciliation found breaks; the session was not committed (fails loudly)."""

    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


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
        report_dir: str | Path | None = None,
    ) -> None:
        chosen = report_dir if report_dir is not None else config.report_dir
        self.report_dir = Path(chosen) if chosen else None
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
        d = date.fromisoformat(str(day)[:10])
        if not is_trading_day(d):
            raise NotASessionError(f"{d} is not an NYSE session (weekend or holiday); no run")
        try:
            return self.panel.index_of(day)
        except KeyError as exc:
            raise NotASessionError(str(exc)) from exc

    def _auto_allowed(self) -> bool:
        return environment() == "dev" or self.allow_paper_auto

    def _session(self, book: str, i: int, *, catch_up: bool, crash: str | None = None) -> DayOutcome:
        day = self.panel.day(i)
        if not is_trading_day(date.fromisoformat(day)):
            raise NotASessionError(f"feed row {day} is not an NYSE session; refusing to book it")
        if self.ledger.has_day(book, day):
            return DayOutcome(book, day, "noop")
        state = self.ledger.state(book) or BookState.new(self.config.gross_notional)
        halt, halt_source = halt_status(self.db)  # env CEAP_PAPER_HALT or the DB kill switch
        pre: list[dict[str, Any]] = []
        if halt:  # kill switch: no new orders, every open order cancelled, reason logged
            state = state.copy()
            for sym in sorted(state.orders):
                o = state.orders.pop(sym)
                state.waived[sym] = state.waived.get(sym, 0.0) + o.quantity
                pre.append({"kind": "cancel", "symbol": sym, "quantity": o.quantity, "order_id": o.order_id,
                            "cash_delta": 0.0, "cost": 0.0,
                            "detail": {"reason": "kill_switch", "halt": halt, **o.trace()}})
        res = simulate_day(state, self.panel, i, self.params, float(self.rf[i]), self.vol_row(i))
        st, events = res.state, pre + list(res.events)
        approval_id: str | None = None
        staged_payload: dict[str, Any] | None = None
        n_orders = 0
        if halt:
            events.append({"kind": "halt", "symbol": None, "quantity": None, "price": None, "order_id": None,
                           "cash_delta": 0.0, "cost": 0.0,
                           "detail": ({"reason": halt, "env": "CEAP_PAPER_HALT"} if halt_source == "env"
                                      else {"reason": halt, "source": "kill_switch_db"})})
        elif (i - st.first_index) % self.config.rebalance == 0:
            target = self.target_shares(i, st.equity)
            if target is not None:
                for sym in sorted(st.orders):  # a new target supersedes every working order
                    o = st.orders.pop(sym)
                    events.append({"kind": "cancel", "symbol": sym, "quantity": o.quantity, "order_id": o.order_id,
                                   "cash_delta": 0.0, "cost": 0.0, "detail": {"reason": "superseded", **o.trace()}})
                chk = check_orders(self._deltas(st, target), st.positions, st.equity, self.panel, i,
                                   self.params.risk, self.config.gross_leverage,
                                   session_cap_fraction=self.params.session_cap_fraction())
                st.target = {k: v for k, v in target.items() if v}
                st.waived = {}
                for blk in chk.blocks:
                    st.waived[blk["symbol"]] = st.waived.get(blk["symbol"], 0.0) + blk["quantity"]
                events.extend(chk.blocks)
                if chk.deltas:
                    if not self._auto_allowed():
                        raise PaperAutoRefusedError(
                            f"CEAP_ENV={environment()!r}: paper-auto may decide only in dev or with "
                            "--allow-paper-auto; nothing was written"
                        )
                    approval_id, staged_payload = self._approve_and_stage(book, i, st, target, chk.deltas, events)
                    n_orders = len(chk.deltas)
        snapshot = dict(res.snapshot, early_close=is_early_close(date.fromisoformat(day)), halted=bool(halt))
        recon = self._eod_reconcile(book, i, st, events)
        if not recon["ok"]:
            self._write_report(book, day, snapshot, events, recon, approval_id, staged_payload, halt, failed=True)
            raise PaperReconciliationError(f"end-of-day reconciliation failed for {book} {day}: "
                                           f"{len(recon['breaks'])} break(s); nothing committed", recon)
        res = DayResult(res.events, st, snapshot)
        self._commit(book, i, res, st, events, staged_payload, catch_up, crash)
        self._write_report(book, day, snapshot, events, recon, approval_id, staged_payload, halt)
        return DayOutcome(book, day, "done", catch_up, n_orders, approval_id, snapshot)

    def _eod_reconcile(self, book: str, i: int, st: BookState, events: list[dict[str, Any]],
                       tol: float = 1e-6) -> dict[str, Any]:
        """Ledger (stored events plus today's) against the book; then target against actual shares."""
        pos: dict[str, float] = {}
        cash = st.capital
        for e in [*self.ledger.events(book), *events]:
            cash += float(e.get("cash_delta") or 0.0)
            if e["kind"] in ("fill", "split", "delist") and e.get("quantity") is not None:
                pos[e["symbol"]] = pos.get(e["symbol"], 0.0) + float(e["quantity"])
        breaks: list[dict[str, Any]] = []
        for sym in sorted(set(pos) | set(st.positions)):
            a, b = pos.get(sym, 0.0), st.positions.get(sym, 0.0)
            if abs(a - b) > tol * max(1.0, abs(b)):
                breaks.append({"type": "position", "symbol": sym, "from_fills": a, "book": b})
        if abs(cash - st.cash) > tol * max(1.0, abs(st.cash)):
            breaks.append({"type": "cash", "from_fills": cash, "book": st.cash})
        if st.target and st.equity > 0:
            for sym in sorted(set(st.target) | set(st.positions) | set(st.orders)):
                o = st.orders.get(sym)
                gap = (st.target.get(sym, 0.0) - st.positions.get(sym, 0.0) - (o.quantity if o else 0.0)
                       - st.waived.get(sym, 0.0))
                price = st.marks.get(sym) or float(self.panel.close[i, self.panel.col(sym)])
                frac = abs(gap) * price / st.equity if np.isfinite(price) else 0.0
                if frac > self.params.risk.drift_tolerance:
                    breaks.append({"type": "drift", "symbol": sym, "unexplained_shares": gap, "fraction": frac})
        return {"ok": not breaks, "breaks": breaks, "drift_tolerance": self.params.risk.drift_tolerance}

    def _write_report(self, book: str, day: str, snapshot: dict[str, Any], events: list[dict[str, Any]],
                      recon: dict[str, Any], approval_id: str | None, staged: dict[str, Any] | None,
                      halt: str | None, failed: bool = False) -> None:
        """Per-run artefact: ``<report_dir>/<book>/<date>.json`` and ``.md``."""
        if self.report_dir is None:
            return
        folder = self.report_dir / book
        folder.mkdir(parents=True, exist_ok=True)
        pick = ("symbol", "quantity", "price", "cost", "order_id")
        fills = [dict({k: e.get(k) for k in pick}, staging_id=e["detail"].get("staging_id"),
                      approval_id=e["detail"].get("approval_id")) for e in events if e["kind"] == "fill"]
        exceptions = [dict({k: e.get(k) for k in pick}, kind=e["kind"], **e["detail"])
                      for e in events if e["kind"] in ("reject", "cancel", "risk_block", "halt")]
        doc = {
            "label": LABEL, "book": book, "date": day, "status": "FAILED_RECONCILIATION" if failed else "OK",
            "halt": halt, "approval_id": approval_id, "staging_id": staged["staging_id"] if staged else None,
            "snapshot": snapshot, "reconciliation": recon, "fills": fills, "exceptions": exceptions,
        }
        (folder / f"{day}.json").write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
        attribution = snapshot.get("attribution", {})
        md = [
            f"# Paper day {day} ({book})", "", f"_{LABEL}_", "",
            f"Status: **{doc['status']}**" + (f"; kill switch: {halt}" if halt else ""), "",
            f"Equity {snapshot['equity']:,.2f}; return {snapshot['daily_return'] * 1e4:.1f} bps; "
            f"gross {snapshot['gross_exposure']:.3f}; net {snapshot['net_exposure']:.3f}; "
            f"turnover {snapshot['turnover']:.4f}" + ("; half day" if snapshot.get("early_close") else ""), "",
            "| Attribution | P&L |", "|---|---:|",
            *[f"| {k} | {v:,.2f} |" for k, v in attribution.items()], "",
            f"Fills: {len(fills)}; exceptions: {len(exceptions)}; approval {approval_id or '-'}; "
            f"staging {doc['staging_id'] or '-'}", "",
            f"Reconciliation: {'OK' if recon['ok'] else 'BREAKS'}",
            *[f"- {json.dumps(b, default=str)}" for b in recon["breaks"]],
        ]
        (folder / f"{day}.md").write_text("\n".join(md) + "\n", encoding="utf-8")

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
        for od in orders:
            st.orders[str(od["symbol"])] = OpenOrder(
                str(od["order_id"]), str(od["symbol"]), float(od["quantity"]), day, staging_id=sid,
                approval_id=req.id, decision_price=float(od["decision_price"]))
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
            for e in events:
                if e["kind"] in ("risk_block", "halt"):
                    subject = f"{book}:{day}:{e.get('symbol') or ''}"
                    self.db.append_audit("paper-risk", f"paper.{e['kind']}", subject, dict(e["detail"]))
            self.db.append_audit(REQUESTER, "paper.day_closed", f"{book}:{day}",
                                 {"equity": snap["equity"], "ledger_head": head, "catch_up": catch_up})

        self.db.run(write)
