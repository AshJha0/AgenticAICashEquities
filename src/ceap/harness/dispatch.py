"""Tool batches, agent runs and folding their results into memory.

Split out of ``ceap.harness.engine`` in 0.9.0 (H6); ``engine`` re-exports the public API.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from typing import Any

from ceap.domain.agents import Agent, AgentContext, AgentResult
from ceap.domain.common import utc_now
from ceap.domain.plans import PlanStep
from ceap.domain.reports import InvestigationReport
from ceap.domain.tasks import ResultWarning, Task
from ceap.domain.tools import ToolResult, ToolStatus
from ceap.harness.base import HarnessBase
from ceap.harness.cancellation import TaskCancelled
from ceap.harness.common import (
    CRITIC_AGENT_ID,
    MAX_PARALLEL_TOOL_CALLS,
    REQUIRED_INVESTIGATION_AGENTS,
    RESEARCH_KIND,
    _agent_record,
    _leaf_exception,
    _message,
    _w,
    task_kind,
)
from ceap.harness.executor import HarnessToolInvoker, RunContext, StepExecutor
from ceap.harness.memory import (
    InvestigationMemory,
    bind_evidence_refs,
)
from ceap.observability.metrics import metrics
from ceap.policy.agents import agent_policy_context

log = logging.getLogger("ceap.harness.engine")


class DispatchMixin(HarnessBase):
    """Tool batches, agent runs and folding their results into memory."""

    # --------------------------------------------------------------- internals
    def _incomplete_agents(self, task: Task, memory: InvestigationMemory) -> list[str]:
        if task_kind(task) == RESEARCH_KIND:
            return []
        return [
            a
            for a in REQUIRED_INVESTIGATION_AGENTS
            if a in self.agents and not (memory.agent_outputs.get(a) or {}).get("success", False)
        ]

    async def _run_tool_batch(
        self, batch: list[PlanStep], executor: StepExecutor, run: RunContext
    ) -> list[ToolResult]:
        sem = asyncio.Semaphore(MAX_PARALLEL_TOOL_CALLS)
        results: list[ToolResult | None] = [None] * len(batch)
        cancelled: list[TaskCancelled] = []

        async def one(i: int, step: PlanStep) -> None:
            async with sem:
                assert step.tool_request is not None
                if cancelled:
                    results[i] = ToolResult(status=ToolStatus.ERROR, error="skipped: investigation cancelled")
                    return
                if utc_now() > run.deadline:
                    results[i] = ToolResult(
                        status=ToolStatus.TIMEOUT, error="skipped: task deadline exceeded"
                    )
                    return
                try:
                    results[i] = await executor.execute_tool(step.tool_request, step.id, run)
                except TaskCancelled as exc:  # do not let cancellation surface as an ExceptionGroup
                    cancelled.append(exc)
                    results[i] = ToolResult(status=ToolStatus.ERROR, error=f"cancelled: {exc}")

        async with asyncio.TaskGroup() as tg:
            for i, step in enumerate(batch):
                tg.create_task(one(i, step))
        if cancelled:
            raise cancelled[0]
        return [
            r if r is not None else ToolResult(status=ToolStatus.ERROR, error="no result produced")
            for r in results
        ]

    async def _run_agent(
        self,
        agent: Agent,
        step_id: str,
        memory: InvestigationMemory,
        run: RunContext,
        executor: StepExecutor,
        task: Task,
    ) -> AgentResult:
        async with self.tracer.span(f"agent:{agent.id}", step_id=step_id) as span:
            # A16: the agent's tool calls run with its own capability set and tool allowlist
            agent_run = replace(run, policy_context=agent_policy_context(run.policy_context, agent.id))
            ctx = AgentContext(
                task_id=task.id,
                execution_id=run.execution_id,
                deadline=run.deadline,
                task_input=dict(task.input),
                current_plan=memory.scratch.get("plan"),
                evidence=memory.evidence_list(),
                state={
                    "memory": memory,
                    "tool_outputs": memory.tool_outputs,
                    "agent_outputs": memory.agent_outputs,
                    "scratch": memory.scratch,
                    "task": task,
                    "tool_catalogue": self.tools.list(),
                    "messages": tuple(memory.messages),  # R3B 3B.6: typed, read-only
                    "cases": memory.scratch.get("cases") or (),  # R3B 3B.7: read-only views
                },
                tool_registry=self.tools,
                policy_context=agent_run.policy_context,
                cancellation_event=run.cancellation,
                tools=HarnessToolInvoker(executor, agent_run, step_id, agent.id),
                findings=tuple(memory.findings),
            )
            t0 = time.perf_counter()
            try:
                result = await asyncio.wait_for(
                    agent.execute(ctx), timeout=max(1.0, (run.deadline - utc_now()).total_seconds())
                )
            except TaskCancelled:
                raise
            except TimeoutError:
                partial = dict((memory.scratch.get("partial") or {}).get(agent.id) or {})
                findings = tuple(partial.pop("findings", ()) or ())
                result = AgentResult(
                    agent_id=agent.id,
                    success=False,
                    error="agent timed out",
                    output={**partial, "partial": bool(partial or findings)},
                    findings=findings,
                    summary="PARTIAL: agent timed out" if (partial or findings) else "agent timed out",
                )
            except Exception as exc:  # noqa: BLE001 - agent failures are contained
                leaf = _leaf_exception(exc)
                if isinstance(leaf, TaskCancelled):
                    raise leaf from None
                log.exception("agent %s raised", agent.id, extra={"task_id": task.id, "agent_id": agent.id})
                result = AgentResult(agent_id=agent.id, success=False, error=f"{type(leaf).__name__}: {leaf}")
            metrics.observe("ceap_agent_duration_ms", (time.perf_counter() - t0) * 1000.0, agent=agent.id)
            metrics.inc(
                "ceap_agent_runs_total", agent=agent.id, status="success" if result.success else "error"
            )
            span.attributes.update(
                {
                    "success": result.success,
                    "findings": len(result.findings),
                    "evidence": len(result.evidence),
                }
            )
            if not result.success:
                span.status, span.error = "ERROR", result.error
            return result

    @staticmethod
    def _absorb(
        agent_id: str, result: AgentResult, memory: InvestigationMemory, warnings: list[ResultWarning]
    ) -> None:
        memory.add_evidence(*result.evidence)
        if agent_id == CRITIC_AGENT_ID and "adjusted_findings" in result.output:
            memory.replace_findings(list(result.output["adjusted_findings"]))
        else:
            # R3B 3B.4: each finding cites the exact attribute values it used
            memory.add_findings(*(bind_evidence_refs(f, memory.evidence) for f in result.findings))
        memory.messages.append(_message(agent_id, result))
        if agent_id == "planner" and "plan" in result.output:
            memory.scratch["plan"] = result.output["plan"]
        for d in result.output.get("degradations") or ():
            # every rung above 0 of the degradation ladder is visible and marks the result DEGRADED
            warnings.append(
                _w(
                    str(d.get("code")),
                    f"{agent_id}: rung {d.get('rung')}: {d.get('message')}",
                    source=agent_id,
                )
            )
        report = result.output.get("report")
        if isinstance(report, InvestigationReport):
            # R3A 3A.4: a narrative number or evidence id that does not trace to facts is blocking
            untraceable = [
                *(report.critique.get("narrative_number_warnings") or []),
                *(report.critique.get("narrative_evidence_warnings") or []),
            ]
            if untraceable:
                warnings.append(
                    _w(
                        "NARRATIVE_UNTRACEABLE",
                        f"{agent_id}: {len(untraceable)} narrative value(s) do not trace to facts, e.g. {untraceable[0]}",
                        source=agent_id,
                    )
                )
        memory.agent_outputs[agent_id] = {
            k: v for k, v in result.output.items() if k not in ("adjusted_findings", "plan", "report")
        } | {"summary": result.summary, "success": result.success, "error": result.error}

    async def _force_critic(
        self,
        memory: InvestigationMemory,
        run: RunContext,
        executor: StepExecutor,
        task: Task,
        step_results: list[dict[str, Any]],
        warnings: list[ResultWarning],
    ) -> AgentResult | None:
        critic = self.agents.get(CRITIC_AGENT_ID)
        if critic is None:
            warnings.append(_w("NO_CRITIC", "no critic agent registered; findings are un-critiqued"))
            return None
        result = await self._run_agent(critic, "gov-critic-forced", memory, run, executor, task)
        self._absorb(CRITIC_AGENT_ID, result, memory, warnings)
        step_results.append(_agent_record("gov-critic-forced", CRITIC_AGENT_ID, result))
        return result
