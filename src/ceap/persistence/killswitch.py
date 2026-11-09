"""DB-backed kill switch (0.8.0, item F11).

The switch is an append-only log of ``set`` and ``clear`` events (``kill_switch_events``, migration
0004); the latest event is the state. Both the MCP staging path (``stage_orders``) and the paper job
check it, so one ``ceap db kill-switch set`` stops new paper orders everywhere that shares the database.
Every set and clear also writes a row to the hash-chained audit log, inside the same transaction.

The older environment switch ``CEAP_PAPER_HALT`` still works for the paper job: either one halts.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ceap.persistence.db import Database, has_column
from ceap.util.canonical import canonical_datetime

AUDIT_SUBJECT = "kill_switch"


@dataclass(frozen=True)
class KillSwitchState:
    active: bool
    reason: str | None = None
    actor: str | None = None
    at: str | None = None


class KillSwitch:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _available(self, conn: sqlite3.Connection) -> bool:
        return bool(
            conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kill_switch_events'").fetchone()
        )

    def state(self) -> KillSwitchState:
        def read(conn: sqlite3.Connection) -> KillSwitchState:
            if not self._available(conn):
                return KillSwitchState(False)
            row = conn.execute(
                "SELECT at, actor, action, reason FROM kill_switch_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            if row is None or row["action"] == "clear":
                return KillSwitchState(False)
            return KillSwitchState(True, row["reason"], row["actor"], row["at"])

        return self.db.run(read, write=False)

    def reason(self) -> str | None:
        st = self.state()
        return st.reason if st.active else None

    def set(self, actor: str, reason: str) -> KillSwitchState:
        if not reason.strip():
            raise ValueError("a kill switch needs a reason")
        return self._write(actor, "set", reason.strip())

    def clear(self, actor: str, reason: str = "cleared") -> KillSwitchState:
        return self._write(actor, "clear", reason.strip() or "cleared")

    def _write(self, actor: str, action: str, reason: str) -> KillSwitchState:
        from ceap.domain.common import utc_now

        at = canonical_datetime(utc_now())

        def write(conn: sqlite3.Connection) -> None:
            if not self._available(conn) or not has_column(conn, "audit_log", "hash_alg"):
                raise RuntimeError("the kill switch needs schema v4 or later; run `ceap db migrate`")
            conn.execute(
                "INSERT INTO kill_switch_events(at, actor, action, reason) VALUES (?,?,?,?)",
                (at, actor, action, reason),
            )
            # same transaction: the audit row and the state change land together or not at all
            self.db.append_audit(actor, f"kill_switch.{action}", AUDIT_SUBJECT, {"reason": reason})

        self.db.run(write)
        return self.state()

    def history(self) -> list[dict[str, str]]:
        return self.db.query("SELECT seq, at, actor, action, reason FROM kill_switch_events ORDER BY seq")
