"""Repositories over :class:`~ceap.persistence.db.Database`: one small class per table.

Every write goes through ``Database.run``/``arun`` and therefore through the single writer thread.
Rows are JSON documents (``*_json`` columns) stamped with ``schema_version`` so a later release can
migrate them forward.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ceap.domain.common import SCHEMA_VERSION, to_jsonable, utc_now
from ceap.persistence.db import Database
from ceap.util.canonical import canonical_datetime, content_hash

#: task statuses that mean "was in flight"; after a restart they become INTERRUPTED (R4 item 4.2)
IN_FLIGHT = ("PENDING", "RUNNING")
IDEMPOTENCY_TTL = timedelta(hours=24)


def _now() -> str:
    return canonical_datetime(utc_now())


def _dumps(obj: Any) -> str:
    doc = to_jsonable(obj)
    if isinstance(doc, dict):
        doc = {"schema_version": SCHEMA_VERSION, **doc}
    return json.dumps(doc, sort_keys=True, default=str, allow_nan=False)


class TaskRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(
        self, task_id: str, kind: str, owner: str, description: str, task_input: dict[str, Any], status: str
    ) -> None:
        now = _now()
        self.db.run(
            lambda c: c.execute(
                "INSERT INTO tasks(id, kind, owner, description, input_json, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
                "owner=excluded.owner, updated_at=excluded.updated_at",
                (task_id, kind, owner, description, _dumps(task_input), status, now, now),
            )
        )

    def set_status(self, task_id: str, status: str) -> None:
        self.db.run(
            lambda c: c.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, _now(), task_id)
            )
        )

    def get(self, task_id: str) -> dict[str, Any] | None:
        rows = self.db.query("SELECT * FROM tasks WHERE id=?", (task_id,))
        return rows[0] if rows else None

    def find(self, owner: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM tasks WHERE 1=1", list[str]()
        if owner is not None:
            sql, params = sql + " AND owner=?", [*params, owner]
        if kind is not None:
            sql, params = sql + " AND kind=?", [*params, kind]
        return self.db.query(sql + " ORDER BY created_at, id", tuple(params))

    def interrupt_in_flight(self) -> list[str]:
        """Mark every task left PENDING/RUNNING by a previous process as INTERRUPTED; return their ids."""

        def interrupt(conn: sqlite3.Connection) -> list[str]:
            marks = ",".join("?" * len(IN_FLIGHT))
            ids = [r[0] for r in conn.execute(f"SELECT id FROM tasks WHERE status IN ({marks})", IN_FLIGHT)]
            conn.execute(
                f"UPDATE tasks SET status='INTERRUPTED', updated_at=? WHERE status IN ({marks})",
                (_now(), *IN_FLIGHT),
            )
            return ids

        return self.db.run(interrupt)


class ResultRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save(
        self, task_id: str, status: str, result: dict[str, Any], trace: dict[str, Any] | None = None
    ) -> None:
        now = _now()

        def save(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT OR REPLACE INTO results(task_id, status, result_json, finished_at, trace_json) "
                "VALUES (?,?,?,?,?)",
                (task_id, status, _dumps(result), now, _dumps(trace) if trace is not None else None),
            )
            conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, now, task_id))

        self.db.run(save)

    def get(self, task_id: str) -> dict[str, Any] | None:
        rows = self.db.query("SELECT result_json FROM results WHERE task_id=?", (task_id,))
        return json.loads(rows[0]["result_json"]) if rows else None


class ApprovalStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class SqlApprovalStore:
    """Approval rows; the decision is a conditional ``UPDATE ... WHERE status='PENDING'`` (one winner)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def insert(self, request: Any) -> None:
        self.db.run(
            lambda c: c.execute(
                "INSERT INTO approvals(id, task_id, step_id, tool_id, request_json, content_hash, requested_by, "
                "requested_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    request.id,
                    request.task_id,
                    request.step_id,
                    request.tool_id,
                    _dumps(request),
                    request.content_hash,
                    request.requested_by,
                    canonical_datetime(request.requested_at),
                    ApprovalStatus.PENDING,
                ),
            )
        )

    def transition(self, approval_id: str, status: str, decided_by: str, comment: str, at: datetime) -> bool:
        """PENDING -> ``status``; True only for the single caller whose update matched a PENDING row."""
        cur = self.db.run(
            lambda c: (
                c.execute(
                    "UPDATE approvals SET status=?, decided_by=?, decided_at=?, comment=? WHERE id=? AND status=?",
                    (
                        status,
                        decided_by,
                        canonical_datetime(at),
                        comment,
                        approval_id,
                        ApprovalStatus.PENDING,
                    ),
                ).rowcount
            )
        )
        return cur == 1

    def status(self, approval_id: str) -> str | None:
        rows = self.db.query("SELECT status FROM approvals WHERE id=?", (approval_id,))
        return rows[0]["status"] if rows else None

    def get(self, approval_id: str) -> dict[str, Any] | None:
        rows = self.db.query("SELECT * FROM approvals WHERE id=?", (approval_id,))
        return rows[0] if rows else None

    def expire_pending(self, reason: str) -> list[str]:
        def expire(conn: sqlite3.Connection) -> list[str]:
            ids = [r[0] for r in conn.execute("SELECT id FROM approvals WHERE status='PENDING'")]
            conn.execute(
                "UPDATE approvals SET status='EXPIRED', decided_by='timeout', decided_at=?, comment=? "
                "WHERE status='PENDING'",
                (_now(), reason),
            )
            return ids

        return self.db.run(expire)


