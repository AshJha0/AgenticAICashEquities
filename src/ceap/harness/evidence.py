"""Evidence validation, dispute resolution, hypotheses and case memory.

Split out of ``ceap.harness.engine`` in 0.9.0 (H6); ``engine`` re-exports the public API.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from ceap.analytics.hypotheses import (
    check_plan,
    evaluate_hypotheses,
    hypothesis_table_dict,
    judge_alternative,
)
from ceap.domain.common import new_id, utc_now
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.findings import Finding
from ceap.domain.plans import PlanStep, StepType
from ceap.domain.tasks import ResultWarning, Task
from ceap.domain.tools import ToolRequest
from ceap.harness.common import (
    CRITIC_AGENT_ID,
    DISPUTE_CAP,
    MAX_DISPUTE_CHECKS,
    _partition,
    _step_record,
    _w,
    task_kind,
)
from ceap.harness.executor import RunContext, StepExecutor
from ceap.harness.memory import (
    CaseRecord,
    InvestigationMemory,
    evidence_problems,
)

log = logging.getLogger("ceap.harness.engine")


class EvidenceMixin:
    """Evidence validation, dispute resolution, hypotheses and case memory."""

    @staticmethod
    def _validate_evidence(
        memory: InvestigationMemory, warnings: list[ResultWarning] | None = None
    ) -> list[str]:
        """Drop findings whose evidence does not resolve, whose cited values do not match the value
        at their path (3B.4), or whose calculations derive from evidence this run never recorded."""
        kept: list[Finding] = []
        dropped: list[str] = []
        for f in memory.findings:
            missing = memory.unresolved_evidence(f)
            problems = evidence_problems(f, memory.evidence)
            if missing or not f.supporting_evidence or problems:
                dropped.append(f.id)
                memory.scratch.setdefault("validation", {}).setdefault("dropped", []).append(
                    {
                        "finding_id": f.id,
                        "statement": f.statement,
                        "missing_evidence": missing,
                        "binding_problems": problems,
                    }
                )
                if problems and warnings is not None:
                    warnings.append(
                        _w(
                            "EVIDENCE_VALUE_MISMATCH",
                            f"finding {f.id} dropped: {problems[0]}",
                            source=f.produced_by,
                        )
                    )
                continue
            kept.append(f)
        memory.replace_findings(kept)
        return dropped

    # ------------------------------------------------------------ R3B helpers
    def _recall_cases(self, task: Task, memory: InvestigationMemory) -> tuple[dict[str, Any], ...]:
        """R3B 3B.7: the three nearest human-decided cases in this task's dataset (read-only)."""
        cm = self.case_memory
        if cm is None or not cm.enabled:
            memory.agent_outputs["case_memory"] = {"enabled": False, "reads": 0, "retrieved": []}
            return ()
        query = (
            f"{task.input.get('kind') or 'investigation'} {task.input.get('symbol') or ''} {task.description}"
        )
        cases = cm.nearest(_partition(task), query, k=3)
        memory.agent_outputs["case_memory"] = {
            "enabled": True,
            "reads": 1,
            "retrieved": [c["case_id"] for c in cases],
        }
        return cases

    async def _resolve_disputes(
        self,
        memory: InvestigationMemory,
        run: RunContext,
        executor: StepExecutor,
        task: Task,
        step_results: list[dict[str, Any]],
        warnings: list[ResultWarning],
    ) -> None:
        """R3B 3B.3: a critic dispute changes a confidence only through a named alternative check whose
        deterministic result contradicts the finding's cited value. At most ``MAX_DISPUTE_CHECKS``
        checks run, each one tool call through the policy path. Free text is stored, never forwarded."""
        critique = (memory.agent_outputs.get(CRITIC_AGENT_ID) or {}).get("critique") or {}
        disputes = list(critique.get("disputes") or [])
        outcomes: list[dict[str, Any]] = []
        by_id = {f.id: f for f in memory.findings}
        checks_run = 0
        for d in disputes:
            finding = by_id.get(str(d.get("finding_id")))
            check = str(d.get("check") or "")
            if finding is None:
                continue
            if checks_run >= MAX_DISPUTE_CHECKS:
                outcomes.append(
                    {
                        "finding_id": finding.id,
                        "check": check,
                        "status": "SKIPPED",
                        "reason": "check cap reached",
                    }
                )
                continue
            planned = check_plan(finding, check, task.input)
            if isinstance(planned, str):
                outcomes.append(
                    {"finding_id": finding.id, "check": check, "status": "UNAVAILABLE", "reason": planned}
                )
                continue
            spec, path, (b_start, b_end) = planned
            symbol, dataset = task.input.get("symbol"), task.input.get("dataset")
            win = memory.find_exact(
                spec.tool_id,
                {
                    "symbol": symbol,
                    "start": task.input.get("window_start"),
                    "end": task.input.get("window_end"),
                    "dataset": dataset,
                },
            )
            if win is None or not isinstance(win.data, dict):
                outcomes.append(
                    {
                        "finding_id": finding.id,
                        "check": check,
                        "status": "UNAVAILABLE",
                        "reason": "window data not fetched",
                    }
                )
                continue
            checks_run += 1
            request = ToolRequest(
                tool_id=spec.tool_id,
                arguments={"symbol": symbol, "start": b_start, "end": b_end, "dataset": dataset},
            )
            step_id = f"gov-dispute-{checks_run:02d}"
            result = await executor.execute_tool(request, step_id, run)
            step_results.append(
                _step_record(
                    PlanStep(
                        id=step_id,
                        sequence=0,
                        type=StepType.TOOL_CALL,
                        description=f"dispute check {check}",
                        tool_request=request,
                    ),
                    result,
                )
            )
            if not result.ok or not isinstance(result.data, dict):
                outcomes.append(
                    {
                        "finding_id": finding.id,
                        "check": check,
                        "status": "UNAVAILABLE",
                        "reason": f"check data unavailable: {result.error}",
                    }
                )
                continue
            outcome = judge_alternative(
                finding,
                check,
                spec,
                path,
                win.data.get(spec.field),
                result.data.get(spec.field),
                (b_start, b_end),
            )
            record = outcome.to_dict()
            if outcome.contradicts:
                calc = Evidence.create(
                    type=EvidenceType.CALCULATION,
                    source="harness:dispute",
                    description=f"Alternative check {check} contradicts {finding.id}: {outcome.reason}",
                    timestamp=utc_now(),
                    attributes={
                        **record,
                        "derived_from": [*win.evidence_ids, *(e.id for e in result.evidence)],
                        "task_id": run.task_id,
                    },
                )
                memory.add_evidence(calc)
                lowered = replace(
                    finding,
                    confidence=round(min(finding.confidence, DISPUTE_CAP), 3),
                    contradicting_evidence=(*finding.contradicting_evidence, calc.id),
                    attributes={**finding.attributes, "disputed_by": check},
                )
                memory.replace_findings([lowered if f.id == finding.id else f for f in memory.findings])
                record["evidence_id"] = calc.id
                warnings.append(
                    _w(
                        "FINDING_DISPUTED",
                        f"{finding.id} lowered to {DISPUTE_CAP}: {outcome.reason}",
                        source=CRITIC_AGENT_ID,
                        info=True,
                    )
                )
            outcomes.append(record)
        unbacked = list(critique.get("unbacked_disputes") or [])
        if outcomes or unbacked:
            memory.agent_outputs["disputes"] = {"checks": outcomes, "recorded_without_check": unbacked}

    @staticmethod
    def _hypotheses(memory: InvestigationMemory) -> Any:
        """R3B 3B.2: the deterministic hypothesis table (verdicts never come from model text)."""
        quant = memory.agent_outputs.get("quant") or {}
        if "quant" not in memory.agent_outputs:
            return None
        scope = memory.agent_outputs.get("scope") or {}
        table = evaluate_hypotheses(
            list(scope.get("hypotheses") or []),
            quant.get("attribution"),
            memory.findings,
            quant.get("attribution_evidence_id"),
        )
        memory.scratch["hypotheses"] = table
        memory.agent_outputs["hypotheses"] = hypothesis_table_dict(table) or {}
        return table

    def _record_case(self, task: Task, step: PlanStep, decision: Any, memory: InvestigationMemory) -> None:
        """R3B 3B.7 / 3B.8: after a human decision, the harness appends one compact case record."""
        cm = self.case_memory
        if cm is None or not cm.enabled:
            return
        table = memory.scratch.get("hypotheses")
        rows = tuple(
            (r.name, r.verdict)
            for r in (table.rows if table else ())
            if r.verdict in ("SUPPORTED", "REFUTED")
        )
        action = getattr(decision, "action", "") or ("approve" if decision.approved else "reject")
        record = CaseRecord(
            case_id=new_id("CASE"),
            task_id=task.id,
            dataset=_partition(task),
            kind=task_kind(task),
            symbol=task.input.get("symbol"),
            question=str(task.description)[:500],
            outcome="APPROVED"
            if decision.approved
            else ("MODIFY_REQUESTED" if action == "modify" else "REJECTED"),
            decided_by=decision.decided_by,
            reason=str(decision.comment or "")[:500],
            hypotheses=rows,
            recorded_at=utc_now().isoformat(),
        )
        if cm.append(record):
            memory.agent_outputs.setdefault("case_memory", {})["written"] = record.case_id
