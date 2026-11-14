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

Since 0.10.0 (H1, findings R-11 and AI-2-05) the journal also holds each model reply (keyed by agent,
step and call order, :class:`StepModelJournal`) and the run context a resume needs (principal, roles,
attributes). While a journal is attached, every scope of the run mints ids from a seed of the task id
and the scope (:func:`id_scope`), so a resumed run reproduces the finding and evidence ids that the
journalled replies refer to. An approval is matched by :func:`stable_content_hash`, which leaves out
those run-minted ids. ``Platform.resume`` (``POST /tasks/{id}/resume``, ``ceap resume <id>``) is the
entry point; ``tests/unit/test_r0100_h1_resume.py`` kills a run and resumes it through the API.

Worked example. Run ``T`` executes ``s1 -> s2 -> stage`` and is killed during ``stage``. ``s1`` and
``s2`` are in the journal. Re-running ``T`` with the same journal reads ``s1`` and ``s2`` back
(zero tool calls), then runs ``stage`` once. Running ``T`` a third time makes zero tool calls and
produces the same report (``tests/unit/test_r090_durable.py``).
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as _dt
import decimal
import enum
import hashlib
import itertools
import json
import logging
import pathlib
from collections.abc import Iterator
from dataclasses import fields
from datetime import datetime
from typing import Any, Protocol

from ceap.domain.common import ID_SOURCE, to_jsonable
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.tools import ToolRequest, ToolResult, ToolStatus
from ceap.llm.models import Degradation, LLMRequest, LLMResponse

log = logging.getLogger("ceap.harness.durable")

TOOL_KIND = "tool"
APPROVAL_KIND = "approval"


