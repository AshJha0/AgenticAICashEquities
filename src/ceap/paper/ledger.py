"""Append-only paper ledger on R4's SQLite :class:`~ceap.persistence.db.Database`.

Four tables (migration ``0003_paper.sql``), all refusing UPDATE and DELETE by trigger:

* ``paper_ledger``: one row per event (order, fill, reject, cancel, split, financing, borrow, mark,
  delist), hash-chained per book on canonical bytes, like the audit log.
* ``paper_days``: the end-of-session snapshot and the full book state, chained per book; its primary
  key ``(book, date)`` is what makes a day idempotent.
* ``paper_forward_log``: the forward record (one row per forward session), never written by backfill.
* ``paper_simulated_log``: rows written by ``backfill``, kept apart.

Writes for one session happen inside the caller's single transaction (see :mod:`ceap.paper.job`).

Keys (0.8.0, item A6). ``paper_ledger`` and ``paper_days`` links written after migration 0004 are
HMAC-SHA256 keyed outside the database (:mod:`ceap.persistence.chainkey`), recorded per row in
``hash_alg``; rows written earlier stay plain SHA-256 and verify as a sealed legacy prefix. The forward
and simulated logs stay plain SHA-256 on purpose: they are exported as a public CSV that anyone must be
able to verify without a key (``ceap paper verify-csv``), and their integrity rests on the git history
of the forward-log branch and the anchored head (:mod:`ceap.persistence.anchor`).

Money in ledger bodies (0.9.0, item F10). Plain idea: a float such as ``0.1 + 0.2`` is not the
number a person reads, so a hash over floats pins an artefact of binary arithmetic. From 0.9.0 every
new ledger row records its money as integer micro-dollars and the hash covers those integers.

Formal statement. A version-2 row carries ``{"money_v2": {"cash_delta": c, "cost": k, "price": p}}``
in its payload, each an ``int`` equal to ``round(x * 1e6)`` (``price`` may be null); the REAL columns
hold exactly ``c / 1e6`` etc. The hashed body is the version-1 body with those three fields replaced
by the integers and ``"body_version": 2`` added. A row whose REAL column disagrees with its integer
gets a marker in the body, so its hash no longer verifies: the columns cannot be edited on their own.
Day rows follow the same version 2: the snapshot JSON carries ``money_v2 = {"equity", "cash"}`` in
micro-dollars, the hash covers the equity integer, and the REAL ``equity`` column holds exactly
``equity / 1e6``. Version-1 rows (no ``money_v2``) are hashed exactly as before, so a database written by 0.8.0, which
the daily forward-log job keeps appending to, verifies unchanged and simply continues in version 2.

Worked example. A fill of 150 shares at 187.123456789 with cost 2.5 dollars has
``cash_delta = -28071.01851835...``; the row stores ``money_v2.cash_delta = -28071018518`` and the
column ``-28071.018518``. Summing the integers over the ledger reproduces cash to the micro-dollar.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Any

from ceap.paper.fills import EPS, BookState
from ceap.persistence.chainkey import LEGACY_ALG, walk_chain
from ceap.persistence.db import GENESIS_HASH, Database, check_anchor, has_column
from ceap.util.canonical import canonical_json

FORWARD_BOOK = "forward"
LOG_SCHEMA_VERSION = 1
LEDGER_FIELDS = ("book", "date", "kind", "order_id", "symbol", "quantity", "price", "cash_delta", "cost")
LEDGER_BODY_VERSION = 2
MONEY_KEY = "money_v2"
MICROS = 1_000_000


def to_micros(x: float) -> int:
    """Dollars to integer micro-dollars, round half to even (``round`` on the scaled float)."""
    return int(round(float(x) * MICROS))


def chain_hash(prev_hash: str, body: dict[str, Any]) -> str:
    """Plain SHA-256 link (forward and simulated logs, and pre-0.8.0 ledger and day rows)."""
    return hashlib.sha256(prev_hash.encode("ascii") + canonical_json(body)).hexdigest()


def _day_money(d: dict[str, Any]) -> dict[str, Any] | None:
    snap = d.get("snapshot_json")
    if not snap or MONEY_KEY not in snap:
        return None
    m = json.loads(snap).get(MONEY_KEY)
    return m if isinstance(m, dict) else None


def _day_body(book: str, d: dict[str, Any]) -> dict[str, Any]:
    body = {"book": book, "date": d["date"], "snapshot": d["snapshot_json"], "state": d["state_json"],
            "ledger_head": d["ledger_head"], "equity": d["equity"]}
    m = _day_money(d)
    if m is None:
        return body  # version 1 day row (0.8.0 and earlier)
    body["body_version"] = LEDGER_BODY_VERSION
    body["equity"] = m.get("equity")
    if not isinstance(m.get("equity"), int) or float(d["equity"]) != m["equity"] / MICROS:
        body["column_mismatch"] = ["equity"]  # breaks the hash on purpose
    return body


def _money(row: dict[str, Any]) -> dict[str, Any] | None:
    payload = row.get("payload_json")
    if not payload or MONEY_KEY not in payload:
        return None
    m = json.loads(payload).get(MONEY_KEY)
    return m if isinstance(m, dict) else None


def _ledger_body(row: dict[str, Any]) -> dict[str, Any]:
    body = {k: row[k] for k in LEDGER_FIELDS}
    body["payload"] = row["payload_json"]
    m = _money(row)
    if m is None:
        return body  # version 1 (0.8.0 and earlier): hashed exactly as written then
    body["body_version"] = LEDGER_BODY_VERSION
    for k in ("cash_delta", "cost", "price"):
        col, u = row[k], m.get(k)
        body[k] = u
        if (u is None) != (col is None) or (u is not None and float(col) != u / MICROS):
            body.setdefault("column_mismatch", []).append(k)  # breaks the hash on purpose
    return body


@dataclass(frozen=True)
class VerifyReport:
    ok: bool
    rows: int
    first_bad: str | None = None
    reason: str = ""


class PaperLedger:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._keyed: bool | None = None

    def _is_keyed(self, conn: sqlite3.Connection) -> bool:
        if self._keyed is None:
            self._keyed = has_column(conn, "paper_ledger", "hash_alg")
        return self._keyed

    def _link(self, conn: sqlite3.Connection, prev: str, body: dict[str, Any]) -> tuple[str, str | None]:
        """(hash, hash_alg) for a new ledger or day row; ``hash_alg`` is None below schema v4."""
        if self._is_keyed(conn):
            return self.db.keys.sign(prev, canonical_json(body)), self.db.keys.alg
        return chain_hash(prev, body), None

    # ------------------------------------------------------------ reads
    def last_day(self, book: str) -> dict[str, Any] | None:
        rows = self.db.query("SELECT * FROM paper_days WHERE book=? ORDER BY date DESC LIMIT 1", (book,))
        return rows[0] if rows else None

    def first_day(self, book: str) -> dict[str, Any] | None:
        rows = self.db.query("SELECT * FROM paper_days WHERE book=? ORDER BY date LIMIT 1", (book,))
        return rows[0] if rows else None

    def has_day(self, book: str, date: str) -> bool:
        return bool(self.db.query("SELECT 1 FROM paper_days WHERE book=? AND date=?", (book, date)))

    def state(self, book: str) -> BookState | None:
        row = self.last_day(book)
        return BookState.from_json(json.loads(row["state_json"])) if row else None

    def days(self, book: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM paper_days WHERE book=? ORDER BY date", (book,))
        out = []
        for r in rows:
            snap = json.loads(r["snapshot_json"])
            snap.pop(MONEY_KEY, None)  # integrity data, not part of the reported snapshot
            out.append(snap)
        return out

    def events(self, book: str) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM paper_ledger WHERE book=? ORDER BY seq", (book,))

    def log(self, book: str) -> list[dict[str, Any]]:
        if book == FORWARD_BOOK:
            rows = self.db.query("SELECT * FROM paper_forward_log ORDER BY seq")
        else:
            rows = self.db.query("SELECT * FROM paper_simulated_log WHERE book=? ORDER BY seq", (book,))
        return [dict(json.loads(r["row_json"]), prev_hash=r["prev_hash"], hash=r["hash"]) for r in rows]

    def heads(self, book: str) -> dict[str, str]:
        def head(sql: str, params: tuple[Any, ...]) -> str:
            rows = self.db.query(sql, params)
            return rows[0]["hash"] if rows else GENESIS_HASH

        log_sql = ("SELECT hash FROM paper_forward_log ORDER BY seq DESC LIMIT 1", ()) if book == FORWARD_BOOK \
            else ("SELECT hash FROM paper_simulated_log WHERE book=? ORDER BY seq DESC LIMIT 1", (book,))
        return {
            "ledger": head("SELECT hash FROM paper_ledger WHERE book=? ORDER BY seq DESC LIMIT 1", (book,)),
            "days": head("SELECT hash FROM paper_days WHERE book=? ORDER BY date DESC LIMIT 1", (book,)),
            "log": head(*log_sql),
        }

    # ----------------------------------------------------------- writes (inside the caller's transaction)
    @staticmethod
    def _head(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> str:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else GENESIS_HASH

    def append_events(self, conn: sqlite3.Connection, book: str, date: str, events: list[dict[str, Any]]) -> str:
        prev = self._head(conn, "SELECT hash FROM paper_ledger WHERE book=? ORDER BY seq DESC LIMIT 1", (book,))
        for e in events:
            price = e.get("price")
            cash_u, cost_u = to_micros(e.get("cash_delta", 0.0)), to_micros(e.get("cost", 0.0))
            price_u = None if price is None else to_micros(price)
            money = {"cash_delta": cash_u, "cost": cost_u, "price": price_u}
            detail = dict(e.get("detail", {}))
            if MONEY_KEY in detail:
                raise ValueError(f"event detail may not carry the reserved key {MONEY_KEY!r}")
            detail[MONEY_KEY] = money
            row = {
                "book": book, "date": date, "kind": e["kind"], "order_id": e.get("order_id"),
                "symbol": e.get("symbol"), "quantity": e.get("quantity"),
                "price": None if price_u is None else price_u / MICROS,
                "cash_delta": cash_u / MICROS, "cost": cost_u / MICROS,
                "payload_json": canonical_json(detail).decode("utf-8"),
            }
            digest, alg = self._link(conn, prev, _ledger_body(row))
            if alg is None:
                conn.execute(
                    "INSERT INTO paper_ledger(book, date, kind, order_id, symbol, quantity, price, cash_delta, "
                    "cost, payload_json, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*[row[k] for k in LEDGER_FIELDS], row["payload_json"], prev, digest),
                )
            else:
                conn.execute(
                    "INSERT INTO paper_ledger(book, date, kind, order_id, symbol, quantity, price, cash_delta, "
                    "cost, payload_json, prev_hash, hash, hash_alg) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*[row[k] for k in LEDGER_FIELDS], row["payload_json"], prev, digest, alg),
                )
            prev = digest
        return prev

    def append_day(self, conn: sqlite3.Connection, book: str, snapshot: dict[str, Any], state: BookState,
                   ledger_head: str, recorded_at: str) -> str:
        prev = self._head(conn, "SELECT hash FROM paper_days WHERE book=? ORDER BY date DESC LIMIT 1", (book,))
        if MONEY_KEY in snapshot:
            raise ValueError(f"snapshot may not carry the reserved key {MONEY_KEY!r}")
        equity_u, cash_u = to_micros(snapshot["equity"]), to_micros(state.cash)
        snap_json = canonical_json(dict(_finite_or_none(snapshot),
                                        **{MONEY_KEY: {"equity": equity_u, "cash": cash_u}})).decode("utf-8")
        equity = equity_u / MICROS
        state_json = canonical_json(state.to_json()).decode("utf-8")
        digest, alg = self._link(conn, prev, {"book": book, "date": snapshot["date"], "snapshot": snap_json,
                                              "state": state_json, "ledger_head": ledger_head,
                                              "equity": equity_u, "body_version": LEDGER_BODY_VERSION})
        values = (book, snapshot["date"], equity, snap_json, state_json, ledger_head, prev,
                  digest, recorded_at)
        if alg is None:
            conn.execute(
                "INSERT INTO paper_days(book, date, equity, snapshot_json, state_json, ledger_head, prev_hash, "
                "hash, recorded_at) VALUES (?,?,?,?,?,?,?,?,?)", values)
        else:
            conn.execute(
                "INSERT INTO paper_days(book, date, equity, snapshot_json, state_json, ledger_head, prev_hash, "
                "hash, recorded_at, hash_alg) VALUES (?,?,?,?,?,?,?,?,?,?)", (*values, alg))
        return digest

    def append_log(self, conn: sqlite3.Connection, book: str, row: dict[str, Any], recorded_at: str) -> str:
        body = dict(_finite_or_none(row), schema_version=LOG_SCHEMA_VERSION)
        row_json = canonical_json(body).decode("utf-8")
        if book == FORWARD_BOOK:
            prev = self._head(conn, "SELECT hash FROM paper_forward_log ORDER BY seq DESC LIMIT 1", ())
            digest = chain_hash(prev, body)
            conn.execute(
                "INSERT INTO paper_forward_log(date, row_json, prev_hash, hash, recorded_at) VALUES (?,?,?,?,?)",
                (row["date"], row_json, prev, digest, recorded_at),
            )
        else:
            prev = self._head(conn, "SELECT hash FROM paper_simulated_log WHERE book=? ORDER BY seq DESC LIMIT 1",
                              (book,))
            digest = chain_hash(prev, body)
            conn.execute(
                "INSERT INTO paper_simulated_log(book, date, row_json, prev_hash, hash, recorded_at) "
                "VALUES (?,?,?,?,?,?)",
                (book, row["date"], row_json, prev, digest, recorded_at),
            )
        return digest

    # ----------------------------------------------------------- verification
    def chain_rows(self, chain: str, book: str) -> list[dict[str, Any]]:
        """Rows of the ``ledger`` or ``days`` chain of ``book``, in chain order."""
        if chain == "ledger":
            return self.events(book)
        return self.db.query("SELECT * FROM paper_days WHERE book=? ORDER BY date", (book,))

    def verify_keyed(self, chain: str, book: str, expected_head: str | None = None,
                     expected_rows: int | None = None) -> tuple[bool, str | None, str]:
        """Verify the keyed ``ledger`` or ``days`` chain of ``book``; returns (ok, first bad label, reason)."""
        rows = self.chain_rows(chain, book)
        legacy, err = self.db.legacy_rows(f"paper_{chain}:{book}")
        if err:
            return False, None, err
        if chain == "ledger":
            links = [(f"ledger seq {r['seq']}", r["prev_hash"], r["hash"], r.get("hash_alg", LEGACY_ALG),
                      canonical_json(_ledger_body(r))) for r in rows]
        else:
            links = [(f"day {d['date']}", d["prev_hash"], d["hash"], d.get("hash_alg", LEGACY_ALG),
                      canonical_json(_day_body(book, d))) for d in rows]
        bad, reason = walk_chain(links, self.db.keys, legacy)
        if bad is not None:
            return False, links[bad][0], f"{chain} row: {reason}"
        problem = check_anchor([r["hash"] for r in rows], expected_head, expected_rows)
        if problem:
            return False, None, f"{chain}: {problem}"
        return True, None, ""

    def verify(self, book: str, anchor: dict[str, dict[str, Any]] | None = None) -> VerifyReport:
        """Recompute every chain of ``book`` (ledger, days, log); with ``anchor`` (``{"ledger": {"head",
        "rows"}, "days": ..., "log": ...}``) also check each against its externally recorded head."""
        anchor = anchor or {}
        rows = self.events(book)
        for chain in ("ledger", "days"):
            a = anchor.get(chain, {})
            ok, where, reason = self.verify_keyed(chain, book, a.get("head"), a.get("rows"))
            if not ok:
                return VerifyReport(False, len(rows), where, reason)
        prev = GENESIS_HASH
        for lr in self.log(book):
            body = {k: v for k, v in lr.items() if k not in ("prev_hash", "hash")}
            if lr["prev_hash"] != prev or chain_hash(prev, body) != lr["hash"]:
                return VerifyReport(False, len(rows), f"log {lr['date']}", "log row does not match its hash")
            prev = lr["hash"]
        a = anchor.get("log", {})
        problem = check_anchor([lr["hash"] for lr in self.log(book)], a.get("head"), a.get("rows"))
        if problem:
            return VerifyReport(False, len(rows), None, f"log: {problem}")
        return VerifyReport(True, len(rows))

    def chain_lengths(self, book: str) -> dict[str, int]:
        return {"ledger": len(self.events(book)), "days": len(self.chain_rows("days", book)),
                "log": len(self.log(book))}

    def reconcile(self, book: str, tol: float = 1e-6) -> dict[str, Any]:
        """Ledger positions equal the sum of fills (plus split and delist adjustments); cash equals capital
        plus the sum of every cash delta. Compared with the stored state of the last day."""
        state = self.state(book)
        if state is None:
            return {"ok": True, "days": 0, "breaks": []}
        pos: dict[str, float] = {}
        cash_u = 0  # version-2 rows: exact integer micro-dollars
        n_v2 = 0
        cash_f = 0.0  # version-1 rows: the float column
        for e in self.events(book):
            m = _money(e)
            if m is not None:
                cash_u += int(m["cash_delta"])
                n_v2 += 1
            else:
                cash_f += e["cash_delta"]
            if e["kind"] in ("fill", "split", "delist") and e["quantity"] is not None:
                pos[e["symbol"]] = pos.get(e["symbol"], 0.0) + e["quantity"]
        cash = state.capital + cash_f + cash_u / MICROS
        breaks = []
        for sym in sorted(set(pos) | set(state.positions)):
            a, b = pos.get(sym, 0.0), state.positions.get(sym, 0.0)
            if abs(a - b) > tol * max(1.0, abs(b)) and not (abs(a) <= EPS and abs(b) <= EPS):
                breaks.append({"symbol": sym, "ledger": a, "state": b})
        if abs(cash - state.cash) > tol * max(1.0, abs(state.cash)):
            breaks.append({"symbol": "CASH", "ledger": cash, "state": state.cash})
        equity = cash + sum(q * state.marks[s] for s, q in pos.items() if abs(q) > EPS and s in state.marks)
        if abs(equity - state.equity) > tol * max(1.0, abs(state.equity)):
            breaks.append({"symbol": "EQUITY", "ledger": equity, "state": state.equity})
        last = self.last_day(book)
        if last is not None and abs(float(last["equity"]) - state.equity) > tol * max(1.0, abs(state.equity)):
            breaks.append({"symbol": "EQUITY_COLUMN", "ledger": float(last["equity"]), "state": state.equity})
        dm = _day_money(last) if last is not None else None
        if dm is not None and not cash_f:
            # all-integer path: ledger cash in micro-dollars against the day row's recorded cash. Each
            # event's money was rounded once (at most half a micro-dollar), so the sums may differ by
            # at most n_v2 / 2 micro-dollars plus the rounding of capital and of the day's cash.
            ledger_u = to_micros(state.capital) + cash_u
            if abs(ledger_u - int(dm["cash"])) > n_v2 // 2 + 2:
                breaks.append({"symbol": "CASH_MICROS", "ledger": ledger_u, "state": int(dm["cash"])})
        return {"ok": not breaks, "days": len(self.days(book)), "breaks": breaks}


def _finite_or_none(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        out[k] = None if isinstance(v, float) and not math.isfinite(v) else v
    return out
