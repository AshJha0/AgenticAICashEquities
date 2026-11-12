"""Durable runs (0.9.0, G5): a step-boundary journal, resume after a crash, idempotent tool calls.

Plain idea. A run that dies half-way (``kill -9``, power cut, OOM) should not repeat work that already
happened when it is started again, and it must never repeat a side effect such as staging orders.
So every completed tool call and every human decision is written to a journal *at the step boundary*,
before the run moves on, and a resumed run consults the journal before doing anything.

Formal statement. Each :class:`~ceap.domain.tools.ToolRequest` gets an idempotency key

    key = sha256(task_id | step_id | tool_id | canonical_json(arguments))

(:func:`idempotency_key`). ``correlation_id`` is deliberately left out: it is fresh on each attempt,
so including it would make every retry look new. When the harness runs with a :class:`RunJournal`:

* a tool call whose key is already journalled with an OK result returns that result without calling
  the tool again (and is marked ``replayed`` in the trace);
* an OK result is journalled immediately after the tool returns, before the next batch starts;
* a human approval whose ``(task_id, step_id, content_hash)`` was already decided is reused, so a
  restart does not ask the approver twice for the same content (resumable approvals). A different
  content hash is a different question and is asked again.

Failed tool calls are not journalled: a resumed run retries them, which is the point of resuming.

Worked example. Run ``T`` executes ``s1 -> s2 -> stage`` and is killed during ``stage``. ``s1`` and
``s2`` are in the journal. Re-running ``T`` with the same journal reads ``s1`` and ``s2`` back
(zero tool calls), then runs ``stage`` once. Running ``T`` a third time makes zero tool calls and
produces the same report (``tests/unit/test_r090_durable.py``).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Protocol

from ceap.domain.common import to_jsonable
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.tools import ToolRequest, ToolResult, ToolStatus

TOOL_KIND = "tool"
APPROVAL_KIND = "approval"


def idempotency_key(task_id: str, step_id: str, request: ToolRequest) -> str:
    """Stable key of one tool call within one run (see the module docstring)."""
    args = json.dumps(request.arguments, sort_keys=True, default=str, separators=(",", ":"))
    raw = "|".join((task_id, step_id, request.tool_id, args))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def approval_key(step_id: str, content_hash: str) -> str:
    return hashlib.sha256(f"approval|{step_id}|{content_hash}".encode()).hexdigest()


class RunJournal(Protocol):
    """Storage of journal entries; :class:`InMemoryRunJournal` or :class:`SqlRunJournal`."""

    def get(self, task_id: str, key: str) -> dict[str, Any] | None: ...

    def put(self, task_id: str, key: str, kind: str, step_id: str, payload: dict[str, Any]) -> bool: ...


class InMemoryRunJournal:
    """A journal that lives as long as the object (tests, single-process replays)."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}

    def get(self, task_id: str, key: str) -> dict[str, Any] | None:
        return self._rows.get((task_id, key))

    def put(self, task_id: str, key: str, kind: str, step_id: str, payload: dict[str, Any]) -> bool:
        if (task_id, key) in self._rows:
            return False
        self._rows[(task_id, key)] = {"kind": kind, "step_id": step_id, "payload": payload}
        return True

    def __len__(self) -> int:
        return len(self._rows)


class SqlRunJournal:
    """The SQLite journal (table ``run_journal``, migration 0005); survives the process."""

    def __init__(self, db: Any) -> None:
        from ceap.persistence.repositories import RunJournalRepository

        self.repo = RunJournalRepository(db)

    def get(self, task_id: str, key: str) -> dict[str, Any] | None:
        row = self.repo.get(task_id, key)
        return (
            None
            if row is None
            else {"kind": row["kind"], "step_id": row["step_id"], "payload": row["payload"]}
        )

    def put(self, task_id: str, key: str, kind: str, step_id: str, payload: dict[str, Any]) -> bool:
        return self.repo.put(task_id, key, kind, step_id, payload)


def result_payload(result: ToolResult) -> dict[str, Any]:
    """JSON form of an OK tool result, evidence included (ids kept, so citations still resolve)."""
    return {
        "status": result.status.value,
        "data": to_jsonable(result.data),
        "execution_time_ms": result.execution_time_ms,
        "evidence": [to_jsonable(e) for e in result.evidence],
    }


def _evidence(doc: dict[str, Any]) -> Evidence:
    ts = doc.get("timestamp")
    return Evidence(
        id=doc["id"],
        type=EvidenceType(doc["type"]),
        source=doc["source"],
        description=doc["description"],
        timestamp=datetime.fromisoformat(ts) if isinstance(ts, str) else ts,
        attributes=dict(doc.get("attributes") or {}),
    )


def result_from_payload(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(
        status=ToolStatus(payload["status"]),
        data=payload.get("data"),
        evidence=tuple(_evidence(e) for e in payload.get("evidence") or ()),
        execution_time_ms=float(payload.get("execution_time_ms") or 0.0),
    )
