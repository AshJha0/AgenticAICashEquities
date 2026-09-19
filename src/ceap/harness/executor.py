"""Step executor: policy -> approval -> retry/timeout -> tool -> memory -> trace."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ceap.domain.agents import ToolInvoker
from ceap.domain.common import utc_now
from ceap.domain.policy import PolicyContext, PolicyDecision, PolicyEngine
from ceap.domain.tools import ToolExecutionContext, ToolRegistry, ToolRequest, ToolResult, ToolStatus
from ceap.harness.cancellation import CancellationToken
from ceap.harness.memory import InvestigationMemory
from ceap.harness.retry import RetryPolicy, retry_async
from ceap.observability.metrics import metrics
from ceap.observability.tracing import ExecutionTracer
from ceap.policy.approvals import ApprovalGateway, ApprovalRequest

log = logging.getLogger(__name__)


class ToolInvocationError(RuntimeError):
    def __init__(self, tool_id: str, result: ToolResult) -> None:
        super().__init__(f"{tool_id}: {result.status.value}: {result.error}")
        self.tool_id = tool_id
        self.result = result


@dataclass(frozen=True)
class RunContext:
    task_id: str
    execution_id: str
    deadline: datetime
    policy_context: PolicyContext
    cancellation: CancellationToken


class StepExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyEngine,
        approvals: ApprovalGateway,
        tracer: ExecutionTracer,
        memory: InvestigationMemory,
        retry_policy: RetryPolicy,
        step_timeout_seconds: float,
        on_awaiting_approval: Callable[[bool], Awaitable[None]] | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.approvals = approvals
        self.tracer = tracer
        self.memory = memory
        self.retry_policy = retry_policy
        self.step_timeout_seconds = step_timeout_seconds
        self._on_awaiting_approval = on_awaiting_approval
        self.policy_log: list[dict[str, Any]] = []

    async def execute_tool(self, request: ToolRequest, step_id: str, run: RunContext) -> ToolResult:
        run.cancellation.raise_if_cancelled()
        async with self.tracer.span(
            f"tool:{request.tool_id}", step_id=step_id, correlation_id=request.correlation_id
        ) as span:
            if not self.registry.has(request.tool_id):
                result = ToolResult(status=ToolStatus.ERROR, error=f"unknown tool {request.tool_id}")
                span.status, span.error = "ERROR", result.error
                metrics.inc("ceap_tool_calls_total", tool=request.tool_id, status="unknown")
                return result
            tool = self.registry.get(request.tool_id)

            # ---- policy -----------------------------------------------------
            evaluation = await self.policy.evaluate(request, tool.metadata, run.policy_context)
            self.policy_log.append(
                {
                    "step_id": step_id,
                    "tool_id": request.tool_id,
                    "decision": evaluation.decision.value,
                    "rule": evaluation.rule,
                    "reason": evaluation.reason,
                }
            )
            self.tracer.event(
                "policy.decision",
                decision=evaluation.decision.value,
                rule=evaluation.rule,
                reason=evaluation.reason,
            )
            if evaluation.decision is PolicyDecision.DENY:
                metrics.inc("ceap_tool_calls_total", tool=request.tool_id, status="denied")
                span.status = "DENIED"
                return ToolResult(
                    status=ToolStatus.DENIED, error=f"policy denied ({evaluation.rule}): {evaluation.reason}"
                )
            if evaluation.decision is PolicyDecision.REQUIRE_APPROVAL:
                approved = await self._seek_approval(request, step_id, run, evaluation.reason)
                if not approved:
                    metrics.inc("ceap_tool_calls_total", tool=request.tool_id, status="approval_denied")
                    span.status = "DENIED"
                    return ToolResult(
                        status=ToolStatus.DENIED, error=f"approval not granted: {evaluation.reason}"
                    )

            # ---- execute with timeout + retry --------------------------------
            ctx = ToolExecutionContext(
                task_id=run.task_id,
                execution_id=run.execution_id,
                step_id=step_id,
                deadline=run.deadline,
                principal=run.policy_context.principal,
                capabilities=run.policy_context.capabilities,
            )
            timeout = min(self.step_timeout_seconds, max(0.1, (run.deadline - utc_now()).total_seconds()))

            async def attempt() -> ToolResult:
                run.cancellation.raise_if_cancelled()
                try:
                    return await asyncio.wait_for(tool.execute(request, ctx), timeout=timeout)
                except TimeoutError:
                    raise
                except asyncio.CancelledError:
                    raise

            def on_retry(n: int, exc: BaseException) -> None:
                metrics.inc("ceap_tool_retries_total", tool=request.tool_id)
                self.tracer.event("tool.retry", attempt=n, error=f"{type(exc).__name__}: {exc}")

            try:
                result = await retry_async(attempt, self.retry_policy, on_retry)
            except TimeoutError:
                result = ToolResult(
                    status=ToolStatus.TIMEOUT,
                    error=f"tool timed out after {timeout:.1f}s ({self.retry_policy.max_attempts} attempts)",
                )

            metrics.inc("ceap_tool_calls_total", tool=request.tool_id, status=result.status.value.lower())
            metrics.observe("ceap_tool_latency_ms", result.execution_time_ms, tool=request.tool_id)
            span.attributes["status"] = result.status.value
            span.attributes["execution_time_ms"] = result.execution_time_ms
            if result.ok:
                self.memory.record_tool(step_id, request, result)
            else:
                span.status = "ERROR"
                span.error = result.error
                log.warning(
                    "tool %s failed: %s",
                    request.tool_id,
                    result.error,
                    extra={"task_id": run.task_id, "step_id": step_id, "tool_id": request.tool_id},
                )
            return result

    async def _seek_approval(self, request: ToolRequest, step_id: str, run: RunContext, reason: str) -> bool:
        approval = ApprovalRequest.create(
            run.task_id, step_id, request.tool_id, request.arguments, reason, run.policy_context.principal
        )
        if self._on_awaiting_approval:
            await self._on_awaiting_approval(True)
        try:
            decision = await self.approvals.request(approval)
        finally:
            if self._on_awaiting_approval:
                await self._on_awaiting_approval(False)
        self.tracer.event(
            "approval.decision",
            request_id=approval.id,
            approved=decision.approved,
            decided_by=decision.decided_by,
        )
        self.policy_log.append(
            {
                "step_id": step_id,
                "tool_id": request.tool_id,
                "decision": "APPROVED" if decision.approved else "REJECTED",
                "rule": "human-approval",
                "reason": decision.comment,
            }
        )
        return decision.approved


class HarnessToolInvoker(ToolInvoker):
    """What agents get: every call routed through the executor (policy, retry, trace)."""

    def __init__(self, executor: StepExecutor, run: RunContext, step_id: str, agent_id: str) -> None:
        self._executor = executor
        self._run = run
        self._step_id = step_id
        self._agent_id = agent_id
        self._n = 0

    async def invoke(self, tool_id: str, arguments: dict[str, Any]) -> Any:
        self._n += 1
        request = ToolRequest(tool_id=tool_id, arguments=dict(arguments))
        result = await self._executor.execute_tool(
            request, f"{self._step_id}/{self._agent_id}#{self._n}", self._run
        )
        if not result.ok:
            raise ToolInvocationError(tool_id, result)
        return result.data
