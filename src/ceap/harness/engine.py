"""The AgentHarness: a state machine that runs an investigation end to end.

    Task -> Planner -> Plan validation -> Step execution (tools / agents)
         -> Critic -> Evidence validation -> Finalise (report)

The harness controls the agents; agents never control the harness.
Governance steps (critic, evidence validation, finalisation) are enforced
by the harness: whatever the plan says, the critic runs *after* every
specialist agent and *before* validation and the report.
"""

from __future__ import annotations

import asyncio
import json
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
from ceap.domain.research import research_tool_arguments
from ceap.domain.tasks import Task
from ceap.domain.tools import ToolRegistry, ToolRequest, ToolResult, ToolStatus
from ceap.harness.cancellation import CancellationToken, TaskCancelled
from ceap.harness.executor import HarnessToolInvoker, RunContext, StepExecutor, race_cancellation
from ceap.harness.memory import InvestigationMemory
from ceap.harness.retry import RetryPolicy
from ceap.harness.state_machine import HarnessState, StateMachine
from ceap.observability.metrics import metrics
from ceap.observability.tracing import ExecutionTracer, InMemoryTracer
from ceap.policy.approvals import ApprovalGateway, ApprovalRequest, AutoApprovalGateway

log = logging.getLogger(__name__)

CRITIC_AGENT_ID = "critic"
PORTFOLIO_RISK_AGENT_ID = "portfolio_risk"
MAX_PARALLEL_TOOL_CALLS = 8
MAX_PLAN_STEPS = 64
MAX_TOOL_CALLS_PER_PLAN = 48
MAX_TOOL_ARGUMENTS_BYTES = 8_192
# governance steps are never taken from the plan: the harness strips them and appends its own tail
GOVERNANCE_TYPES = (StepType.VALIDATION, StepType.FINALISE, StepType.HUMAN_APPROVAL)
RESEARCH_KIND = "research"
APPROVAL_STEP_ID = "gov-approval"
STAGE_APPROVAL_STEP_ID = "gov-stage-approval"
STAGE_STEP_ID = "gov-stage-orders"
STAGE_TOOL_ID = "execution.stage_orders"
HUMAN_APPROVAL_TOOL_ID = "harness.human_approval"
# task-input keys whose values a plan may use for the corresponding tool arguments
# (a list-valued task input, e.g. the research universe, admits any of its elements)
_PINNED_ARGS = {"symbol": ("symbol", "universe"), "dataset": ("dataset",), "signal": ("signal",)}
_WINDOW_ARGS = ("start", "end", "baseline_start", "baseline_end", "in_sample_end", "as_of")
_WINDOW_SOURCE_KEYS = (
    "window_start",
    "window_end",
    "baseline_start",
    "baseline_end",
    "start",
    "end",
    "in_sample_end",
    "as_of",
)


