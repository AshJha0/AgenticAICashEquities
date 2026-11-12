"""Typed base shared by the harness mixins (0.9.0 integration).

Plain idea: ``PlanningMixin``, ``DispatchMixin``, ``ApprovalsMixin`` and ``EvidenceMixin`` read
attributes that ``AgentHarness.__init__`` sets and call methods that a sibling mixin defines.
``HarnessBase`` declares those names with their types so mypy checks every mixin on its own.

Formally: the class holds annotations only (no values, no ``__init__``) and declares the
cross-mixin methods under ``TYPE_CHECKING``, so at run time it adds nothing to the MRO's
behaviour; ``AgentHarness`` and the mixins keep their implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ceap.config import Settings
    from ceap.domain.agents import Agent, AgentResult
    from ceap.domain.plans import PlanStep
    from ceap.domain.policy import PolicyEngine
    from ceap.domain.tasks import Task
    from ceap.domain.tools import ToolRegistry, ToolResult
    from ceap.harness.durable import RunJournal
    from ceap.harness.executor import RunContext, StepExecutor
    from ceap.harness.memory import CaseMemory, InvestigationMemory
    from ceap.harness.retry import RetryPolicy
    from ceap.observability.tracing import ExecutionTracer
    from ceap.policy.approvals import ApprovalGateway


class HarnessBase:
    """Attributes set by ``AgentHarness.__init__`` and read by the mixins."""

    journal: RunJournal | None
    planner: Agent
    case_memory: CaseMemory | None
    agents: dict[str, Agent]
    tools: ToolRegistry
    policy: PolicyEngine
    reporter: Agent
    tracer: ExecutionTracer
    approvals: ApprovalGateway
    settings: Settings
    retry_policy: RetryPolicy

    if TYPE_CHECKING:
        # defined by DispatchMixin
        async def _run_tool_batch(
            self, batch: list[PlanStep], executor: StepExecutor, run: RunContext
        ) -> list[ToolResult]: ...

        async def _run_agent(
            self,
            agent: Agent,
            step_id: str,
            memory: InvestigationMemory,
            run: RunContext,
            executor: StepExecutor,
            task: Task,
        ) -> AgentResult: ...

        # defined by EvidenceMixin
        def _record_case(
            self, task: Task, step: PlanStep, decision: Any, memory: InvestigationMemory
        ) -> None: ...