def _canonical(obj: Any) -> Any:
    """JSON form of a non-JSON argument that is the same in every process.

    ``default=str`` was stable only for JSON-native arguments: a set prints in hash order and an
    arbitrary object prints its memory address, so a resumed run computed a different key.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {"__dataclass__": type(obj).__qualname__, **dataclasses.asdict(obj)}
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=lambda v: json.dumps(v, sort_keys=True, default=_canonical))
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    if isinstance(obj, (_dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, (decimal.Decimal, enum.Enum, pathlib.PurePath)):
        return str(obj.value if isinstance(obj, enum.Enum) else obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "tolist"):
        return obj.tolist()
    raise TypeError(f"idempotency_key: argument of type {type(obj).__name__} has no stable JSON form")


def idempotency_key(task_id: str, step_id: str, request: ToolRequest) -> str:
    """Stable key of one tool call within one run (see the module docstring)."""
    args = json.dumps(request.arguments, sort_keys=True, default=_canonical, separators=(",", ":"))
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


# ------------------------------------------------------------------ 0.10.0 H1 (R-11, AI-2-05)
#: journal kinds added in 0.10.0: one model reply, and the run context a resume needs
MODEL_KIND = "model"
CONTEXT_KIND = "context"
CONTEXT_KEY = "run-context"
CONTEXT_STEP = "-"

#: keys whose values each run mints afresh (finding, evidence, request and case ids). Two approvals
#: that differ only in these ask the same question, so the stable hash leaves them out.
VOLATILE_KEYS = frozenset(
    {"id", "request_id", "finding_id", "finding_ids", "evidence_id", "evidence_ids", "case_id", "execution_id"}
)


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _strip_volatile(v) for k, v in value.items() if str(k) not in VOLATILE_KEYS}
    if isinstance(value, (list, tuple)):
        return [_strip_volatile(v) for v in value]
    return value


def stable_content_hash(tool_id: str, arguments: dict[str, Any]) -> str:
    """Hash of an approval's content with run-minted ids removed (R-11).

    ``ApprovalRequest.content_hash`` still binds the exact bytes the approver saw (A14). This hash only
    decides whether a resumed run is asking the same question again, so a fresh finding id in the
    review block does not turn an approval that was already decided into a new request.
    """
    doc = {"tool_id": tool_id, "arguments": _strip_volatile(to_jsonable(arguments))}
    text = json.dumps(doc, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def model_key(agent_id: str, step_id: str, ordinal: int) -> str:
    """Key of the ``ordinal``-th model call an agent makes in one step of one run.

    Keyed by order, not prompt text: a prompt can carry per-attempt values, while the sequence of
    calls an agent makes for the same inputs is fixed.
    """
    return hashlib.sha256(f"model|{agent_id}|{step_id}|{ordinal}".encode()).hexdigest()


def response_payload(response: LLMResponse) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(json.dumps(to_jsonable(response), default=str))
    return doc


def response_from_payload(doc: dict[str, Any]) -> LLMResponse:
    names = {f.name for f in fields(LLMResponse)}
    kwargs = {k: v for k, v in doc.items() if k in names}
    kwargs["degradations"] = tuple(Degradation(**d) for d in kwargs.get("degradations") or ())
    for name in ("parse_errors", "citations"):
        kwargs[name] = tuple(kwargs.get(name) or ())
    return LLMResponse(**kwargs)


class StepModelJournal:
    """Model replies of one agent step, journalled and read back on resume (AI-2-05).

    ``counts`` is shared by every step of one run (it lives on the run's memory), so a step that runs
    twice - the forced critic, for example - keeps numbering its calls instead of starting again.
    """

    def __init__(
        self, journal: RunJournal, task_id: str, agent_id: str, step_id: str, counts: dict[str, int]
    ) -> None:
        self.journal, self.task_id, self.agent_id, self.step_id = journal, task_id, agent_id, step_id
        self._counts = counts
        self._scope = f"{agent_id}|{step_id}"
        self.replayed = 0

    def _key(self) -> str:
        return model_key(self.agent_id, self.step_id, self._counts.get(self._scope, 0))

    def lookup(self, request: LLMRequest) -> LLMResponse | None:
        """The journalled reply for this call, or ``None`` when the model has to be asked."""
        row = self.journal.get(self.task_id, self._key())
        if row is None or row["kind"] != MODEL_KIND:
            return None
        if row["payload"].get("purpose") != request.purpose:
            # the run diverged (the code changed between attempts): ask the model, do not answer wrongly
            log.warning(
                "journalled model reply for %s/%s has purpose %r, the request has %r; calling the model",
                self.agent_id,
                self.step_id,
                row["payload"].get("purpose"),
                request.purpose,
            )
            return None
        self._counts[self._scope] = self._counts.get(self._scope, 0) + 1
        self.replayed += 1
        return response_from_payload(row["payload"]["response"])

    def record(self, request: LLMRequest, response: LLMResponse) -> None:
        self.journal.put(
            self.task_id,
            self._key(),
            MODEL_KIND,
            self.step_id,
            {"purpose": request.purpose, "agent_id": self.agent_id, "response": response_payload(response)},
        )
        self._counts[self._scope] = self._counts.get(self._scope, 0) + 1


class _ScopedIds:
    """An id source derived from a seed: the same seed gives the same sequence of ids."""

    def __init__(self, seed: str) -> None:
        self.seed = seed
        self._n = itertools.count()

    def __call__(self) -> str:
        return hashlib.sha256(f"{self.seed}|{next(self._n)}".encode()).hexdigest()[:8]


@contextlib.contextmanager
def id_scope(seed: str | None) -> Iterator[None]:
    """Mint ids from ``seed`` inside the block (AI-2-05).

    With a journal, each scope of a run (the run, each agent step, each tool call) mints its ids from a
    seed made of the task id and the scope, so a resumed run reproduces the finding and evidence ids
    that journalled model replies refer to. A no-op for ``None``, and while a replay
    (``ceap.harness.replay``) owns the id source.
    """
    current = ID_SOURCE.get()
    if seed is None or (current is not None and not isinstance(current, _ScopedIds)):
        yield
        return
    token = ID_SOURCE.set(_ScopedIds(seed))
    try:
        yield
    finally:
        ID_SOURCE.reset(token)


def scope_seed(task_id: str, *parts: str) -> str:
    return "|".join(("ceap-durable", task_id, *parts))
