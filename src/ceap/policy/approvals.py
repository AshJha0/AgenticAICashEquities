"""Human-in-the-loop approvals."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ceap.domain.common import new_id, utc_now


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
        decision = ApprovalDecision(request_id, approved, decided_by, comment)
        fut.set_result(decision)
        return decision
