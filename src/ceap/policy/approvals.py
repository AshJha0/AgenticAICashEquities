"""Human-in-the-loop approvals."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import threading
from abc import ABC, abstractmethod
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ceap.domain.common import new_id, utc_now
from ceap.util.canonical import content_hash


def request_content_hash(tool_id: str, arguments: Mapping[str, Any]) -> str:
    """The content hash an approval binds to: the tool id and its exact arguments."""
    payload = {"tool_id": tool_id, "arguments": dict(arguments)}
    try:
        return content_hash(payload)
    except (TypeError, ValueError):  # non-canonical argument types: hash a stable text form
        return content_hash(json.dumps(payload, sort_keys=True, default=str))


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    task_id: str
    step_id: str
    tool_id: str
    arguments: dict[str, Any]
    reason: str
    requested_by: str
    requested_at: datetime = field(default_factory=utc_now)
    content_hash: str = ""

    def __post_init__(self) -> None:
        # The approver signs off on exactly these bytes; any later change to the payload is detectable.
        if not self.content_hash:
            object.__setattr__(self, "content_hash", self.current_hash())

    def current_hash(self) -> str:
        """The hash of ``tool_id`` and ``arguments`` as they are now (A14 re-check)."""
        return request_content_hash(self.tool_id, self.arguments)

    def intact(self) -> bool:
        """True while the arguments still hash to the value the approver saw."""
        return self.current_hash() == self.content_hash

    @staticmethod
    def create(
        task_id: str, step_id: str, tool_id: str, arguments: dict[str, Any], reason: str, requested_by: str
    ) -> ApprovalRequest:
        return ApprovalRequest(
            new_id("APR"), task_id, step_id, tool_id, dict(arguments), reason, requested_by
        )


@dataclass(frozen=True)
class ApprovalDecision:
    request_id: str
    approved: bool
    decided_by: str
    comment: str = ""
    decided_at: datetime = field(default_factory=utc_now)
    action: str = ""  # R3B 3B.8: approve | reject | modify ("" = derived from ``approved``)


#: non-human deciders; exempt from the four-eyes check and always recorded in the audit trail
SYSTEM_ACTORS = frozenset({"timeout", "policy", "auto-approver"})
#: key in ``ApprovalRequest.arguments`` naming principals who may not decide this request
DISTINCT_FROM_KEY = "four_eyes_distinct_from"


class FourEyesViolationError(PermissionError):
    """The decider requested the action, or already approved an earlier stage of it."""


def check_four_eyes(request: ApprovalRequest, decided_by: str) -> None:
    """Four-eyes rule (PLAN 1.3, threat T17): nobody approves their own request, and the staging
    approval comes from a different principal than the proposal approval."""
    if decided_by in SYSTEM_ACTORS:
        return
    if decided_by == request.requested_by:
        raise FourEyesViolationError(f"{decided_by} requested {request.id} and cannot also decide it")
    excluded = request.arguments.get(DISTINCT_FROM_KEY) or ()
    if decided_by in excluded:
        raise FourEyesViolationError(
            f"{decided_by} approved an earlier stage of this task; {request.id} needs a different approver"
        )


class ApprovalGateway(ABC):
    @abstractmethod
    async def request(self, approval: ApprovalRequest) -> ApprovalDecision: ...

    def pending(self) -> list[ApprovalRequest]:
        return []


class AutoApprovalGateway(ApprovalGateway):
    """Development default: approves everything but records that it did."""

    def __init__(self, keep: int = 200) -> None:
        self.log: deque[ApprovalRequest] = deque(maxlen=keep)

    async def request(self, approval: ApprovalRequest) -> ApprovalDecision:
        self.log.append(approval)
        return bind_to_content(
            approval, ApprovalDecision(approval.id, True, "auto-approver", "auto-approved (dev mode)")
        )


#: comment on an approval voided because the arguments changed after the decision
HASH_MISMATCH = "arguments changed after approval; content hash no longer matches"


def bind_to_content(approval: ApprovalRequest, decision: ApprovalDecision) -> ApprovalDecision:
    """Re-check the content hash at the last moment before the tool runs (A14).

    The gateway returns straight to the executor, which then runs the tool, so this is the final
    check. An approval whose arguments no longer hash to what the approver saw becomes a refusal.
    """
    if decision.approved and not approval.intact():
        return ApprovalDecision(approval.id, False, "policy", HASH_MISMATCH, action="reject")
    return decision


class DenyApprovalGateway(ApprovalGateway):
    async def request(self, approval: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(approval.id, False, "policy", "approvals disabled in this environment")


PENDING, APPROVED, REJECTED, EXPIRED = "PENDING", "APPROVED", "REJECTED", "EXPIRED"


class ApprovalConflictError(RuntimeError):
    """The approval is no longer PENDING (decided, expired or interrupted): HTTP 409, and audited."""


#: an audit sink ``audit(actor, action, subject, payload)``; the platform wires the hash-chained log
AuditSink = Callable[[str, str, str, dict[str, Any]], Any]


class ApprovalStore(Protocol):
    def insert(self, request: ApprovalRequest) -> None: ...

    def transition(
        self, approval_id: str, status: str, decided_by: str, comment: str, at: datetime
    ) -> bool: ...

    def status(self, approval_id: str) -> str | None: ...

    def expire_pending(self, reason: str) -> list[str]: ...


class InMemoryApprovalStore:
    """Process-local approval rows with the same one-winner semantics as the SQL store."""

    def __init__(self, keep: int = 5000) -> None:
        self._lock = threading.Lock()
        self._status: OrderedDict[str, str] = OrderedDict()
        self._keep = keep

    def insert(self, request: ApprovalRequest) -> None:
        with self._lock:
            self._status[request.id] = PENDING
            while len(self._status) > self._keep:
                oldest = next(iter(self._status))
                if self._status[oldest] == PENDING:
                    break
                self._status.pop(oldest)

    def transition(self, approval_id: str, status: str, decided_by: str, comment: str, at: datetime) -> bool:
        with self._lock:
            if self._status.get(approval_id) != PENDING:
                return False
            self._status[approval_id] = status
            return True

    def status(self, approval_id: str) -> str | None:
        with self._lock:
            return self._status.get(approval_id)

    def expire_pending(self, reason: str) -> list[str]:
        with self._lock:
            ids = [k for k, v in self._status.items() if v == PENDING]
            for k in ids:
                self._status[k] = EXPIRED
            return ids


class ConcurrencySlot:
    """One unit of the API's task concurrency bound, given back while its task waits for a human (4.4).

    A task parked on an approval uses no CPU and must not starve new work: with
    ``CEAP_MAX_CONCURRENT_INVESTIGATIONS=4``, four tasks awaiting approval leave room for a fifth.
    """

    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self.semaphore = semaphore
        self.held = False
        self._token: contextvars.Token[ConcurrencySlot | None] | None = None

    async def __aenter__(self) -> ConcurrencySlot:
        await self.semaphore.acquire()
        self.held = True
        self._token = _CURRENT_SLOT.set(self)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._token is not None:
            _CURRENT_SLOT.reset(self._token)
        if self.held:
            self.held = False
            self.semaphore.release()


_CURRENT_SLOT: contextvars.ContextVar[ConcurrencySlot | None] = contextvars.ContextVar(
    "ceap_concurrency_slot", default=None
)


@contextlib.asynccontextmanager
async def released_slot() -> AsyncIterator[None]:
    """Give the current task's concurrency slot back while the body awaits; take it again after."""
    slot = _CURRENT_SLOT.get()
    if slot is None or not slot.held:
        yield
        return
    slot.held = False
    slot.semaphore.release()
    try:
        yield
    finally:
        await slot.semaphore.acquire()
        slot.held = True


