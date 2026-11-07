"""Human-in-the-loop approvals."""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ceap.domain.common import new_id, utc_now
from ceap.util.canonical import content_hash


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
            payload = {"tool_id": self.tool_id, "arguments": self.arguments}
            try:
                digest = content_hash(payload)
            except (TypeError, ValueError):  # non-canonical argument types: hash a stable text form
                digest = content_hash(json.dumps(payload, sort_keys=True, default=str))
            object.__setattr__(self, "content_hash", digest)

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
        return ApprovalDecision(approval.id, True, "auto-approver", "auto-approved (dev mode)")


class DenyApprovalGateway(ApprovalGateway):
    async def request(self, approval: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(approval.id, False, "policy", "approvals disabled in this environment")


class QueuedApprovalGateway(ApprovalGateway):
    """Parks the step until a human decides through the API (or the timeout elapses)."""

    def __init__(self, timeout_seconds: float = 120.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: dict[str, ApprovalRequest] = {}
        self._futures: dict[str, asyncio.Future[ApprovalDecision]] = {}
        self.history: deque[ApprovalDecision] = deque(maxlen=500)

    def pending(self) -> list[ApprovalRequest]:
        return list(self._pending.values())

    async def request(self, approval: ApprovalRequest) -> ApprovalDecision:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ApprovalDecision] = loop.create_future()
        self._pending[approval.id] = approval
        self._futures[approval.id] = fut
        try:
            decision = await asyncio.wait_for(fut, timeout=self.timeout_seconds)
        except TimeoutError:
            decision = ApprovalDecision(
                approval.id, False, "timeout", f"no decision within {self.timeout_seconds}s"
            )
        finally:
            self._pending.pop(approval.id, None)
            self._futures.pop(approval.id, None)
        self.history.append(decision)
        return decision

    def decide(self, request_id: str, approved: bool, decided_by: str, comment: str = "") -> ApprovalDecision:
        fut = self._futures.get(request_id)
        if fut is None:
            raise KeyError(f"no pending approval {request_id}")
        if fut.done():
            raise RuntimeError(f"approval {request_id} was already decided")
        check_four_eyes(self._pending[request_id], decided_by)
        decision = ApprovalDecision(request_id, approved, decided_by, comment)
        fut.set_result(decision)
        return decision
