"""Investigation memory: everything gathered during one harness execution."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from ceap.domain.common import utc_now
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.findings import EvidenceRef, Finding
from ceap.domain.tools import ToolRequest, ToolResult


@dataclass(frozen=True)
class ToolOutput:
    step_id: str
    tool_id: str
    arguments: dict[str, Any]
    data: Any
    evidence_ids: tuple[str, ...]
    execution_time_ms: float

    def matches(self, **filters: Any) -> bool:
        """True when every filter key matches this output's arguments (``None`` filters match absence)."""
        return all(self.arguments.get(k) == v for k, v in filters.items())

    def matches_exactly(self, arguments: dict[str, Any]) -> bool:
        """True when the argument dicts are equal after dropping ``None`` values on both sides."""
        mine = {k: v for k, v in self.arguments.items() if v is not None}
        theirs = {k: v for k, v in arguments.items() if v is not None}
        return mine == theirs


@dataclass
class InvestigationMemory:
    evidence: dict[str, Evidence] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    tool_outputs: list[ToolOutput] = field(default_factory=list)
    agent_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    scratch: dict[str, Any] = field(default_factory=dict)
    messages: list[Any] = field(default_factory=list)  # R3B 3B.6: typed AgentMessage envelopes
    # H1: per-run counters of journalled scopes (model calls per agent step, agent step occurrences)
    durable_counts: dict[str, int] = field(default_factory=dict)

    def add_evidence(self, *items: Evidence) -> None:
        for e in items:
            self.evidence[e.id] = e

    def add_findings(self, *items: Finding) -> None:
        self.findings.extend(items)

    def replace_findings(self, items: list[Finding]) -> None:
        self.findings = list(items)

    def record_tool(self, step_id: str, request: ToolRequest, result: ToolResult) -> ToolOutput | None:
        if not result.ok:
            return None
        self.add_evidence(*result.evidence)
        out = ToolOutput(
            step_id,
            request.tool_id,
            dict(request.arguments),
            result.data,
            tuple(e.id for e in result.evidence),
            result.execution_time_ms,
        )
        self.tool_outputs.append(out)
        return out

    def find(self, tool_id: str, **filters: Any) -> ToolOutput | None:
        for out in reversed(self.tool_outputs):
            if out.tool_id == tool_id and out.matches(**filters):
                return out
        return None

    def find_exact(self, tool_id: str, arguments: dict[str, Any]) -> ToolOutput | None:
        for out in reversed(self.tool_outputs):
            if out.tool_id == tool_id and out.matches_exactly(arguments):
                return out
        return None

    def find_all(self, tool_id: str, **filters: Any) -> list[ToolOutput]:
        return [o for o in self.tool_outputs if o.tool_id == tool_id and o.matches(**filters)]

    def evidence_list(self) -> tuple[Evidence, ...]:
        return tuple(self.evidence.values())

    def unresolved_evidence(self, finding: Finding) -> list[str]:
        return [
            e
            for e in (*finding.supporting_evidence, *finding.contradicting_evidence)
            if e not in self.evidence
        ]


# ------------------------------------------------------------------ evidence binding (R3B 3B.4)

_MISSING = object()
#: tolerance when a cited value is compared with the value at its path
REF_TOLERANCE = 1e-9


def value_at(evidence: Evidence, path: str) -> Any:
    """The value at a dotted ``path`` inside ``evidence.attributes`` (``_MISSING`` if absent)."""
    node: Any = evidence.attributes
    for part in path.split(".") if path else ():
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, (list, tuple)) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return _MISSING
    return node