def task_kind(task: Task) -> str:
    return str(task.input.get("kind") or "investigation")


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
            "policy_log": to_jsonable(self.policy_log),
            "agent_outputs": to_jsonable(self.agent_outputs),
            "step_results": to_jsonable(self.step_results),
        }
        if include_trace:
            out["trace"] = to_jsonable(self.trace)
        return out

    @staticmethod
    def failed(task_id: str, error: str, started_at: str | None = None) -> HarnessResult:
        """A terminal FAILED result for failures that happen outside ``execute`` (e.g. setup)."""
        now = utc_now().isoformat()
        return HarnessResult(
            task_id=task_id,
            execution_id=new_id("RUN"),
            state=HarnessState.FAILED,
            plan=None,
            findings=(),
            evidence=(),
            report=None,
            error=error,
            trace={"trace_id": None, "spans": []},
            state_history=[{"from": "CREATED", "to": "FAILED", "reason": error, "at": now}],
            policy_log=[],
            agent_outputs={},
            step_results=[],
            duration_ms=0.0,
            started_at=started_at or now,
            finished_at=now,
        )


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

        pending_approvals = 0

        async def on_awaiting(entering: bool) -> None:
            # Count pending approvals so concurrent tool calls cannot flap the state.
            nonlocal pending_approvals
            if entering:
                pending_approvals += 1
                if pending_approvals == 1 and sm.state is HarnessState.EXECUTING:
                    sm.transition(HarnessState.AWAITING_APPROVAL, "tool requires human approval")
            else:
                pending_approvals = max(0, pending_approvals - 1)
                if pending_approvals == 0 and sm.state is HarnessState.AWAITING_APPROVAL:
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
                    self._absorb("planner", planner_result, memory, warnings)
                    step_results.append(_agent_record("plan", "planner", planner_result))

                # ---- plan validation -------------------------------------------
                sm.transition(HarnessState.VALIDATING_PLAN, "plan produced")
                async with self.tracer.span("harness.validate_plan", steps=len(plan.steps)):
                    plan, dropped_steps = self._enforce_governance_steps(plan, task)
                    for d in dropped_steps:
                        warnings.append(f"plan step dropped by governance: {d}")
                    await self._validate_plan(plan, policy_context, task)

                # ---- execution -------------------------------------------------
                sm.transition(HarnessState.EXECUTING, "plan validated")
                agents_since_critic = 0
                critic_ran = False
                declined: set[str] = set()  # optional approval steps a human declined
                for batch in _batches(plan.ordered_steps()):
                    cancellation.raise_if_cancelled()
                    if utc_now() > deadline:
                        raise TimeoutError("task deadline exceeded")
                    blocked = [s for s in batch if any(d in declined for d in s.depends_on)]
                    if blocked:
                        for step in blocked:
                            step_results.append(
                                {
                                    "step_id": step.id,
                                    "type": step.type.value,
                                    "status": "SKIPPED",
                                    "error": "skipped: a required approval was declined",
                                }
                            )
                            if step.id == STAGE_STEP_ID:
                                memory.scratch["staging"] = {
                                    "step_id": step.id,
                                    "status": "SKIPPED",
                                    "error": "order staging declined by the approver",
                                }
                        continue
                    kind = batch[0].type
                    if kind is StepType.TOOL_CALL:
                        results = await self._run_tool_batch(batch, executor, run)
                        for step, result in zip(batch, results, strict=True):
                            step_results.append(_step_record(step, result))
                            if not result.ok:
                                warnings.append(f"step {step.id} ({step.description}) failed: {result.error}")
                            if step.id == STAGE_STEP_ID:
                                memory.scratch["staging"] = {
                                    "step_id": step.id,
                                    "status": result.status.value,
                                    "error": result.error,
                                }
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
                        agent_result = await self._run_agent(agent, step.id, memory, run, executor, task)
                        self._absorb(agent_id, agent_result, memory, warnings)
                        step_results.append(_agent_record(step.id, agent_id, agent_result))
                        if not agent_result.success:
                            warnings.append(f"agent {agent_id} failed: {agent_result.error}")
                        if agent_id == CRITIC_AGENT_ID:
                            critic_ran, agents_since_critic = True, 0
                            sm.transition(HarnessState.EXECUTING, "critique complete")
                        else:
                            agents_since_critic += 1
                    elif step.type is StepType.HUMAN_APPROVAL:
                        approved = await self._human_gate(step, run, sm, warnings, memory)
                        step_results.append(
                            {
                                "step_id": step.id,
                                "type": step.type.value,
                                "status": "APPROVED" if approved else "REJECTED",
                            }
                        )
                        if not approved:
                            if step.metadata.get("optional"):
                                declined.add(step.id)  # dependants are skipped; the task completes
                            else:
                                raise PlanValidationError(
                                    f"human approval declined at step {step.id}: {step.description}"
                                )
                    elif step.type is StepType.VALIDATION:
                        if not critic_ran or agents_since_critic:  # governance: critique what was added
                            await self._force_critic(memory, run, executor, task, step_results, warnings)
                            critic_ran, agents_since_critic = True, 0
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
                        if not critic_ran or agents_since_critic:
                            await self._force_critic(memory, run, executor, task, step_results, warnings)
                            critic_ran, agents_since_critic = True, 0
                            self._validate_evidence(memory)
                        sm.transition(HarnessState.FINALISING, "finalise step")
                        rep_result = await self._run_agent(
                            self.reporter, step.id, memory, run, executor, task
                        )
                        self._absorb("reporter", rep_result, memory, warnings)
                        if not rep_result.success or "report" not in rep_result.output:
                            raise RuntimeError(rep_result.error or "reporter produced no report")
                        report = rep_result.output["report"]
                        step_results.append(
                            {"step_id": step.id, "type": step.type.value, "status": "SUCCESS"}
                        )
                    elif step.type is StepType.ANALYSIS:
                        step_results.append(
                            {
                                "step_id": step.id,
                                "type": step.type.value,
                                "status": "SKIPPED",
                                "error": "ANALYSIS steps are informational",
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
            leaf = _leaf_exception(exc)
            if isinstance(leaf, TaskCancelled):
                error = f"cancelled: {leaf}"
                sm.cancel(str(leaf))
                metrics.inc("ceap_investigations_cancelled_total")
            else:
                error = f"{type(leaf).__name__}: {leaf}"
                log.exception(
                    "investigation failed", extra={"task_id": task.id, "execution_id": execution_id}
                )
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
    async def _validate_plan(self, plan: Plan, policy_context: PolicyContext, task: Task) -> None:
        if not plan.steps:
            raise PlanValidationError("plan has no steps")
        if len(plan.steps) > MAX_PLAN_STEPS:
            raise PlanValidationError(f"plan has {len(plan.steps)} steps; maximum is {MAX_PLAN_STEPS}")
        tool_steps = [s for s in plan.steps if s.type is StepType.TOOL_CALL]
        if len(tool_steps) > MAX_TOOL_CALLS_PER_PLAN:
            raise PlanValidationError(
                f"plan has {len(tool_steps)} tool calls; maximum is {MAX_TOOL_CALLS_PER_PLAN}"
            )
        seq = [s.sequence for s in plan.ordered_steps()]
        if len(set(seq)) != len(seq):
            raise PlanValidationError("plan step sequences are not unique")
        ids = [s.id for s in plan.steps]
        if len(set(ids)) != len(ids):
            raise PlanValidationError("plan step ids are not unique")
        allowed_window_values = {
            str(task.input.get(k)) for k in _WINDOW_SOURCE_KEYS if task.input.get(k) is not None
        }
        for step in plan.steps:
            if step.type is StepType.TOOL_CALL:
                if step.tool_request is None:
                    raise PlanValidationError(f"step {step.id}: TOOL_CALL without tool_request")
                req = step.tool_request
                if not self.tools.has(req.tool_id):
                    raise PlanValidationError(f"step {step.id}: unknown tool {req.tool_id}")
                self._validate_arguments(step, req.arguments, task, allowed_window_values)
                meta = self.tools.get(req.tool_id).metadata
                try:
                    evaluation = await self.policy.evaluate(req, meta, policy_context)
                except Exception as exc:  # noqa: BLE001 - a rule crash must fail closed
                    raise PlanValidationError(
                        f"step {step.id}: policy evaluation failed ({type(exc).__name__}: {exc})"
                    ) from exc
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

    @staticmethod
    def _validate_arguments(
        step: PlanStep, arguments: dict[str, Any], task: Task, allowed_window_values: set[str]
    ) -> None:
        """Arguments that scope data access must be pinned to the task, and must be plain JSON scalars."""
        try:
            encoded = json.dumps(arguments, default=str)
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(f"step {step.id}: arguments are not JSON-serialisable ({exc})") from exc
        if len(encoded) > MAX_TOOL_ARGUMENTS_BYTES:
            raise PlanValidationError(f"step {step.id}: arguments exceed {MAX_TOOL_ARGUMENTS_BYTES} bytes")
        for key, value in arguments.items():
            if isinstance(value, (dict, list, tuple, set)):
                raise PlanValidationError(f"step {step.id}: argument {key} must be a scalar")
        for key, task_keys in _PINNED_ARGS.items():
            if key in arguments:
                expected: set[str] = set()
                for k in task_keys:
                    value = task.input.get(k)
                    if value is None:
                        continue
                    if isinstance(value, (list, tuple, set, frozenset)):
                        expected.update(str(v) for v in value)
                    else:
                        expected.add(str(value))
                if expected and str(arguments[key]) not in expected:
                    raise PlanValidationError(
                        f"step {step.id}: argument {key}={arguments[key]!r} is not pinned to the task ({sorted(expected)})"
                    )
        for key in _WINDOW_ARGS:
            if key in arguments and str(arguments[key]) not in allowed_window_values:
                raise PlanValidationError(
                    f"step {step.id}: argument {key}={arguments[key]!r} is not a task window boundary"
                )

    def _enforce_governance_steps(self, plan: Plan, task: Task) -> tuple[Plan, list[str]]:
        """Strip any critic / validation / approval / finalise steps the plan contains and append the
        harness-owned governance tail, so the critic always runs after every specialist and a human
        approval (for research tasks) always follows the critique."""
        steps = list(plan.ordered_steps())
        kept: list[PlanStep] = []
        dropped_steps: list[PlanStep] = []
        for s in steps:
            is_gov = s.type in GOVERNANCE_TYPES or (
                s.type is StepType.AGENT_CALL and s.agent_id == CRITIC_AGENT_ID
            )
            if is_gov:
                dropped_steps.append(s)
            else:
                kept.append(s)
        tail = self._governance_tail(task)
        merged = kept + tail
        resequenced = tuple(replace(s, sequence=i + 1) for i, s in enumerate(merged))
        # only report drops that were the canonical governance steps in canonical (trailing) position
        n = len(dropped_steps)
        canonical = (
            n > 0
            and steps[-n:] == dropped_steps
            and _is_subsequence([_signature(s) for s in dropped_steps], [_signature(s) for s in tail])
        )
        dropped = [
            f"{s.id} ({s.type.value}{'/' + s.agent_id if s.agent_id else ''}) - governance steps are appended by the harness"
            for s in dropped_steps
        ]
        return Plan(
            id=plan.id,
            task_id=plan.task_id,
            steps=resequenced,
            rationale=plan.rationale,
            generated_by=plan.generated_by,
        ), ([] if canonical else dropped)

    def _governance_tail(self, task: Task) -> list[PlanStep]:
        """critic -> validation [-> proposal approval [-> staging approval -> stage orders]] -> finalise."""
        tail: list[PlanStep] = []
        if CRITIC_AGENT_ID in self.agents:
            tail.append(
                PlanStep(
                    id="gov-critic",
                    sequence=0,
                    type=StepType.AGENT_CALL,
                    description="Governance: independent critique",
                    agent_id=CRITIC_AGENT_ID,
                )
            )
        tail.append(
            PlanStep(
                id="gov-validation",
                sequence=0,
                type=StepType.VALIDATION,
                description="Governance: validate evidence references",
            )
        )
        if task_kind(task) == RESEARCH_KIND:
            scope = {
                k: task.input.get(k)
                for k in ("kind", "dataset", "signal", "start", "end", "in_sample_end", "stage_orders")
            }
            tail.append(
                PlanStep(
                    id=APPROVAL_STEP_ID,
                    sequence=0,
                    type=StepType.HUMAN_APPROVAL,
                    description="Governance: human approval of the research proposal",
                    metadata=scope,
                )
            )
            if task.input.get("stage_orders"):
                arguments = research_tool_arguments(task.input)["stage_orders"]
                tail.append(
                    PlanStep(
                        id=STAGE_APPROVAL_STEP_ID,
                        sequence=0,
                        type=StepType.HUMAN_APPROVAL,
                        description="Governance: human approval to stage paper orders",
                        metadata={**scope, "optional": True, "tool_id": STAGE_TOOL_ID, "arguments": arguments},
                        depends_on=(APPROVAL_STEP_ID,),
                    )
                )
                tail.append(
                    PlanStep(
                        id=STAGE_STEP_ID,
                        sequence=0,
                        type=StepType.TOOL_CALL,
                        description="Stage paper orders for the approved proposal",
                        tool_request=ToolRequest(tool_id=STAGE_TOOL_ID, arguments=arguments),
                        depends_on=(STAGE_APPROVAL_STEP_ID,),
                    )
                )
        tail.append(
            PlanStep(
                id="gov-finalise",
                sequence=0,
                type=StepType.FINALISE,
                description="Governance: produce report",
            )
        )
        return tail

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
    def _absorb(agent_id: str, result: AgentResult, memory: InvestigationMemory, warnings: list[str]) -> None:
        memory.add_evidence(*result.evidence)
        if agent_id == CRITIC_AGENT_ID and "adjusted_findings" in result.output:
            memory.replace_findings(list(result.output["adjusted_findings"]))
        else:
            memory.add_findings(*result.findings)
        if agent_id == "planner" and "plan" in result.output:
            memory.scratch["plan"] = result.output["plan"]
        fallback = result.output.get("llm_fallback")
        if fallback:
            warnings.append(f"{agent_id}: LLM fallback used ({fallback})")
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
        warnings: list[str],
    ) -> AgentResult | None:
        critic = self.agents.get(CRITIC_AGENT_ID)
        if critic is None:
            warnings.append("no critic agent registered; findings are un-critiqued")
            return None
        result = await self._run_agent(critic, "gov-critic-forced", memory, run, executor, task)
        self._absorb(CRITIC_AGENT_ID, result, memory, warnings)
        step_results.append(_agent_record("gov-critic-forced", CRITIC_AGENT_ID, result))
        return result

    async def _human_gate(
        self,
        step: PlanStep,
        run: RunContext,
        sm: StateMachine,
        warnings: list[str],
        memory: InvestigationMemory,
    ) -> bool:
        request = ApprovalRequest.create(
            run.task_id,
            step.id,
            HUMAN_APPROVAL_TOOL_ID,
            _approval_payload(step, memory),
            step.description,
            run.policy_context.principal,
        )
        if sm.state is HarnessState.EXECUTING:
            sm.transition(HarnessState.AWAITING_APPROVAL, "human approval step")
        try:
            decision = await race_cancellation(self.approvals.request(request), run.cancellation)
        finally:
            if sm.state is HarnessState.AWAITING_APPROVAL:
                sm.transition(HarnessState.EXECUTING, "human approval decided")
        self.tracer.event(
            "approval.decision",
            request_id=request.id,
            approved=decision.approved,
            decided_by=decision.decided_by,
        )
        memory.scratch.setdefault("approvals", {})[step.id] = {
            "request_id": request.id,
            "step_id": step.id,
            "approved": decision.approved,
            "decided_by": decision.decided_by,
            "comment": decision.comment,
            "decided_at": decision.decided_at.isoformat(),
        }
        if not decision.approved:
            warnings.append(f"human approval declined at {step.id}: {decision.comment}")
        return decision.approved

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


def _signature(step: PlanStep) -> tuple[str, str | None]:
    tool_id = step.tool_request.tool_id if step.tool_request else None
    return step.type.value, step.agent_id or tool_id


def _is_subsequence(needle: list[Any], haystack: list[Any]) -> bool:
    it = iter(haystack)
    return all(any(item == candidate for candidate in it) for item in needle)


def _approval_payload(step: PlanStep, memory: InvestigationMemory) -> dict[str, Any]:
    """What the approver sees: the critic's assessment and the recomputed target portfolio."""
    payload: dict[str, Any] = dict(step.metadata)
    outputs = memory.agent_outputs
    assessment = (outputs.get(CRITIC_AGENT_ID) or {}).get("assessment") or {}
    exposure = (outputs.get(PORTFOLIO_RISK_AGENT_ID) or {}).get("exposure") or {}
    positions = exposure.get("positions") or []
    if step.id == APPROVAL_STEP_ID:
        payload["proposal"] = assessment
        payload["target_portfolio"] = {
            "as_of": exposure.get("as_of"),
            "names": len(positions),
            "gross": exposure.get("gross"),
            "net": exposure.get("net"),
            "max_abs_weight": exposure.get("max_abs_weight"),
            "beta": exposure.get("beta"),
            "max_adv_participation": exposure.get("max_adv_participation"),
        }
    elif step.id == STAGE_APPROVAL_STEP_ID:
        payload["proposal"] = {"verdict": assessment.get("verdict"), "flags": assessment.get("flags")}
        payload["orders_preview"] = [
            {
                "symbol": p.get("symbol"),
                "side": "BUY" if (p.get("quantity") or 0) > 0 else "SELL",
                "quantity": abs(int(p.get("quantity") or 0)),
                "notional": p.get("notional"),
            }
            for p in positions
        ]
    return payload


def _leaf_exception(exc: BaseException) -> BaseException:
    """Unwrap ExceptionGroups (from TaskGroup) to their first leaf exception."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


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


def _agent_record(step_id: str, agent_id: str, result: AgentResult) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "type": StepType.AGENT_CALL.value,
        "agent_id": agent_id,
        "status": "SUCCESS" if result.success else "ERROR",
        "error": result.error,
        "findings": len(result.findings),
        "summary": result.summary,
    }
