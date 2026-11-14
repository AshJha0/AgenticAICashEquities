"""The AgentHarness: a state machine that runs an investigation end to end.

    Task -> Planner -> Plan validation -> Step execution (tools / agents)
         -> Critic -> Evidence validation -> Finalise (report)

The harness controls the agents; agents never control the harness.
Governance steps (critic, evidence validation, finalisation) are enforced
by the harness: whatever the plan says, the critic runs *after* every
specialist agent and *before* validation and the report.

Since 0.9.0 (H6) the implementation is split into mixins - ``planning``, ``dispatch``,
``approvals``, ``evidence`` - plus ``common`` (constants, :class:`HarnessResult`, helpers).
This module keeps ``execute`` and is the stable import path: it re-exports everything above.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import timedelta
from typing import Any

from ceap.config import Settings, load_settings
from ceap.domain.agents import Agent
from ceap.domain.common import new_id, to_jsonable, utc_now
from ceap.domain.plans import Plan, PlanValidationError, StepType
from ceap.domain.policy import PolicyContext, PolicyEngine
from ceap.domain.reports import InvestigationReport
from ceap.domain.tasks import ResultWarning, Task, WarningSeverity
from ceap.domain.tools import ToolRegistry
from ceap.harness.approvals import ApprovalsMixin
from ceap.harness.cancellation import CancellationToken, TaskCancelled
from ceap.harness.common import (  # noqa: F401 - re-exported, engine is the facade
    _PINNED_ARGS,
    _WINDOW_ARGS,
    _WINDOW_SOURCE_KEYS,
    APPROVAL_STEP_ID,
    APPROVED_PORTFOLIO_HASH,
    CRITIC_AGENT_ID,
    DATA_STEP_PREFIX,
    DISPUTE_CAP,
    GOVERNANCE_TYPES,
    HUMAN_APPROVAL_TOOL_ID,
    KILL_SWITCH_CODE,
    MAX_DISPUTE_CHECKS,
    MAX_PARALLEL_TOOL_CALLS,
    MAX_PLAN_STEPS,
    MAX_REPLANS,
    MAX_TOOL_ARGUMENTS_BYTES,
    MAX_TOOL_CALLS_PER_PLAN,
    PORTFOLIO_HASH_KEY,
    PORTFOLIO_RISK_AGENT_ID,
    REQUIRED_INVESTIGATION_AGENTS,
    RESEARCH_KIND,
    SIGNOFF_STEP_ID,
    STAGE_APPROVAL_STEP_ID,
    STAGE_STEP_ID,
    STAGE_TOOL_ID,
    HarnessResult,
    _agent_record,
    _approval_payload,
    _batches,
    _bind_approved_portfolio,
    _is_subsequence,
    _leaf_exception,
    _message,
    _partition,
    _prompt_refs,
    _same_request,
    _signature,
    _step_record,
    _w,
    task_kind,
)
from ceap.harness.dispatch import DispatchMixin
from ceap.harness.durable import RunJournal, id_scope, scope_seed
from ceap.harness.evidence import EvidenceMixin
from ceap.harness.executor import RunContext, StepExecutor
from ceap.harness.memory import (
    CaseMemory,
    InvestigationMemory,
)
from ceap.harness.planning import PlanningMixin
from ceap.harness.retry import RetryPolicy
from ceap.harness.state_machine import HarnessState, StateMachine
from ceap.llm.router import task_cost, task_scope
from ceap.observability.metrics import metrics
from ceap.observability.tracing import ExecutionTracer, InMemoryTracer
from ceap.policy.approvals import (
    ApprovalGateway,
    DenyApprovalGateway,
)

log = logging.getLogger(__name__)


class AgentHarness(PlanningMixin, DispatchMixin, ApprovalsMixin, EvidenceMixin):
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
        case_memory: CaseMemory | None = None,
        journal: RunJournal | None = None,
    ) -> None:
        # G5: step-boundary journal; with one, re-running a task id resumes instead of repeating
        self.journal = journal
        self.planner = planner
        # R3B 3B.7 (D10): None or disabled in evaluation; written only here, after a human decision
        self.case_memory = case_memory
        self.agents = agents
        self.tools = tools
        self.policy = policy
        self.reporter = reporter
        self.tracer = tracer or InMemoryTracer()
        # fail closed: no gateway means every approval is denied (PLAN 1.4); auto-approval is opt-in
        self.approvals = approvals or DenyApprovalGateway()
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
            journal=self.journal,
        )
        plan: Plan | None = None
        report: InvestigationReport | None = None
        error: str | None = None
        warnings: list[ResultWarning] = []
        step_results: list[dict[str, Any]] = []
        metrics.inc("ceap_investigations_total")
        # R3A 3A.6: every model call made by this run is attributed to this task (context variable,
        # so concurrent tasks on one router never share a cost meter)
        llm_scope = task_scope(task.id)
        llm_scope.__enter__()
        # H1: with a journal, ids minted at run level derive from the task id, so a resumed run
        # reproduces them (the execution id above stays fresh: each attempt is its own execution)
        ids = id_scope(scope_seed(task.id, "run") if self.journal is not None else None)
        ids.__enter__()

        try:
            async with self.tracer.span("harness.execute", task_id=task.id, execution_id=execution_id):
                # ---- planning ---------------------------------------------------
                sm.transition(HarnessState.PLANNING, "task accepted")
                memory.scratch["cases"] = self._recall_cases(task, memory)
                async with self.tracer.span("harness.plan"):
                    planner_result = await self._run_agent(self.planner, "plan", memory, run, executor, task)
                    if not planner_result.success or "plan" not in planner_result.output:
                        raise PlanValidationError(planner_result.error or "planner produced no plan")
                    plan = planner_result.output["plan"]
                    self._absorb("planner", planner_result, memory, warnings)
                    step_results.append(_agent_record("plan", "planner", planner_result))
                    run_task = self._apply_scope(
                        task, planner_result.output.get("scope") or {}, memory, warnings
                    )

                # ---- plan validation -------------------------------------------
                sm.transition(HarnessState.VALIDATING_PLAN, "plan produced")
                async with self.tracer.span("harness.validate_plan", steps=len(plan.steps)):
                    plan, dropped_steps = self._enforce_governance_steps(plan, run_task)
                    for d in dropped_steps:
                        warnings.append(
                            _w("PLAN_STEP_DROPPED", f"plan step dropped by governance: {d}", info=True)
                        )
                    await self._validate_plan(plan, policy_context, run_task, original=task)

                # ---- execution -------------------------------------------------
                sm.transition(HarnessState.EXECUTING, "plan validated")
                agents_since_critic = 0
                critic_ran = False
                replans = 0
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
                        batch = [_bind_approved_portfolio(s, memory) for s in batch]
                        results = await self._run_tool_batch(batch, executor, run)
                        failed: list[str] = []
                        for step, result in zip(batch, results, strict=True):
                            step_results.append(_step_record(step, result))
                            if not result.ok:
                                failed.append(
                                    f"{step.id} {step.tool_request.tool_id if step.tool_request else ''}: {result.error}"
                                )
                                warnings.append(
                                    _w(
                                        "TOOL_STEP_FAILED",
                                        f"step {step.id} ({step.description}) failed: {result.error}",
                                    )
                                )
                            if step.id == STAGE_STEP_ID:
                                memory.scratch["staging"] = {
                                    "step_id": step.id,
                                    "status": result.status.value,
                                    "error": result.error,
                                }
                        if failed and replans < MAX_REPLANS and not any(s.id == STAGE_STEP_ID for s in batch):
                            replans += 1
                            await self._replan(
                                "tool step(s) failed: " + "; ".join(failed)[:800],
                                memory,
                                run,
                                executor,
                                run_task,
                                policy_context,
                                step_results,
                                warnings,
                            )
                        continue
                    step = batch[0]
                    if step.type is StepType.AGENT_CALL:
                        agent_id = step.agent_id or ""
                        agent = self.agents.get(agent_id)
                        if agent is None:
                            warnings.append(_w("UNKNOWN_AGENT", f"step {step.id}: unknown agent {agent_id}"))
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
                        agent_result = await self._run_agent(agent, step.id, memory, run, executor, run_task)
                        required_failure = (
                            not agent_result.success
                            and agent_id in REQUIRED_INVESTIGATION_AGENTS
                            and task_kind(task) != RESEARCH_KIND
                        )
                        if required_failure and replans < MAX_REPLANS:
                            # R3B 3B.6: one bounded re-plan with the failure reason, then one re-run
                            replans += 1
                            step_results.append(_agent_record(step.id, agent_id, agent_result))
                            await self._replan(
                                f"agent {agent_id} failed: {agent_result.error}",
                                memory,
                                run,
                                executor,
                                run_task,
                                policy_context,
                                step_results,
                                warnings,
                            )
                            agent_result = await self._run_agent(
                                agent, f"{step.id}-replanned", memory, run, executor, run_task
                            )
                        self._absorb(agent_id, agent_result, memory, warnings)
                        step_results.append(_agent_record(step.id, agent_id, agent_result))
                        if not agent_result.success:
                            required = (
                                agent_id in REQUIRED_INVESTIGATION_AGENTS and task_kind(task) != RESEARCH_KIND
                            )
                            warnings.append(
                                _w(
                                    "REQUIRED_AGENT_FAILED" if required else "AGENT_FAILED",
                                    f"agent {agent_id} failed: {agent_result.error}",
                                    source=agent_id,
                                    severity=WarningSeverity.ERROR,
                                )
                            )
                        if agent_id == CRITIC_AGENT_ID:
                            await self._resolve_disputes(
                                memory, run, executor, run_task, step_results, warnings
                            )
                            critic_ran, agents_since_critic = True, 0
                            sm.mark_critiqued()
                            sm.transition(HarnessState.EXECUTING, "critique complete")
                        else:
                            agents_since_critic += 1
                            sm.mark_new_work()
                    elif step.type is StepType.HUMAN_APPROVAL:
                        self._hypotheses(memory)
                        approved = await self._human_gate(step, run, sm, warnings, memory, task)
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
                            await self._force_critic(memory, run, executor, run_task, step_results, warnings)
                            critic_ran, agents_since_critic = True, 0
                            sm.mark_critiqued()
                        sm.transition(HarnessState.VALIDATING_EVIDENCE, "validation step")
                        dropped = self._validate_evidence(memory, warnings)
                        sm.mark_evidence_validated()
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
                            await self._force_critic(memory, run, executor, run_task, step_results, warnings)
                            critic_ran, agents_since_critic = True, 0
                            sm.mark_critiqued()
                        if not sm.evidence_validated:  # AI-2-11: never finalise unvalidated evidence
                            sm.transition(HarnessState.VALIDATING_EVIDENCE, "evidence check before finalising")
                            self._validate_evidence(memory, warnings)
                            sm.mark_evidence_validated()
                            sm.transition(HarnessState.EXECUTING, "evidence validated")
                        sm.transition(HarnessState.FINALISING, "finalise step")
                        table = self._hypotheses(memory)
                        rep_result = await self._run_agent(
                            self.reporter, step.id, memory, run, executor, run_task
                        )
                        self._absorb("reporter", rep_result, memory, warnings)
                        if not rep_result.success or "report" not in rep_result.output:
                            raise RuntimeError(rep_result.error or "reporter produced no report")
                        report = replace(rep_result.output["report"], prompts=_prompt_refs(memory))
                        if report.hypotheses is None and task_kind(task) != RESEARCH_KIND:
                            report = replace(report, hypotheses=table)
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
                    else:  # AI-2-11: an unknown step type fails the run instead of being skipped
                        raise PlanValidationError(f"step {step.id}: unsupported step type {step.type!r}")

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

        ids.__exit__(None, None, None)
        llm_scope.__exit__(None, None, None)
        cost = task_cost(task.id)  # R3A 3A.6: per-task cost span (local estimate, labelled)
        if cost.calls:
            metrics.observe("ceap_llm_task_cost_usd_estimate", cost.usd)
            metrics.inc("ceap_llm_calls_total", float(cost.calls))
            memory.agent_outputs["llm"] = {
                "usd_estimate": round(cost.usd, 6),
                "usd_is_estimate": True,
                "calls": cost.calls,
                "records": [to_jsonable(r) for r in cost.records],
            }
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
            incomplete_agents=self._incomplete_agents(task, memory),
        )