def same_value(a: Any, b: Any) -> bool:
    """Equality for cited values: numbers within a tolerance (NaN equals NaN), else ``==``."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        fa, fb = float(a), float(b)
        if math.isnan(fa) or math.isnan(fb):
            return math.isnan(fa) and math.isnan(fb)
        return math.isclose(fa, fb, rel_tol=REF_TOLERANCE, abs_tol=REF_TOLERANCE)
    return bool(a == b)


_SKIP_KEYS = frozenset({"arguments", "derived_from", "task_id", "step_id", "correlation_id", "digest", "tool_id"})
MAX_BIND_DEPTH = 3


def _paths(node: Any, key: str, value: Any, prefix: str = "", depth: int = 0) -> list[str]:
    """Dotted paths under ``node`` whose last segment is ``key`` and whose value equals ``value``."""
    found: list[str] = []
    if depth > MAX_BIND_DEPTH:
        return found
    if isinstance(node, dict):
        for k, v in node.items():
            if depth == 0 and k in _SKIP_KEYS:
                continue
            path = f"{prefix}{k}"
            if k == key and isinstance(v, (int, float, str)) and same_value(v, value):
                found.append(path)
            elif isinstance(v, (dict, list)):
                found.extend(_paths(v, key, value, path + ".", depth + 1))
    elif isinstance(node, list):
        for i, v in enumerate(node[:50]):
            if isinstance(v, (dict, list)):
                found.extend(_paths(v, key, value, f"{prefix}{i}.", depth + 1))
    return found


def bind_evidence_refs(finding: Finding, evidence: dict[str, Evidence]) -> Finding:
    """Cite the attribute the agent used: every scalar finding attribute whose name and value appear
    in one of its supporting evidence records (an agent CALCULATION, or a tool record's ``values``)
    becomes an ``EvidenceRef``. CALCULATION records are searched first; explicit refs are kept."""
    if finding.evidence_refs:
        return finding
    records = [evidence[e] for e in finding.supporting_evidence if e in evidence]
    records.sort(key=lambda ev: ev.type is not EvidenceType.CALCULATION)
    refs: list[EvidenceRef] = []
    for key, value in finding.attributes.items():
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        for ev in records:
            paths = _paths(ev.attributes, key, value)
            if paths:
                refs.append(EvidenceRef(id=ev.id, path=paths[0], value=value))
                break
    return replace(finding, evidence_refs=tuple(refs)) if refs else finding


def evidence_problems(finding: Finding, evidence: dict[str, Evidence]) -> list[str]:
    """Why a finding's evidence binding does not hold (empty: it holds).

    * every cited ref resolves and the value at its path equals the cited value;
    * every CALCULATION record it relies on that declares ``derived_from`` derives only from
      evidence recorded in this run.
    """
    problems: list[str] = []
    for ref in finding.evidence_refs:
        ev = evidence.get(ref.id)
        if ev is None:
            problems.append(f"evidence_ref {ref.id} does not exist")
            continue
        actual = value_at(ev, ref.path)
        if actual is _MISSING:
            problems.append(f"evidence_ref {ref.id}:{ref.path} has no value at that path")
        elif not same_value(actual, ref.value):
            problems.append(
                f"evidence_ref {ref.id}:{ref.path} cites {ref.value!r} but the evidence holds {actual!r}"
            )
    for eid in dict.fromkeys((*finding.supporting_evidence, *(r.id for r in finding.evidence_refs))):
        ev = evidence.get(eid)
        if ev is None or ev.type is not EvidenceType.CALCULATION:
            continue
        derived = ev.attributes.get("derived_from")
        if derived is None:
            continue
        broken = (
            [d for d in derived if d not in evidence]
            if isinstance(derived, (list, tuple))
            else ["(malformed)"]
        )
        if broken:
            problems.append(f"calculation {eid} derives from unknown evidence {broken}")
    return problems


# ------------------------------------------------------------------ case memory (R3B 3B.7, D10)


@dataclass(frozen=True)
class CaseRecord:
    """A compact record of one human-decided case. Written by the harness only, never by a model."""

    case_id: str
    task_id: str
    dataset: str
    kind: str
    symbol: str | None
    question: str
    outcome: str  # APPROVED | REJECTED
    decided_by: str
    reason: str
    hypotheses: tuple[tuple[str, str], ...]  # (name, verdict) of the SUPPORTED / REFUTED rows
    recorded_at: str
    #: H6 (AI-2-07): the principal whose task this was; only that principal reads it back
    owner: str = ""

    def text(self) -> str:
        causes = " ".join(f"{n} {v}" for n, v in self.hypotheses)
        return f"{self.kind} {self.symbol or ''} {self.question} {causes} {self.outcome} {self.reason}"

    def view(self) -> dict[str, Any]:
        """The read-only form the planner sees."""
        return {
            "case_id": self.case_id,
            "kind": self.kind,
            "symbol": self.symbol,
            "question": self.question[:300],
            "outcome": self.outcome,
            "reason": self.reason[:300],
            "hypotheses": [list(h) for h in self.hypotheses],
        }


AuditHook = Callable[[str, str, str, dict[str, Any]], Any]  # (actor, action, subject, payload)


#: decision makers that are not people (``ceap.policy.approvals.SYSTEM_ACTORS``); repeated here so
#: memory does not import the policy package. ``tests/agent/test_r0100_h6_case_memory.py`` keeps
#: the two in step.
NON_HUMAN_DECIDERS = frozenset({"timeout", "policy", "auto-approver"})
#: H6 (AI-2-07): a case older than this is not read back and is pruned on the next write
DEFAULT_CASE_TTL_DAYS = 90


class CaseMemory:
    """Bounded case memory of human decisions (decision D10; 0.10.0 H6).

    * written only by the harness, after a human decision; a decision by the system (auto-approval,
      timeout, policy) is refused (AI-2-06); there is no memory tool;
    * no update or delete by any caller; ``max_cases`` bounds each partition (new cases are refused
      and audited once it is full); expired cases are pruned on write (``ttl_days``, AI-2-07);
    * partitioned by dataset and owner: a lookup never crosses datasets, and returns another
      principal's case only to a reader with ``tasks:read-any`` (AI-2-07);
    * read-only for the planner: :meth:`nearest` returns views, never the store;
    * OFF in evaluation (``enabled=False``): no reads and no writes, and ``reads`` proves it;
    * every write is audited through ``audit``.

    Retrieval is BM25 over the case text through the R3C public API (``KnowledgeBase.search``).
    The store is per process; a database table is a follow-up (the audit chain already persists
    every write when the platform passes the database's audit hook).
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_cases: int = 1000,
        audit: AuditHook | None = None,
        ttl_days: float = DEFAULT_CASE_TTL_DAYS,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.enabled = enabled
        self.max_cases = max_cases
        self.ttl = timedelta(days=ttl_days)
        self._now = now
        self._audit = audit
        self._cases: dict[tuple[str, str], list[CaseRecord]] = {}
        self.reads = 0
        self.writes = 0

    def _fresh(self, record: CaseRecord) -> bool:
        try:
            at = datetime.fromisoformat(record.recorded_at)
        except (TypeError, ValueError):
            return False  # an undated case cannot be shown to be in date
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        return self._now() - at <= self.ttl

    def append(self, record: CaseRecord) -> bool:
        if not self.enabled or record.decided_by in NON_HUMAN_DECIDERS:
            return False
        key = (record.dataset, record.owner)
        partition = [c for c in self._cases.get(key, []) if self._fresh(c)]  # expiry: prune on write
        self._cases[key] = partition
        if any(c.case_id == record.case_id for c in partition):
            return False  # the same decision again (a resumed run): one case, not two
        if len(partition) >= self.max_cases:
            if self._audit is not None:
                self._audit("harness", "case_memory.full", record.case_id, {"dataset": record.dataset})
            return False
        partition.append(record)
        self.writes += 1
        if self._audit is not None:
            self._audit(
                "harness",
                "case_memory.append",
                record.case_id,
                {"dataset": record.dataset, "owner": record.owner, "decided_by": record.decided_by, **record.view()},
            )
        return True

    def __len__(self) -> int:
        return sum(len(p) for p in self._cases.values())

    def nearest(
        self, dataset: str, query: str, k: int = 3, *, owner: str | None = None, read_any: bool = False
    ) -> tuple[dict[str, Any], ...]:
        """The ``k`` nearest in-date cases in ``dataset`` (read-only views); empty when disabled.

        Only ``owner``'s cases are candidates unless ``read_any`` (the reader holds ``tasks:read-any``).
        ``owner=None`` matches only cases recorded without an owner.
        """
        if not self.enabled:
            return ()
        self.reads += 1
        partition = [
            c
            for (ds, who), cases in self._cases.items()
            if ds == dataset and (read_any or who == (owner or ""))
            for c in cases
            if self._fresh(c)
        ]
        if not partition:
            return ()
        from ceap.rag.ingestion import Document
        from ceap.rag.retrieval import KnowledgeBase

        docs = [
            Document(id=c.case_id, title=c.case_id, path=f"case/{c.case_id}", text=c.text())
            for c in partition
        ]
        hits = KnowledgeBase(docs, min_support=0.0).search(query, k=k * 4, min_support=0.0)
        by_id = {c.case_id: c for c in partition}
        out: list[dict[str, Any]] = []
        for hit in hits:
            case = by_id.get(hit.chunk.document_id)
            if case is not None and all(v["case_id"] != case.case_id for v in out):
                out.append(case.view())
            if len(out) >= k:
                break
        return tuple(out)