class QueuedApprovalGateway(ApprovalGateway):
    """Parks the step until a human decides through the API (or the timeout elapses).

    Every request is a row in an :class:`ApprovalStore` (SQLite in the platform, in memory otherwise).
    A decision is a conditional PENDING -> APPROVED/REJECTED transition, so of two concurrent decides
    exactly one wins; the loser, and any decide after a timeout or a restart, gets
    :class:`ApprovalConflictError` (HTTP 409) and an audit entry (R4 item 4.2). The waiting task's
    concurrency slot is released while it waits (R4 item 4.4). Public API unchanged since 0.4.0:
    ``request``, ``pending``, ``decide``; the constructor only gained keyword-only arguments.
    """

    def __init__(
        self,
        timeout_seconds: float = 120.0,
        *,
        store: ApprovalStore | None = None,
        audit: AuditSink | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.store: ApprovalStore = store or InMemoryApprovalStore()
        self.audit = audit
        self._pending: dict[str, ApprovalRequest] = {}
        self._futures: dict[str, tuple[asyncio.Future[ApprovalDecision], asyncio.AbstractEventLoop]] = {}
        self.history: deque[ApprovalDecision] = deque(maxlen=500)

    def pending(self) -> list[ApprovalRequest]:
        return list(self._pending.values())

    def _audit(self, actor: str, action: str, subject: str, payload: dict[str, Any]) -> None:
        if self.audit is not None:
            self.audit(actor, action, subject, payload)

    async def request(self, approval: ApprovalRequest) -> ApprovalDecision:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ApprovalDecision] = loop.create_future()
        self.store.insert(approval)
        self._pending[approval.id] = approval
        self._futures[approval.id] = (fut, loop)
        self._audit(
            approval.requested_by,
            "approval.requested",
            approval.id,
            {"task_id": approval.task_id, "step_id": approval.step_id, "content_hash": approval.content_hash},
        )
        try:
            async with released_slot():
                try:
                    decision = await asyncio.wait_for(asyncio.shield(fut), timeout=self.timeout_seconds)
                except TimeoutError:
                    reason = f"no decision within {self.timeout_seconds}s"
                    if self.store.transition(approval.id, EXPIRED, "timeout", reason, utc_now()):
                        decision = ApprovalDecision(approval.id, False, "timeout", reason)
                        self._audit("timeout", "approval.expired", approval.id, {"reason": reason})
                    else:  # a human decided in the same instant: that decision stands
                        decision = await fut
        finally:
            self._pending.pop(approval.id, None)
            self._futures.pop(approval.id, None)
        bound = bind_to_content(approval, decision)
        if bound is not decision:
            self._audit(
                "policy",
                "approval.hash_mismatch",
                approval.id,
                {"expected": approval.content_hash, "actual": approval.current_hash()},
            )
        self.history.append(bound)
        return bound

    def decide(
        self,
        request_id: str,
        approved: bool,
        decided_by: str,
        comment: str = "",
        *,
        action: str = "",
        content_hash: str | None = None,
    ) -> ApprovalDecision:
        """Decide a pending request. ``content_hash``, when given, is the hash the approver reviewed;
        a mismatch with the pending request is refused, so a decision cannot attach to different
        arguments than the ones shown (A14)."""
        entry = self._futures.get(request_id)
        if entry is None:
            status = self.store.status(request_id)
            if status is None:
                raise KeyError(f"no pending approval {request_id}")
            self._audit(
                decided_by, "approval.late_decide", request_id, {"status": status, "approved": approved}
            )
            raise ApprovalConflictError(f"approval {request_id} is {status}; the decision was refused")
        pending = self._pending[request_id]
        check_four_eyes(pending, decided_by)
        if content_hash is not None and content_hash != pending.content_hash:
            self._audit(
                decided_by,
                "approval.hash_mismatch",
                request_id,
                {"expected": pending.content_hash, "presented": content_hash},
            )
            raise ApprovalConflictError(
                f"approval {request_id}: the reviewed content hash does not match the pending request"
            )
        action = action or ("approve" if approved else "reject")
        if action not in DECISION_ACTIONS or (action == "approve") != approved:
            raise ValueError(f"action {action!r} does not match approved={approved}")
        decision = ApprovalDecision(request_id, approved, decided_by, comment, action=action)
        if not self.store.transition(
            request_id, APPROVED if approved else REJECTED, decided_by, comment, decision.decided_at
        ):
            current = self.store.status(request_id)
            self._audit(
                decided_by, "approval.late_decide", request_id, {"status": current, "approved": approved}
            )
            raise ApprovalConflictError(f"approval {request_id} was already decided ({current})")
        # R3B 3B.8: the reject / modify reason is part of the hash-chained audit entry
        self._audit(
            decided_by,
            "approval.decided",
            request_id,
            {"approved": approved, "action": action, "reason": comment, "comment": comment},
        )
        fut, loop = entry

        def resolve() -> None:
            if not fut.done():
                fut.set_result(decision)

        try:
            running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            resolve()
        else:  # decided from another thread (or no loop): hand the result to the waiting loop
            loop.call_soon_threadsafe(resolve)
        return decision

    def expire_all(self, reason: str) -> list[str]:
        """Expire every PENDING row (startup recovery after a crash or SIGKILL)."""
        ids = self.store.expire_pending(reason)
        for approval_id in ids:
            self._audit("timeout", "approval.expired", approval_id, {"reason": reason})
        return ids


