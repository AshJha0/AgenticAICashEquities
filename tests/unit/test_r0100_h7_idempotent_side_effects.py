"""0.10.0 item H7 (finding AI-2-04): side-effecting tools receive the idempotency key and are not
retried; read-only tools keep their retries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ceap.domain.policy import PolicyContext, PolicyDecision, PolicyEvaluation
from ceap.domain.tools import (
    RiskLevel,
    Tool,
    ToolExecutionContext,
    ToolMetadata,
    ToolRegistry,
    ToolRequest,
    ToolResult,
    ToolStatus,
    TransientToolError,
)
from ceap.harness.cancellation import CancellationToken
from ceap.harness.durable import idempotency_key
from ceap.harness.executor import RunContext, StepExecutor
from ceap.harness.memory import InvestigationMemory
from ceap.harness.retry import RetryPolicy
from ceap.observability.tracing import InMemoryTracer
from ceap.policy.approvals import AutoApprovalGateway


class _AllowAll:
    async def evaluate(self, request, metadata, context):  # noqa: ANN001 - test double
        return PolicyEvaluation(PolicyDecision.ALLOW, "test", "allow-all", request.tool_id)


class _FlakyAfterWrite(Tool):
    """Writes a staged set, then fails as if the reply was lost on the way back."""

    def __init__(self, tool_id: str, read_only: bool) -> None:
        self._meta = ToolMetadata(tool_id, tool_id, "test", read_only=read_only, risk_level=RiskLevel.HIGH)
        self.writes: list[str] = []
        self.keys: list[str] = []

    @property
    def id(self) -> str:
        return self._meta.id

    @property
    def metadata(self) -> ToolMetadata:
        return self._meta

    async def execute(self, request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
        self.keys.append(context.idempotency_key)
        self.writes.append(f"STG-{len(self.writes) + 1}")
        if len(self.writes) == 1:
            raise TransientToolError("connection reset after the write")
        return ToolResult(status=ToolStatus.SUCCESS, data={"staging_id": self.writes[-1]})


async def _run(tool: _FlakyAfterWrite) -> tuple[ToolResult, str]:
    registry = ToolRegistry()
    registry.register(tool)
    executor = StepExecutor(
        registry, _AllowAll(), AutoApprovalGateway(), InMemoryTracer(), InvestigationMemory(),
        RetryPolicy(max_attempts=3, base_delay_seconds=0.0), 5.0,
    )
    policy_context = PolicyContext("user:alice", frozenset(), frozenset(), "T1")
    run = RunContext("T1", "R1", datetime.now(UTC) + timedelta(minutes=1), policy_context, CancellationToken())
    request = ToolRequest(tool.id, {"signal": "momentum_12_1"})
    return await executor.execute_tool(request, "gov-stage-orders", run), idempotency_key("T1", "gov-stage-orders", request)


async def test_retried_stage_orders_produces_one_staged_set_under_a_failure_after_the_write():
    tool = _FlakyAfterWrite("execution.stage_orders", read_only=False)
    result, key = await _run(tool)
    assert tool.writes == ["STG-1"]  # not retried: one staged set
    assert result.status is ToolStatus.ERROR and "connection reset" in (result.error or "")
    assert tool.keys == [key]  # the tool received the run's idempotency key


async def test_read_only_tool_is_still_retried():
    tool = _FlakyAfterWrite("market_data.get_quotes", read_only=True)
    result, _ = await _run(tool)
    assert result.ok and len(tool.writes) == 2
