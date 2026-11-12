"""Shared constants, the HarnessResult record and module-level helpers of the harness.

Split out of ``ceap.harness.engine`` in 0.9.0 (H6); ``engine`` re-exports the public API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

from ceap.domain.agents import AgentMessage, AgentResult
from ceap.domain.common import new_id, to_jsonable, utc_now
from ceap.domain.evidence import Evidence
from ceap.domain.findings import Finding
from ceap.domain.plans import Plan, PlanStep, StepType
from ceap.domain.reports import InvestigationReport
from ceap.domain.tasks import ResultWarning, Task, TaskStatus, WarningSeverity
from ceap.domain.tools import ToolRequest, ToolResult
from ceap.harness.memory import (
    InvestigationMemory,
)
from ceap.harness.state_machine import HarnessState
from ceap.policy.approvals import (
    review_payload,
)

log = logging.getLogger("ceap.harness.engine")

CRITIC_AGENT_ID = "critic"
#: specialists every execution investigation runs, appended by the harness if the plan omits them
REQUIRED_INVESTIGATION_AGENTS = ("market", "execution", "quant", "risk", "engineering")
KILL_SWITCH_CODE = "LLM_DISABLED"
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
SIGNOFF_STEP_ID = "gov-signoff"  # R3B 3B.7: optional human sign-off of an investigation
DATA_STEP_PREFIX = "gov-data-"  # R3B 3B.1: data steps the harness adds for the chosen agents
MAX_REPLANS = 1  # R3B 3B.6: one bounded re-plan on a tool or required-agent failure
MAX_DISPUTE_CHECKS = 3  # R3B 3B.3: alternative checks run per task (one tool call each)
DISPUTE_CAP = 0.3  # confidence of a finding overturned by a contradicting deterministic check
HUMAN_APPROVAL_TOOL_ID = "harness.human_approval"
#: scratch key for an optional challenger plan ``{"plan": <raw plan dict>, "model": str}`` (0.9.0 B7)
CHALLENGER_PLAN_KEY = "challenger_plan"
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
    warnings: list[ResultWarning] = field(default_factory=list)
    incomplete_agents: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.state is HarnessState.COMPLETED and self.report is not None

    @property
    def status(self) -> TaskStatus:
        """Outcome as the API reports it: a fallback never reads as a clean ``COMPLETED``."""
        if self.state is HarnessState.CANCELLED:
            return TaskStatus.CANCELLED
        if not self.success:
            return TaskStatus.FAILED
        if self.incomplete_agents:
            return TaskStatus.INCOMPLETE_ANALYSIS
        if any(w.severity is not WarningSeverity.INFO for w in self.warnings):
            return TaskStatus.DEGRADED
        return TaskStatus.COMPLETED

    @property
    def banner(self) -> str | None:
        if any(w.code == KILL_SWITCH_CODE for w in self.warnings):
            return "LLM DISABLED: every model step ran its deterministic fallback (CEAP_LLM_DISABLED=true)"
        return None

    def warning_messages(self) -> list[str]:
        return [w.message for w in self.warnings]

    def to_dict(self, include_trace: bool = False) -> dict[str, Any]:
        out = {
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "state": self.state.value,
            "status": self.status.value,
            "success": self.success,
            "error": self.error,
            "banner": self.banner,
            "incomplete_agents": self.incomplete_agents,
            "warnings": [to_jsonable(w) for w in self.warnings],
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


def _w(
    code: str,
    message: str,
    *,
    source: str = "harness",
    severity: WarningSeverity = WarningSeverity.DEGRADED,
    info: bool = False,
) -> ResultWarning:
    return ResultWarning(
        code=code, message=message, source=source, severity=WarningSeverity.INFO if info else severity
    )


def _same_request(a: ToolRequest, b: ToolRequest) -> bool:
    drop = lambda d: {k: v for k, v in d.items() if v is not None}  # noqa: E731
    return a.tool_id == b.tool_id and drop(dict(a.arguments)) == drop(dict(b.arguments))


def _partition(task: Task) -> str:
    """Case memory partition: the task's dataset (D10: a lookup never crosses datasets)."""
    return str(task.input.get("dataset") or "default")