# ------------------------------------------------------------------ review payload (R3B 3B.8)

#: what a human may do with an approval request
DECISION_ACTIONS = ("approve", "reject", "modify")
REVIEW_PAYLOAD_VERSION = 1
MAX_REVIEW_FINDINGS = 20


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def review_payload(
    *,
    hypotheses: Mapping[str, Any] | None,
    findings: Sequence[Any],
    evidence: Mapping[str, Any],
    execution: Mapping[str, Any] | None,
    critique: Mapping[str, Any] | None,
    disputes: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """What the approver sees (PLAN 3B.8): the hypothesis table, the evidence behind each finding,
    the diff against the pre-trade estimate and the critic's disputes. Built from harness state
    only; model text is limited to the critic's stored comments, labelled as such."""
    rows = list((hypotheses or {}).get("rows") or [])
    links = []
    for f in list(findings)[:MAX_REVIEW_FINDINGS]:
        links.append(
            {
                "finding_id": f.id,
                "statement": str(f.statement)[:300],
                "confidence": f.confidence,
                "evidence": [
                    {"id": e, "type": getattr(getattr(evidence.get(e), "type", None), "value", None)}
                    for e in f.supporting_evidence
                ],
                "evidence_refs": [{"id": r.id, "path": r.path, "value": r.value} for r in f.evidence_refs],
            }
        )
    window = (execution or {}).get("window") or {}
    baseline = (execution or {}).get("baseline") or {}
    realised, estimate = (
        _num(window.get("implementation_shortfall_bps")),
        _num(baseline.get("implementation_shortfall_bps")),
    )
    pre_trade = {
        "metric": "implementation_shortfall_bps",
        "estimate_bps": estimate,
        "estimate_source": "baseline window (the parametric model in ceap.ml.pretrade_cost is not used by this view)",
        "realised_bps": realised,
        "diff_bps": None if realised is None or estimate is None else round(realised - estimate, 4),
    }
    return {
        "version": REVIEW_PAYLOAD_VERSION,
        "hypotheses": {
            group: [r for r in rows if r.get("verdict") == group]
            for group in ("SUPPORTED", "REFUTED", "UNTESTED")
        },
        "evidence_links": links,
        "pre_trade_diff": pre_trade if execution else None,
        "disputes": {
            "checks": list((disputes or {}).get("checks") or []),
            "recorded_without_check": list((disputes or {}).get("recorded_without_check") or []),
            "contradictions": list((critique or {}).get("contradictions") or []),
            "critic_comments_are_model_text": True,
        },
    }


#: argument keys that carry harness bookkeeping rather than tool input; shown separately
_VIEW_INTERNAL_KEYS = ("review",)


def approval_view(approval: ApprovalRequest) -> dict[str, Any]:
    """What the approver must see to sign off (A14): the tool, its exact arguments and the hash.

    ``arguments`` is everything the tool receives except the review payload, which is returned
    on its own. The hash covers both, and ``hash_verified`` says whether it still matches now.
    """
    return {
        "tool_id": approval.tool_id,
        "arguments": {k: v for k, v in approval.arguments.items() if k not in _VIEW_INTERNAL_KEYS},
        "content_hash": approval.content_hash,
        "hash_covers": ["tool_id", "arguments (including review)"],
        "hash_verified": approval.intact(),
    }
