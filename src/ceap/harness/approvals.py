"""Human approval gates and staging grants.

Split out of ``ceap.harness.engine`` in 0.9.0 (H6); ``engine`` re-exports the public API.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from ceap.domain.plans import PlanStep
from ceap.domain.tasks import ResultWarning, Task, WarningSeverity
from ceap.harness.base import HarnessBase
from ceap.harness.common import (
    APPROVAL_STEP_ID,
    APPROVED_PORTFOLIO_HASH,
    HUMAN_APPROVAL_TOOL_ID,
    PORTFOLIO_HASH_KEY,
    SIGNOFF_STEP_ID,
    STAGE_APPROVAL_STEP_ID,
    _approval_payload,
    _w,
)
from ceap.harness.durable import APPROVAL_KIND, approval_key, stable_content_hash
from ceap.harness.executor import RunContext, race_cancellation
from ceap.harness.memory import (
    InvestigationMemory,
)
from ceap.harness.state_machine import HarnessState, StateMachine
from ceap.policy.approvals import (
    DISTINCT_FROM_KEY,
    SYSTEM_ACTORS,
    ApprovalDecision,
    ApprovalRequest,
    FourEyesViolationError,
    check_four_eyes,
)

log = logging.getLogger("ceap.harness.engine")


class ApprovalsMixin(HarnessBase):
    """Human approval gates and staging grants."""

    async def _human_gate(
        self,
        step: PlanStep,
        run: RunContext,
        sm: StateMachine,
        warnings: list[ResultWarning],
        memory: InvestigationMemory,
        task: Task | None = None,
    ) -> bool:
        payload = _approval_payload(step, memory)
        if step.id == STAGE_APPROVAL_STEP_ID:
            # four-eyes: whoever approved the proposal may not also approve staging (T17)
            prior = (memory.scratch.get("approvals") or {}).get(APPROVAL_STEP_ID) or {}
            if prior.get("decided_by") and prior["decided_by"] not in SYSTEM_ACTORS:
                payload[DISTINCT_FROM_KEY] = [prior["decided_by"]]
        request = ApprovalRequest.create(
            run.task_id,
            step.id,
            HUMAN_APPROVAL_TOOL_ID,
            payload,
            step.description,
            run.policy_context.principal,
        )
        journal = getattr(self, "journal", None)
        # R-11: keyed on stable content, so fresh finding or evidence ids do not make it a new question
        akey = approval_key(step.id, stable_content_hash(request.tool_id, dict(request.arguments)))
        prior = journal.get(run.task_id, akey) if journal is not None else None
        if prior is not None:  # G5: resumed run - this exact content was already decided
            decision = ApprovalDecision(
                request.id,
                bool(prior["payload"]["approved"]),
                str(prior["payload"]["decided_by"]),
                str(prior["payload"].get("comment") or ""),
            )
            self.tracer.event("approval.resumed", step_id=step.id, content_hash=request.content_hash)
        else:
            if sm.state is HarnessState.EXECUTING:
                sm.transition(HarnessState.AWAITING_APPROVAL, "human approval step")
            try:
                with run.clock.waiting():  # H6 (AI-2-08): the task clock stops while a human decides
                    decision = await race_cancellation(self.approvals.request(request), run.cancellation)
            finally:
                if sm.state is HarnessState.AWAITING_APPROVAL:
                    sm.transition(HarnessState.EXECUTING, "human approval decided")
            if journal is not None and decision.decided_by != "timeout":
                journal.put(
                    run.task_id,
                    akey,
                    APPROVAL_KIND,
                    step.id,
                    {
                        "approved": decision.approved,
                        "decided_by": decision.decided_by,
                        "comment": decision.comment,
                    },
                )
        if decision.approved:
            try:  # defence in depth: enforce four-eyes whatever gateway implementation decided
                check_four_eyes(request, decision.decided_by)
            except FourEyesViolationError as exc:
                warnings.append(_w("FOUR_EYES_VIOLATION", str(exc), severity=WarningSeverity.ERROR))
                decision = replace(decision, approved=False, comment=f"rejected by harness: {exc}")
        self.tracer.event(
            "approval.decision",
            system_actor=decision.decided_by in SYSTEM_ACTORS,
            request_id=request.id,
            approved=decision.approved,
            decided_by=decision.decided_by,
        )
        memory.scratch.setdefault("approvals", {})[step.id] = {
            "request_id": request.id,
            "step_id": step.id,
            "approved": decision.approved,
            "decided_by": decision.decided_by,
            "system_actor": decision.decided_by in SYSTEM_ACTORS,
            "comment": decision.comment,
            "decided_at": decision.decided_at.isoformat(),
        }
        if task is not None and step.id in (APPROVAL_STEP_ID, SIGNOFF_STEP_ID):
            self._record_case(task, step, decision, memory)
        if not decision.approved:
            warnings.append(
                _w(
                    "APPROVAL_DECLINED",
                    f"human approval declined at {step.id}: {decision.comment}",
                    info=True,
                )
            )
        elif step.id == STAGE_APPROVAL_STEP_ID and request.arguments.get(PORTFOLIO_HASH_KEY):
            # R4 4.5: staging must stage exactly the portfolio this approver saw
            memory.scratch[APPROVED_PORTFOLIO_HASH] = request.arguments[PORTFOLIO_HASH_KEY]
        if decision.approved and step.id == STAGE_APPROVAL_STEP_ID:
            self._grant_staging(run, step, memory)
        return decision.approved

    def _grant_staging(self, run: RunContext, step: PlanStep, memory: InvestigationMemory) -> None:
        """A4: the four-eyes staging approval is the human approval of the HIGH-risk staging call.

        The grant covers exactly the arguments the staging step will send (those in the approval
        payload plus the bound portfolio hash), once, for this task; anything else asks again.
        """
        grant = getattr(self.policy, "grant_approved", None)
        tool_id, arguments = step.metadata.get("tool_id"), step.metadata.get("arguments")
        if grant is None or not isinstance(tool_id, str) or not isinstance(arguments, dict):
            return
        final = dict(arguments)
        if memory.scratch.get(APPROVED_PORTFOLIO_HASH):
            final[APPROVED_PORTFOLIO_HASH] = memory.scratch[APPROVED_PORTFOLIO_HASH]
        grant(run.task_id, tool_id, final)
