"""The AgentHarness: a state machine that runs an investigation end to end.

    Task -> Planner -> Plan validation -> Step execution (tools / agents)
         -> Critic -> Evidence validation -> Finalise (report)

The harness controls the agents; agents never control the harness.
Governance steps (critic, evidence validation, finalisation) are enforced
by the harness even if a plan omits them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

from ceap.config import Settings, load_settings
from ceap.domain.agents import Agent, AgentContext, AgentResult
from ceap.domain.common import new_id, to_jsonable, utc_now
from ceap.domain.evidence import Evidence
from ceap.domain.findings import Finding
from ceap.domain.plans import Plan, PlanStep, PlanValidationError, StepType
from ceap.domain.policy import PolicyContext, PolicyDecision, PolicyEngine
from ceap.domain.reports import InvestigationReport
from ceap.domain.tasks import Task
from ceap.domain.tools import ToolRegistry, ToolResult, ToolStatus
from ceap.harness.cancellation import CancellationToken, TaskCancelled
from ceap.harness.executor import HarnessToolInvoker, RunContext, StepExecutor
from ceap.harness.memory import InvestigationMemory
from ceap.harness.retry import RetryPolicy
from ceap.harness.state_machine import HarnessState, StateMachine
from ceap.observability.metrics import metrics
from ceap.observability.tracing import ExecutionTracer, InMemoryTracer
from ceap.policy.approvals import ApprovalGateway, AutoApprovalGateway

log = logging.getLogger(__name__)

CRITIC_AGENT_ID = "critic"
MAX_PARALLEL_TOOL_CALLS = 8


@dataclass
class HarnessResult:
    task_id: str
    execution_id: str
    state: HarnessState
    plan: Plan | None
    findings: tuple[Finding, ...]
    evidence: tuple[Evidence, ...]
    report: InvestigationReport | None
    error: str | None
    trace: dict[str, Any]
    state_history: list[dict[str, str]]
    policy_log: list[dict[str, Any]]
    agent_outputs: dict[str, dict[str, Any]]
    step_results: list[dict[str, Any]]
    duration_ms: float
    started_at: str
    finished_at: str
    warnings: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.state is HarnessState.COMPLETED and self.report is not None

    def to_dict(self, include_trace: bool = False) -> dict[str, Any]:
        out = {
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "state": self.state.value,
            "success": self.success,
            "error": self.error,
            "warnings": self.warnings,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "plan": to_jsonable(self.plan),
            "findings": to_jsonable(self.findings),
            "evidence": to_jsonable(self.evidence),
            "report": to_jsonable(self.report),
            "state_history": self.state_history,
            "policy_log": self.policy_log,
            "agent_outputs": to_jsonable(self.agent_outputs),
            "step_results": self.step_results,
        }
        if include_trace:
            out["trace"] = self.trace
        return out


class AgentHarness:
    def __init__(
        self,
        planner: Agent,
        agents: dict[str, Agent],
        tools: ToolRegistry,
        policy: PolicyEngine,
        reporter: Agent,
        tracer: ExecutionTracer | None = None,
        approvals: ApprovalGateway | None = None,
        settings: Settings | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.planner = planner
        self.agents = agents
        self.tools = tools
        self.policy = policy
        self.reporter = reporter
        self.tracer = tracer or InMemoryTracer()
        self.approvals = approvals or AutoApprovalGateway()
        self.settings = settings or load_settings()
        self.retry_policy = retry_policy or RetryPolicy(max_attempts=self.settings.max_retries + 1)

    # ------------------------------------------------------------------ public
    async def execute(
        self,
        task: Task,
        policy_context: PolicyContext,
        cancellation: CancellationToken | None = None,
    ) -> HarnessResult:
        started = time.perf_counter()
        started_at = utc_now()
        execution_id = new_id("RUN")
        cancellation = cancellation or CancellationToken()
        memory = InvestigationMemory()
        sm = StateMachine()
        deadline = started_at + timedelta(seconds=self.settings.task_timeout_seconds)
        run = RunContext(task.id, execution_id, deadline, policy_context, cancellation)

        async def on_awaiting(entering: bool) -> None:
            if entering and sm.state is HarnessState.EXECUTING:
                sm.transition(HarnessState.AWAITING_APPROVAL, "tool requires human approval")
            elif not entering and sm.state is HarnessState.AWAITING_APPROVAL:
                sm.transition(HarnessState.EXECUTING, "approval decided")

        executor = StepExecutor(
            self.tools,
            self.policy,
            self.approvals,
            self.tracer,
            memory,
            self.retry_policy,
            self.settings.step_timeout_seconds,
            on_awaiting,
        )
        plan: Plan | None = None
        report: InvestigationReport | None = None
        error: str | None = None
        warnings: list[str] = []
        step_results: list[dict[str, Any]] = []
        metrics.inc("ceap_investigations_total")

        try:
            async with self.tracer.span("harness.execute", task_id=task.id, execution_id=execution_id):
                # ---- planning ---------------------------------------------------
                sm.transition(HarnessState.PLANNING, "task accepted")
                async with self.tracer.span("harness.plan"):
                    planner_result = await self._run_agent(self.planner, "plan", memory, run, executor, task)
                    if not planner_result.success or "plan" not in planner_result.output:
                        raise PlanValidationError(planner_result.error or "planner produced no plan")
                    plan = planner_result.output["plan"]

                # ---- plan validation -------------------------------------------
                sm.transition(HarnessState.VALIDATING_PLAN, "plan produced")
                async with self.tracer.span("harness.validate_plan", steps=len(plan.steps)):
                    await self._validate_plan(plan, policy_context)
                    plan = self._enforce_governance_steps(plan)

                # ---- execution -------------------------------------------------
                sm.transition(HarnessState.EXECUTING, "plan validated")
                critic_ran = False
                for batch in _batches(plan.ordered_steps()):
                    cancellation.raise_if_cancelled()
                    if utc_now() > deadline:
                        raise TimeoutError("task deadline exceeded")
                    kind = batch[0].type
                    if kind is StepType.TOOL_CALL:
                        results = await self._run_tool_batch(batch, executor, run)
                        for step, result in zip(batch, results, strict=True):
                            step_results.append(_step_record(step, result))
                            if not result.ok:
                                warnings.append(f"step {step.id} ({step.description}) failed: {result.error}")
                        continue
                    step = batch[0]
                    if step.type is StepType.AGENT_CALL:
                        agent_id = step.agent_id or ""
                        agent = self.agents.get(agent_id)
                        if agent is None:
                            warnings.append(f"step {step.id}: unknown agent {agent_id}")
                            step_results.append(
                                {
                                    "step_id": step.id,
                                    "type": step.type.value,
                                    "status": "SKIPPED",
                                    "error": f"unknown agent {agent_id}",
                                }
                            )
                            continue
                        if agent_id == CRITIC_AGENT_ID:
                            sm.transition(HarnessState.CRITIQUING, "critic step")
                        result = await self._run_agent(agent, step.id, memory, run, executor, task)
                        self._absorb(agent_id, result, memory)
                        step_results.append(
                            {
                                "step_id": step.id,
                                "type": step.type.value,
                                "agent_id": agent_id,
                                "status": "SUCCESS" if result.success else "ERROR",
                                "error": result.error,
                                "findings": len(result.findings),
                                "summary": result.summary,
                            }
                        )
                        if not result.success:
                            warnings.append(f"agent {agent_id} failed: {result.error}")
                        if agent_id == CRITIC_AGENT_ID:
                            critic_ran = True
                            sm.transition(HarnessState.EXECUTING, "critique complete")
                    elif step.type is StepType.VALIDATION:
                        if not critic_ran:  # governance: no validation before critique
                            result = await self._force_critic(
                                memory, run, executor, task, step_results, warnings
                            )
                            critic_ran = True
                        sm.transition(HarnessState.VALIDATING_EVIDENCE, "validation step")
                        dropped = self._validate_evidence(memory)
                        step_results.append(
                            {
                                "step_id": step.id,
                                "type": step.type.value,
                                "status": "SUCCESS",
                                "dropped_findings": dropped,
                            }
                        )
                        sm.transition(HarnessState.EXECUTING, "evidence validated")
                    elif step.type is StepType.FINALISE:
                        if not critic_ran:
                            await self._force_critic(memory, run, executor, task, step_results, warnings)
                            critic_ran = True
                            self._validate_evidence(memory)
                        sm.transition(HarnessState.FINALISING, "finalise step")
                        result = await self._run_agent(self.reporter, step.id, memory, run, executor, task)
                        if not result.success or "report" not in result.output:
                            raise RuntimeError(result.error or "reporter produced no report")
                        report = result.output["report"]
                        step_results.append(
                            {"step_id": step.id, "type": step.type.value, "status": "SUCCESS"}
                        )
                    elif step.type in (StepType.ANALYSIS, StepType.HUMAN_APPROVAL):
                        step_results.append(
                            {
                                "step_id": step.id,
                                "type": step.type.value,
                                "status": "SKIPPED",
                                "error": "no executor for step type",
                            }
                        )

                if report is None:
                    raise RuntimeError("plan finished without producing a report")
                sm.transition(HarnessState.COMPLETED, "report produced")
                metrics.inc("ceap_investigations_completed_total")
        except TaskCancelled as exc:
            error = f"cancelled: {exc}"
            sm.cancel(str(exc))
            metrics.inc("ceap_investigations_cancelled_total")
        except Exception as exc:  # noqa: BLE001 - terminal failure boundary
            error = f"{type(exc).__name__}: {exc}"
            log.exception("investigation failed", extra={"task_id": task.id, "execution_id": execution_id})
            sm.fail(error)
            metrics.inc("ceap_investigations_failed_total")

        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        metrics.observe("ceap_investigation_duration_ms", duration_ms)
        return HarnessResult(
            task_id=task.id,
            execution_id=execution_id,
            state=sm.state,
            plan=plan,
            findings=tuple(memory.findings),
            evidence=memory.evidence_list(),
            report=report,
            error=error,
            trace=self.tracer.export(),
            state_history=sm.export(),
            policy_log=executor.policy_log,
            agent_outputs=memory.agent_outputs,
            step_results=step_results,
            duration_ms=duration_ms,
            started_at=started_at.isoformat(),
            finished_at=utc_now().isoformat(),
            warnings=warnings,
        )

    # --------------------------------------------------------------- internals
    async def _validate_plan(self, plan: Plan, policy_context: PolicyContext) -> None:
        if not plan.steps:
            raise PlanValidationError("plan has no steps")
        seq = [s.sequence for s in plan.ordered_steps()]
        if len(set(seq)) != len(seq):
            raise PlanValidationError("plan step sequences are not unique")
        ids = [s.id for s in plan.steps]
        if len(set(ids)) != len(ids):
            raise PlanValidationError("plan step ids are not unique")
        for step in plan.steps:
            if step.type is StepType.TOOL_CALL:
                if step.tool_request is None:
                    raise PlanValidationError(f"step {step.id}: TOOL_CALL without tool_request")
                if not self.tools.has(step.tool_request.tool_id):
                    raise PlanValidationError(f"step {step.id}: unknown tool {step.tool_request.tool_id}")
                meta = self.tools.get(step.tool_request.tool_id).metadata
                evaluation = await self.policy.evaluate(step.tool_request, meta, policy_context)
                if evaluation.decision is PolicyDecision.DENY:
                    raise PlanValidationError(
                        f"step {step.id}: policy denies {meta.id} ({evaluation.rule}: {evaluation.reason})"
                    )
            elif step.type is StepType.AGENT_CALL:
                if not step.agent_id:
                    raise PlanValidationError(f"step {step.id}: AGENT_CALL without agent_id")
                if step.agent_id not in self.agents:
                    raise PlanValidationError(f"step {step.id}: unknown agent {step.agent_id}")
            for dep in step.depends_on:
                if dep not in ids:
                    raise PlanValidationError(f"step {step.id}: unknown dependency {dep}")

    def _enforce_governance_steps(self, plan: Plan) -> Plan:
        """Guarantee critic -> validation -> finalise at the end of every plan."""
        steps = list(plan.ordered_steps())
        types = [s.type for s in steps]
        agent_ids = [s.agent_id for s in steps]
        next_seq = (steps[-1].sequence + 1) if steps else 1
        additions: list[PlanStep] = []
        if CRITIC_AGENT_ID not in agent_ids and CRITIC_AGENT_ID in self.agents:
            additions.append(
                PlanStep(
                    id="gov-critic",
                    sequence=next_seq,
                    type=StepType.AGENT_CALL,
                    description="Governance: independent critique",
                    agent_id=CRITIC_AGENT_ID,
                )
            )
            next_seq += 1
        if StepType.VALIDATION not in types:
            additions.append(
                PlanStep(
                    id="gov-validation",
                    sequence=next_seq,
                    type=StepType.VALIDATION,
                    description="Governance: validate evidence references",
                )
            )
            next_seq += 1
        if StepType.FINALISE not in types:
            additions.append(
                PlanStep(
                    id="gov-finalise",
                    sequence=next_seq,
                    type=StepType.FINALISE,
                    description="Governance: produce report",
                )
            )
        if not additions:
            return plan
        # keep any user FINALISE last
        finalise = [s for s in steps if s.type is StepType.FINALISE]
        others = [s for s in steps if s.type is not StepType.FINALISE]
        merged = others + additions + finalise
        resequenced = tuple(replace(s, sequence=i + 1) for i, s in enumerate(merged))
        return Plan(
            id=plan.id,
            task_id=plan.task_id,
            steps=resequenced,
            rationale=plan.rationale,
            generated_by=plan.generated_by,
        )

    async def _run_tool_batch(
        self, batch: list[PlanStep], executor: StepExecutor, run: RunContext
    ) -> list[ToolResult]:
        sem = asyncio.Semaphore(MAX_PARALLEL_TOOL_CALLS)
        results: list[ToolResult | None] = [None] * len(batch)

        async def one(i: int, step: PlanStep) -> None:
            async with sem:
                assert step.tool_request is not None
                results[i] = await executor.execute_tool(step.tool_request, step.id, run)

        async with asyncio.TaskGroup() as tg:
            for i, step in enumerate(batch):
                tg.create_task(one(i, step))
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
            ctx = AgentContext(
                task_id=task.id,
                execution_id=run.execution_id,
                deadline=run.deadline,
                task_input=dict(task.input),
                current_plan=None,
                evidence=memory.evidence_list(),
                state={
                    "memory": memory,
                    "tool_outputs": memory.tool_outputs,
                    "agent_outputs": memory.agent_outputs,
                    "scratch": memory.scratch,
                    "task": task,
                    "tool_catalogue": self.tools.list(),
                },
                tool_registry=self.tools,
                policy_context=run.policy_context,
                cancellation_event=run.cancellation,
                tools=HarnessToolInvoker(executor, run, step_id, agent.id),
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
                result = AgentResult(agent_id=agent.id, success=False, error="agent timed out")
            except Exception as exc:  # noqa: BLE001 - agent failures are contained
                log.exception("agent %s raised", agent.id, extra={"task_id": task.id, "agent_id": agent.id})
                result = AgentResult(agent_id=agent.id, success=False, error=f"{type(exc).__name__}: {exc}")
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
    def _absorb(agent_id: str, result: AgentResult, memory: InvestigationMemory) -> None:
        memory.add_evidence(*result.evidence)
        if agent_id == CRITIC_AGENT_ID and "adjusted_findings" in result.output:
            memory.replace_findings(list(result.output["adjusted_findings"]))
        else:
            memory.add_findings(*result.findings)
        memory.agent_outputs[agent_id] = {
            k: v for k, v in result.output.items() if k != "adjusted_findings"
        } | {"summary": result.summary, "success": result.success, "error": result.error}

    async def _force_critic(
        self,
        memory: InvestigationMemory,
        run: RunContext,
        executor: StepExecutor,
        task: Task,
        step_results: list[dict[str, Any]],
        warnings: list[str],
    ) -> AgentResult | None:
        critic = self.agents.get(CRITIC_AGENT_ID)
        if critic is None:
            warnings.append("no critic agent registered; findings are un-critiqued")
            return None
        result = await self._run_agent(critic, "gov-critic-forced", memory, run, executor, task)
        self._absorb(CRITIC_AGENT_ID, result, memory)
        step_results.append(
            {
                "step_id": "gov-critic-forced",
                "type": "AGENT_CALL",
                "agent_id": CRITIC_AGENT_ID,
                "status": "SUCCESS" if result.success else "ERROR",
                "error": result.error,
            }
        )
        return result

    @staticmethod
    def _validate_evidence(memory: InvestigationMemory) -> list[str]:
        kept: list[Finding] = []
        dropped: list[str] = []
        for f in memory.findings:
            missing = memory.unresolved_evidence(f)
            if missing or not f.supporting_evidence:
                dropped.append(f.id)
                memory.scratch.setdefault("validation", {}).setdefault("dropped", []).append(
                    {"finding_id": f.id, "statement": f.statement, "missing_evidence": missing}
                )
                continue
            kept.append(f)
        memory.replace_findings(kept)
        return dropped


def _batches(steps: list[PlanStep]) -> list[list[PlanStep]]:
    """Group consecutive independent TOOL_CALL steps so they run concurrently."""
    batches: list[list[PlanStep]] = []
    for step in steps:
        if (
            step.type is StepType.TOOL_CALL
            and batches
            and batches[-1][0].type is StepType.TOOL_CALL
            and not step.depends_on
        ):
            batches[-1].append(step)
        else:
            batches.append([step])
    return batches


def _step_record(step: PlanStep, result: ToolResult) -> dict[str, Any]:
    return {
        "step_id": step.id,
        "type": step.type.value,
        "tool_id": step.tool_request.tool_id if step.tool_request else None,
        "status": result.status.value,
        "error": result.error,
        "execution_time_ms": result.execution_time_ms,
        "evidence": [e.id for e in result.evidence],
    }