def _message(agent_id: str, result: AgentResult) -> AgentMessage:
    """R3B 3B.6: the typed message the harness builds from an agent's result."""
    data = {k: v for k, v in result.output.items() if k not in ("adjusted_findings", "plan", "report")}
    return AgentMessage(
        sender=agent_id,
        success=result.success,
        summary=result.summary[:1000],
        error=result.error,
        finding_ids=tuple(f.id for f in result.findings),
        evidence_ids=tuple(e.id for e in result.evidence),
        data=to_jsonable(data),
    )


def _signature(step: PlanStep) -> tuple[str, str | None]:
    tool_id = step.tool_request.tool_id if step.tool_request else None
    return step.type.value, step.agent_id or tool_id


def _is_subsequence(needle: list[Any], haystack: list[Any]) -> bool:
    it = iter(haystack)
    return all(any(item == candidate for candidate in it) for item in needle)


PORTFOLIO_HASH_KEY = "portfolio_hash"
APPROVED_PORTFOLIO_HASH = "approved_portfolio_hash"


def _bind_approved_portfolio(step: PlanStep, memory: InvestigationMemory) -> PlanStep:
    """Attach the approved portfolio hash to the staging call so the tool can refuse a changed portfolio."""
    approved = memory.scratch.get(APPROVED_PORTFOLIO_HASH)
    if step.id != STAGE_STEP_ID or not approved or step.tool_request is None:
        return step
    request = replace(
        step.tool_request, arguments={**step.tool_request.arguments, APPROVED_PORTFOLIO_HASH: approved}
    )
    return replace(step, tool_request=request)


def _approval_payload(step: PlanStep, memory: InvestigationMemory) -> dict[str, Any]:
    """What the approver sees: the critic's assessment and the recomputed target portfolio."""
    payload: dict[str, Any] = dict(step.metadata)
    outputs = memory.agent_outputs
    payload["review"] = review_payload(
        hypotheses=outputs.get("hypotheses"),
        findings=memory.findings,
        evidence=memory.evidence,
        execution=outputs.get("execution"),
        critique=(outputs.get(CRITIC_AGENT_ID) or {}).get("critique"),
        disputes=outputs.get("disputes"),
    )
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
        from ceap.mcp.execution.staging import portfolio_hash

        payload["proposal"] = {"verdict": assessment.get("verdict"), "flags": assessment.get("flags")}
        payload[PORTFOLIO_HASH_KEY] = portfolio_hash(
            exposure.get("as_of"), ((p.get("symbol"), p.get("quantity") or 0) for p in positions)
        )
        payload["orders_preview"] = [
            {
                "symbol": p.get("symbol"),
                "side": "BUY" if (p.get("quantity") or 0) > 0 else "SELL",
                "quantity": abs(int(p.get("quantity") or 0)),
                "notional": p.get("notional"),
            }
            for p in positions
        ]
    challenger = memory.scratch.get(CHALLENGER_PLAN_KEY)
    if challenger:
        # 0.9.0 B7, off by default: only when a challenger model planned the same task does the
        # approver get the advisory agreement block; the challenger never changes the plan.
        from ceap.eval.challenger import approver_challenger_view

        payload["challenger"] = approver_challenger_view(
            _plan_as_dict(memory.scratch.get("plan")),
            challenger.get("plan"),
            str(challenger.get("model") or ""),
        )
    return payload


def _plan_as_dict(plan: Any) -> dict[str, Any] | None:
    """The primary plan in the raw shape :func:`ceap.eval.challenger.tool_sequence` reads."""
    if plan is None or isinstance(plan, dict):
        return plan
    return {
        "steps": [
            {"type": "TOOL_CALL", "tool_id": s.tool_request.tool_id}
            for s in plan.steps
            if s.type is StepType.TOOL_CALL and s.tool_request is not None
        ]
    }


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
        "status": "SUCCESS" if result.success else ("PARTIAL" if result.output.get("partial") else "ERROR"),
        "error": result.error,
        "findings": len(result.findings),
        "summary": result.summary,
    }


def _prompt_refs(memory: InvestigationMemory) -> dict[str, dict[str, str]]:
    """R3A 3A.7: prompt id -> {version, sha256} of every prompt used by this run."""
    refs: dict[str, dict[str, str]] = {}
    for out in memory.agent_outputs.values():
        ref = out.get("prompt") if isinstance(out, dict) else None
        if isinstance(ref, dict) and "id" in ref:
            refs[str(ref["id"])] = {"version": str(ref.get("version")), "sha256": str(ref.get("sha256"))}
    return refs