class IdempotencyRepository:
    """``Idempotency-Key`` records, scoped per principal, kept for 24 hours (R4 item 4.4)."""

    def __init__(self, db: Database, ttl: timedelta = IDEMPOTENCY_TTL) -> None:
        self.db = db
        self.ttl = ttl

    @staticmethod
    def body_hash(body: dict[str, Any]) -> str:
        return content_hash(json.loads(json.dumps(body, default=str)))

    def get(self, principal: str, key: str) -> dict[str, Any] | None:
        cutoff = canonical_datetime(utc_now() - self.ttl)

        def get(conn: sqlite3.Connection) -> dict[str, Any] | None:
            conn.execute("DELETE FROM idempotency_keys WHERE created_at < ?", (cutoff,))
            row = conn.execute(
                "SELECT * FROM idempotency_keys WHERE principal=? AND key=?", (principal, key)
            ).fetchone()
            return dict(row) if row else None

        return self.db.run(get)

    def put(self, principal: str, key: str, body_hash: str, task_id: str, response: dict[str, Any]) -> None:
        self.db.run(
            lambda c: c.execute(
                "INSERT OR IGNORE INTO idempotency_keys(principal, key, body_hash, task_id, response_json, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (principal, key, body_hash, task_id, json.dumps(to_jsonable(response), default=str), _now()),
            )
        )


class StagedOrderRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, staging_id: str, payload: dict[str, Any]) -> bool:
        """Insert once; returns False when the staging id already exists (idempotent staging)."""
        return (
            self.db.run(
                lambda c: (
                    c.execute(
                        "INSERT OR IGNORE INTO staged_orders(staging_id, payload_json, created_at) VALUES (?,?,?)",
                        (staging_id, _dumps(payload), _now()),
                    ).rowcount
                )
            )
            == 1
        )

    def get(self, staging_id: str) -> dict[str, Any] | None:
        rows = self.db.query("SELECT payload_json FROM staged_orders WHERE staging_id=?", (staging_id,))
        return json.loads(rows[0]["payload_json"]) if rows else None


class LlmCallRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        task_id: str | None,
        purpose: str,
        model: str,
        prompt_hash: str,
        request: dict[str, Any] | None,
        response: dict[str, Any] | None,
    ) -> None:
        self.db.run(
            lambda c: c.execute(
                "INSERT INTO llm_calls(task_id, purpose, model, prompt_hash, request_json, response_json, at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    task_id,
                    purpose,
                    model,
                    prompt_hash,
                    json.dumps(request, default=str) if request is not None else None,
                    json.dumps(response, default=str) if response is not None else None,
                    _now(),
                ),
            )
        )


class TrialLedgerRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self, dataset_hash: str, params_hash: str, sharpe: float | None, record: dict[str, Any]
    ) -> bool:
        """Append one trial; a repeat of the same (dataset, params) is not a new trial."""
        return (
            self.db.run(
                lambda c: (
                    c.execute(
                        "INSERT OR IGNORE INTO trial_ledger(dataset_hash, params_hash, sharpe, record_json, at) "
                        "VALUES (?,?,?,?,?)",
                        (dataset_hash, params_hash, sharpe, json.dumps(record, default=str), _now()),
                    ).rowcount
                )
            )
            == 1
        )

    def count(self, dataset_hash: str) -> int:
        return int(
            self.db.query("SELECT COUNT(*) AS n FROM trial_ledger WHERE dataset_hash=?", (dataset_hash,))[0][
                "n"
            ]
        )


@dataclass(frozen=True)
class RetentionPolicy:
    results_days: float | None = None
    traces_days: float | None = None
    llm_io_days: float | None = None
    max_bytes: int | None = None


def apply_retention(db: Database, policy: RetentionPolicy) -> dict[str, int]:
    """Prune by age, then oldest finished results first until under ``max_bytes``. The audit log is never pruned."""
    now = utc_now()
    out = {"results": 0, "traces": 0, "llm_io": 0, "size_pruned": 0}

    def cutoff(days: float) -> str:
        return canonical_datetime(now - timedelta(days=days))

    def prune(conn: sqlite3.Connection) -> None:
        if policy.results_days is not None:
            out["results"] = conn.execute(
                "DELETE FROM results WHERE finished_at < ?", (cutoff(policy.results_days),)
            ).rowcount
            conn.execute(  # finished tasks go with their results; in-flight rows are never pruned
                "DELETE FROM tasks WHERE updated_at < ? AND status NOT IN ('PENDING','RUNNING') "
                "AND id NOT IN (SELECT task_id FROM results)",
                (cutoff(policy.results_days),),
            )
        if policy.traces_days is not None:
            out["traces"] = conn.execute(
                "UPDATE results SET trace_json=NULL WHERE trace_json IS NOT NULL AND finished_at < ?",
                (cutoff(policy.traces_days),),
            ).rowcount
        if policy.llm_io_days is not None:
            out["llm_io"] = conn.execute(
                "UPDATE llm_calls SET request_json=NULL, response_json=NULL "
                "WHERE request_json IS NOT NULL AND at < ?",
                (cutoff(policy.llm_io_days),),
            ).rowcount

    db.run(prune)
    if policy.max_bytes is not None:
        while db.size_bytes() > policy.max_bytes:
            deleted = db.run(
                lambda c: (
                    c.execute(
                        "DELETE FROM results WHERE task_id IN "
                        "(SELECT task_id FROM results ORDER BY finished_at LIMIT 20)"
                    ).rowcount
                )
            )
            if not deleted:
                break
            out["size_pruned"] += deleted
            db.run(lambda c: c.execute("VACUUM"), write=False)
    return out
