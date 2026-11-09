"""Plan validation, governance tail, scoping and bounded re-planning.

Split out of ``ceap.harness.engine`` in 0.9.0 (H6); ``engine`` re-exports the public API.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any

from ceap.agents.requirements import requirements_for
from ceap.domain.plans import Plan, PlanStep, PlanValidationError, StepType
from ceap.domain.policy import PolicyContext, PolicyDecision
from ceap.domain.research import research_tool_arguments
from ceap.domain.tasks import ResultWarning, Task
from ceap.domain.tools import ToolRequest
from ceap.harness.base import HarnessBase
from ceap.harness.common import (
    _PINNED_ARGS,
    _WINDOW_ARGS,
    _WINDOW_SOURCE_KEYS,
    APPROVAL_STEP_ID,
    CRITIC_AGENT_ID,
    DATA_STEP_PREFIX,
    GOVERNANCE_TYPES,
    MAX_PLAN_STEPS,
    MAX_TOOL_ARGUMENTS_BYTES,
    MAX_TOOL_CALLS_PER_PLAN,
    REQUIRED_INVESTIGATION_AGENTS,
    RESEARCH_KIND,
    SIGNOFF_STEP_ID,
    STAGE_APPROVAL_STEP_ID,
    STAGE_STEP_ID,
    STAGE_TOOL_ID,
    _agent_record,
    _is_subsequence,
    _same_request,
    _signature,
    _step_record,
    _w,
    task_kind,
)
from ceap.harness.executor import RunContext, StepExecutor
from ceap.harness.memory import (
    InvestigationMemory,
)

log = logging.getLogger("ceap.harness.engine")


class PlanningMixin(HarnessBase):
    """Plan validation, governance tail, scoping and bounded re-planning."""

    async def _validate_plan(
        self, plan: Plan, policy_context: PolicyContext, task: Task, original: Task | None = None
    ) -> None:
        if not plan.steps:
            raise PlanValidationError("plan has no steps")
        if len(plan.steps) > MAX_PLAN_STEPS:
            raise PlanValidationError(f"plan has {len(plan.steps)} steps; maximum is {MAX_PLAN_STEPS}")
        # the cap bounds what the model asked for; harness data steps (3B.1) are bounded by the agents
        tool_steps = [
            s for s in plan.steps if s.type is StepType.TOOL_CALL and not s.id.startswith(DATA_STEP_PREFIX)
        ]
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
            str(t.input.get(k))
            for t in (task, original or task)
            for k in _WINDOW_SOURCE_KEYS
            if t.input.get(k) is not None
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
        if task_kind(task) != RESEARCH_KIND:
            present = {s.agent_id for s in kept if s.type is StepType.AGENT_CALL}
            for agent_id in REQUIRED_INVESTIGATION_AGENTS:
                if agent_id in self.agents and agent_id not in present:
                    kept.append(
                        PlanStep(
                            id=f"gov-required-{agent_id}",
                            sequence=0,
                            type=StepType.AGENT_CALL,
                            description=f"Governance: required specialist {agent_id} (omitted by the plan)",
                            agent_id=agent_id,
                        )
                    )
        kept = self._with_data_steps(kept, task)
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
                        metadata={
                            **scope,
                            "optional": True,
                            "tool_id": STAGE_TOOL_ID,
                            "arguments": arguments,
                        },
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
        if task_kind(task) != RESEARCH_KIND and task.input.get("signoff"):
            tail.append(
                PlanStep(
                    id=SIGNOFF_STEP_ID,
                    sequence=0,
                    type=StepType.HUMAN_APPROVAL,
                    description="Governance: human sign-off of the investigation findings",
                    metadata={
                        "optional": True,
                        **{
                            k: task.input.get(k)
                            for k in ("kind", "symbol", "dataset", "window_start", "window_end")
                        },
                    },
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

    @staticmethod
    def _with_data_steps(kept: list[PlanStep], task: Task) -> list[PlanStep]:
        """R3B 3B.1: insert, before the first agent step, every tool call a chosen agent reads that
        the plan did not schedule with exactly those arguments. Agents never fetch for themselves."""
        chosen = [s.agent_id for s in kept if s.type is StepType.AGENT_CALL and s.agent_id]
        scheduled = [s.tool_request for s in kept if s.tool_request is not None]
        added: list[PlanStep] = []
        for agent_id in dict.fromkeys(chosen):
            for request in requirements_for(agent_id, task.input):
                if any(_same_request(request, r) for r in scheduled):
                    continue
                scheduled.append(request)
                added.append(
                    PlanStep(
                        id=f"{DATA_STEP_PREFIX}{len(added) + 1:02d}",
                        sequence=0,
                        type=StepType.TOOL_CALL,
                        description=f"Governance: data read by the {agent_id} agent ({request.tool_id})",
                        tool_request=request,
                    )
                )
        if not added:
            return kept
        first_agent = next((i for i, s in enumerate(kept) if s.type is StepType.AGENT_CALL), len(kept))
        return kept[:first_agent] + added + kept[first_agent:]

    def _apply_scope(
        self, task: Task, scope: dict[str, Any], memory: InvestigationMemory, warnings: list[ResultWarning]
    ) -> Task:
        """R3B 3B.1: apply the plan's enumerated choices. The baseline choice replaces the baseline
        window every agent reads; each distinct choice is recorded as a trial."""
        record = dict(scope)
        for problem in scope.get("rejected") or ():
            warnings.append(_w("PLAN_SCOPE_REJECTED", f"planner: {problem}", source="planner", info=True))
        cited = list(scope.get("context_cases") or [])
        if "case_memory" in memory.agent_outputs:
            memory.agent_outputs["case_memory"]["cited"] = cited
        window = scope.get("baseline_window") or []
        effective = task
        if task_kind(task) != RESEARCH_KIND and len(window) == 2 and scope.get("baseline") != "task_baseline":
            effective = replace(
                task, input={**task.input, "baseline_start": window[0], "baseline_end": window[1]}
            )
        trials = memory.scratch.setdefault("trials", [])
        choice = {"baseline": scope.get("baseline"), "hypotheses": list(scope.get("hypotheses") or [])}
        if choice not in [t["choice"] for t in trials]:
            trials.append({"trial": len(trials) + 1, "choice": choice})
        record["trials"] = list(trials)
        memory.agent_outputs["scope"] = record
        return effective

    async def _replan(
        self,
        reason: str,
        memory: InvestigationMemory,
        run: RunContext,
        executor: StepExecutor,
        task: Task,
        policy_context: PolicyContext,
        step_results: list[dict[str, Any]],
        warnings: list[ResultWarning],
    ) -> None:
        """R3B 3B.6: ask the planner once more, with the failure reason, then run the tool steps of the
        new plan that are not satisfied yet (validated and policy-checked like the first plan)."""
        warnings.append(_w("REPLANNED", f"one re-plan attempt: {reason[:300]}", info=True))
        memory.scratch["replan"] = {"reason": reason[:2000]}
        result = await self._run_agent(self.planner, "replan", memory, run, executor, task)
        memory.scratch.pop("replan", None)
        step_results.append(_agent_record("replan", "planner", result))
        new_plan = result.output.get("plan") if result.success else None
        if not isinstance(new_plan, Plan):
            warnings.append(_w("REPLAN_FAILED", f"re-plan produced no plan: {result.error}"))
            return
        new_plan, _ = self._enforce_governance_steps(new_plan, task)
        pending = [
            replace(s, id=f"replan-{s.id}")
            for s in new_plan.ordered_steps()
            if s.tool_request is not None
            and s.tool_request.tool_id != STAGE_TOOL_ID
            and memory.find_exact(s.tool_request.tool_id, dict(s.tool_request.arguments)) is None
        ]
        if not pending:
            return
        try:
            await self._validate_plan(replace(new_plan, steps=tuple(pending)), policy_context, task)
        except PlanValidationError as exc:
            warnings.append(_w("REPLAN_REJECTED", f"re-plan rejected by validation: {exc}"))
            return
        results = await self._run_tool_batch(pending, executor, run)
        for step, res in zip(pending, results, strict=True):
            step_results.append(_step_record(step, res))
            if not res.ok:
                warnings.append(_w("TOOL_STEP_FAILED", f"re-planned step {step.id} failed: {res.error}"))
