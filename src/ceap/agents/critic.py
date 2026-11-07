"""Critic agent: are these conclusions actually supported?

Deterministic checks run first (they cannot be talked out of by a model):

1. every cited evidence id must resolve;
2. a finding that asserts an anomaly must be consistent with the
   attribution scores (otherwise its confidence is cut and the attribution
   record is attached as contradicting evidence);
3. cross-agent contradictions (e.g. "technology caused it" vs
   "latency normal") are flagged;
4. thinly evidenced findings are capped.

An optional LLM critique then runs. Since R3B (3B.3) it cannot change a confidence itself: an
assessment that disputes a finding is a *dispute*, which counts only when it names an alternative
deterministic check (``alternative_baseline``, ``different_horizon``, ``peer_set``). The harness runs
that check through the policy path and lowers the confidence only if the check's result contradicts
the finding's cited value. ``supported=False`` without a named check is recorded and changes nothing.
The model's comments are stored, never forwarded to another model.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import replace
from typing import Any

from ceap.agents.base import BaseAgent, degradation_records, finite, llm_disabled
from ceap.analytics.attribution import MATERIAL_SCORE
from ceap.analytics.research_assessment import DEFAULT_THRESHOLDS, assess_research
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.findings import Finding
from ceap.domain.research import (
    ResearchFlag,
    ResearchVerdict,
    backtest_result_from_dict,
    portfolio_risk_report_from_dict,
    signal_statistics_from_dict,
)
from ceap.harness.cancellation import TaskCancelled
from ceap.llm.client import LLMClient
from ceap.llm.models import (
    RUNG_DETERMINISTIC,
    RUNG_KILL_SWITCH,
    RUNG_REPAIR,
    Degradation,
    LLMRequest,
)
from ceap.llm.prompts import DATA_BLOCK_NOTE, data_block, fit_context, get_prompt
from ceap.llm.schemas import (
    CRITIC_CHECKS,
    CritiqueOutput,
    critique_semantic_errors,
    parse_json_object,
    validate_model,
)

log = logging.getLogger(__name__)
CRITIC_REPAIR_INSTRUCTION = (
    "Your previous critique cannot be used. Problems found:\n{problems}\n"
    "Reply again with one assessment per finding, using only the finding ids given above."
)

SINGLE_EVIDENCE_CAP = 0.7
RESEARCH_KIND = "research"
_RESEARCH_FLAGS = {f.value for f in ResearchFlag}


class CriticAgent(BaseAgent):
    agent_id = "critic"
    agent_type = "critic"

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    async def execute(self, context: AgentContext) -> AgentResult:
        memory = self.memory(context)
        quant = (context.state.get("agent_outputs") or {}).get("quant") or {}
        engineering = (context.state.get("agent_outputs") or {}).get("engineering") or {}
        attribution = quant.get("attribution") or {}
        scores = {r["cause"]: r["score"] for r in attribution.get("ranked", [])}
        attribution_ev = quant.get("attribution_evidence_id")

        assessments: list[dict[str, Any]] = []
        contradictions: list[str] = []
        adjusted: list[Finding] = []

        for f in context.findings:
            conf = f.confidence
            notes: list[str] = []
            contradicting = list(f.contradicting_evidence)
            missing = memory.unresolved_evidence(f)
            supported = True
            if missing or not f.supporting_evidence:
                supported = False
                conf = min(conf, 0.1)
                notes.append(f"unresolved evidence: {missing or 'none cited'}")
            anomaly = f.attributes.get("anomaly")
            if anomaly and anomaly in scores and f.attributes.get("role") != "rejected":
                score = scores[anomaly]
                if score < MATERIAL_SCORE:
                    conf = min(conf, max(0.2, conf - 0.3))
                    notes.append(
                        f"attribution score for {anomaly} is {score:.2f} (< {MATERIAL_SCORE}); anomaly not corroborated"
                    )
                    if attribution_ev and attribution_ev not in contradicting:
                        contradicting.append(attribution_ev)
                    contradictions.append(f"{f.id}: claims {anomaly} but attribution does not corroborate")
            if anomaly == "CODE_CHANGE":
                lr = engineering.get("latency_ratio")
                if lr is not None and finite(lr) and float(lr) < 3.0:
                    conf = min(conf, 0.6)
                    notes.append(
                        "deployment did not coincide with a latency anomaly; treat as context, not cause"
                    )
            if anomaly == "TECHNOLOGY_LATENCY" and f.produced_by == "quant":
                lr = engineering.get("latency_ratio")
                if (
                    lr is not None
                    and finite(lr)
                    and float(lr) < 3.0
                    and (engineering.get("error_logs") or 0) == 0
                ):
                    conf = min(conf, 0.4)
                    contradictions.append(
                        f"{f.id}: quant attributes to technology but engineering telemetry is normal"
                    )
                    notes.append("engineering telemetry contradicts technology attribution")
            if len(set(f.supporting_evidence)) <= 1 and conf > SINGLE_EVIDENCE_CAP:
                conf = SINGLE_EVIDENCE_CAP
                notes.append("single evidence record; confidence capped")
            assessments.append(
                {
                    "finding_id": f.id,
                    "supported": supported,
                    "original_confidence": f.confidence,
                    "adjusted_confidence": round(conf, 3),
                    "notes": notes,
                }
            )
            adjusted.append(
                replace(
                    f,
                    confidence=round(conf, 3),
                    contradicting_evidence=tuple(contradicting),
                    attributes={**f.attributes, "critic_notes": notes},
                )
            )

        research_assessment: dict[str, Any] | None = None
        if str(context.task_input.get("kind") or "") == RESEARCH_KIND:
            adjusted, research_assessment = self._research_assessment(
                context, adjusted, assessments, contradictions
            )

        overall = self._overall(assessments, contradictions)
        if research_assessment is not None:
            flags = ", ".join(research_assessment["flags"]) or "no flags"
            overall += f" Research assessment: {research_assessment['verdict']} ({flags})."
        llm_overall = None
        degradations: list[Degradation] = []
        if self.llm is not None and llm_disabled(self.llm):
            degradations.append(
                Degradation(RUNG_KILL_SWITCH, "LLM_DISABLED", "kill switch on: deterministic critique only")
            )
        elif self.llm is not None:
            try:
                adjusted, llm_overall, llm_degradations = await self._llm_critique(
                    context, adjusted, memory, assessments
                )
                degradations.extend(llm_degradations)
            except (TaskCancelled, asyncio.CancelledError):
                raise
            except Exception as exc:  # noqa: BLE001 - the deterministic critique must survive a model failure
                log.warning("LLM critique failed (%s); keeping deterministic assessment", exc)
                llm_overall = None
                degradations.append(
                    Degradation(
                        RUNG_DETERMINISTIC,
                        "LLM_CRITIQUE_FAILED",
                        f"deterministic critique only ({type(exc).__name__}: {str(exc)[:200]})",
                    )
                )
        unsupported = [a["finding_id"] for a in assessments if not a["supported"]]
        critique: dict[str, Any] = {
            "assessments": assessments,
            "contradictions": contradictions,
            "unsupported": unsupported,
            "overall": overall,
            # R3B 3B.3: disputes with a named check go to the harness; the rest are only recorded
            "disputes": [a["dispute"] for a in assessments if a.get("dispute")],
            "unbacked_disputes": [a["finding_id"] for a in assessments if a.get("unbacked_dispute")],
        }
        if llm_overall is not None:
            critique["llm_overall"] = llm_overall  # model text: stored, never forwarded
        calc = self.calc_evidence(
            context,
            "Critic assessment of findings",
            {"assessments": assessments, "contradictions": contradictions},
        )
        output: dict[str, Any] = {"critique": critique, "adjusted_findings": tuple(adjusted)}
        if self.llm is not None and not llm_disabled(self.llm):
            output["prompt"] = get_prompt("critic").ref()
        if research_assessment is not None:
            output["assessment"] = research_assessment
        if degradations:
            output["degradations"] = degradation_records(degradations)
        return AgentResult(
            agent_id=self.id,
            success=True,
            evidence=(calc,),
            output=output,
            summary=f"{len(assessments)} findings reviewed, {len(unsupported)} unsupported, {len(contradictions)} contradictions",
        )

    def _research_assessment(
        self,
        context: AgentContext,
        findings: list[Finding],
        assessments: list[dict[str, Any]],
        contradictions: list[str],
    ) -> tuple[list[Finding], dict[str, Any]]:
        """Deterministic research verdict, then cap findings that contradict it."""
        outputs = context.state.get("agent_outputs") or {}
        alpha_out = outputs.get("alpha") or {}
        bt_out = outputs.get("backtest") or {}
        risk_out = outputs.get("portfolio_risk") or {}
        stats = (
            signal_statistics_from_dict(alpha_out["signal_statistics"])
            if alpha_out.get("signal_statistics")
            else None
        )
        backtest = backtest_result_from_dict(bt_out["backtest"]) if bt_out.get("backtest") else None
        risk = portfolio_risk_report_from_dict(risk_out["risk"]) if risk_out.get("risk") else None
        assessment = assess_research(stats, backtest, risk)
        source = tuple(
            e
            for e in (alpha_out.get("signal_evidence_id"), bt_out.get("backtest_evidence_id"))
            if isinstance(e, str)
        )
        flags = {f.value for f in assessment.flags}
        calc = self.calc_evidence(
            context,
            f"Research assessment: {assessment.verdict.value} ({', '.join(sorted(flags)) or 'no flags'})",
            assessment.to_dict(),
            source,
        )
        metrics = assessment.metrics
        is_t = metrics.get("is_ic_t_stat", float("nan"))
        records = {a["finding_id"]: a for a in assessments}
        out: list[Finding] = []
        for f in findings:
            claim = f.attributes.get("claim")
            flag = f.attributes.get("flag")
            cap: float | None = None
            note = ""
            if claim == "ALPHA" and (not finite(is_t) or float(is_t) < DEFAULT_THRESHOLDS.ic_t_stat_min):
                cap, note = 0.4, "claims alpha but the in-sample IC t-stat is below the promotion threshold"
            elif claim == "ROBUST" and ResearchFlag.OOS_DECAY.value in flags:
                cap, note = 0.4, "claims robustness but the assessment flags OOS_DECAY"
            elif (
                claim == "PROFITABLE"
                and assessment.verdict is ResearchVerdict.REJECT
                and flags & {ResearchFlag.OOS_DECAY.value, ResearchFlag.COST_DRAG.value}
            ):
                cap, note = 0.5, "claims profitability but the assessment rejects the strategy"
            elif (
                f.attributes.get("coverage") == "COMPLETE" and ResearchFlag.INCOMPLETE_ANALYSIS.value in flags
            ):
                cap, note = 0.5, "claims complete coverage but the analysis is incomplete"
            elif isinstance(flag, str) and flag in _RESEARCH_FLAGS and flag not in flags:
                cap, note = 0.6, f"flag {flag} is not corroborated by the assessment"
            if cap is None or f.confidence <= cap:
                out.append(f)
                continue
            contradicting = list(f.contradicting_evidence)
            if calc.id not in contradicting:
                contradicting.append(calc.id)
            contradictions.append(f"{f.id}: {note}")
            record = records.get(f.id)
            if record is not None:
                record["adjusted_confidence"] = round(cap, 3)
                record["notes"] = [*record.get("notes", []), note]
            out.append(
                replace(
                    f,
                    confidence=round(cap, 3),
                    contradicting_evidence=tuple(contradicting),
                    attributes={
                        **f.attributes,
                        "critic_notes": [*f.attributes.get("critic_notes", []), note],
                    },
                )
            )
        return out, {**assessment.to_dict(), "evidence_id": calc.id}

    @staticmethod
    def _overall(assessments: list[dict[str, Any]], contradictions: list[str]) -> str:
        n = len(assessments)
        unsupported = sum(1 for a in assessments if not a["supported"])
        lowered = sum(1 for a in assessments if a["adjusted_confidence"] < a["original_confidence"])
        parts = [
            f"Reviewed {n} findings: {n - unsupported} supported by resolvable evidence, {unsupported} unsupported."
        ]
        if lowered:
            parts.append(f"Confidence lowered on {lowered} finding(s).")
        if contradictions:
            parts.append(f"{len(contradictions)} cross-agent contradiction(s) flagged.")
        else:
            parts.append("No cross-agent contradictions.")
        return " ".join(parts)

    async def _llm_critique(
        self, context: AgentContext, findings: list[Finding], memory: Any, assessments: list[dict[str, Any]]
    ) -> tuple[list[Finding], str | None, list[Degradation]]:
        raw_payload = {
            "findings": [
                {
                    "id": f.id,
                    "statement": f.statement,
                    "confidence": f.confidence,
                    "supporting_evidence": [
                        _ev_view(memory.evidence[e]) for e in f.supporting_evidence if e in memory.evidence
                    ],
                    "contradicting_evidence": [
                        _ev_view(memory.evidence[e]) for e in f.contradicting_evidence if e in memory.evidence
                    ],
                    "evidence_resolved": not memory.unresolved_evidence(f),
                }
                for f in findings
            ]
        }
        # R3B 3B.6: context budget with the documented truncation order; R3B 3B.5: evidence is data
        payload, truncation = fit_context(raw_payload)
        assert self.llm is not None
        prompt = get_prompt("critic")
        content = (
            f"{DATA_BLOCK_NOTE}\nAssess these findings. To dispute one, name an alternative check.\n"
            + data_block("evidence", "findings", payload)
        )
        request = LLMRequest(
            system_prompt=prompt.text,
            messages=[{"role": "user", "content": content}],
            purpose="critique",  # max_tokens 8000 and effort from model_pins.toml
            metadata={"task_id": context.task_id, "prompt": prompt.ref()},
            output_model=CritiqueOutput,
        )
        ids = [f.id for f in findings]

        def check(resp: Any) -> tuple[dict[str, Any], list[str]]:
            if resp.parsed is not None:
                raw: dict[str, Any] | None = resp.parsed
                errors: list[str] = []
            elif resp.parse_errors:
                raw, errors = None, list(resp.parse_errors)
            else:
                raw, errors = parse_json_object(resp.content)
            if raw is None:
                return {}, errors or ["no structured reply"]
            return raw, validate_model(CritiqueOutput, raw) + critique_semantic_errors(raw, ids)

        response = await self.call_llm(context, self.llm, request)
        degradations = list(response.degradations)
        if truncation:
            degradations.append(
                Degradation(RUNG_REPAIR, "CONTEXT_TRUNCATED", "critic payload truncated: " + "; ".join(truncation))
            )
        parsed, errors = check(response)
        if findings and errors:
            degradations.append(
                Degradation(
                    RUNG_REPAIR, "LLM_REPAIR_RETRY", f"critique invalid ({errors[0][:160]}); one repair retry"
                )
            )
            repair = LLMRequest(
                **{
                    **request.__dict__,
                    "messages": [
                        *request.messages,
                        {"role": "assistant", "content": response.content[:4000] or "(no usable reply)"},
                        {
                            "role": "user",
                            "content": CRITIC_REPAIR_INSTRUCTION.format(
                                problems="\n".join(f"- {e}" for e in errors[:20])
                            ),
                        },
                    ],
                }
            )
            response = await self.call_llm(context, self.llm, repair)
            degradations.extend(response.degradations)
            parsed, errors = check(response)
            if errors:
                degradations.append(
                    Degradation(
                        RUNG_DETERMINISTIC,
                        "LLM_CRITIQUE_UNUSABLE",
                        "deterministic critique only (model output unusable)",
                    )
                )
                parsed = {}
        by_id = {a.get("finding_id"): a for a in (parsed.get("assessments") or []) if isinstance(a, dict)}
        for f in findings:
            a = by_id.get(f.id)
            if a is None:
                continue
            try:
                proposed = float(a.get("adjusted_confidence", f.confidence))
            except (TypeError, ValueError):
                proposed = f.confidence
            disputes = a.get("supported") is False or (math.isfinite(proposed) and proposed < f.confidence)
            check = a.get("check")
            for rec in assessments:
                if rec["finding_id"] != f.id:
                    continue
                rec["llm_comment"] = str(a.get("comment", ""))[:500]  # stored, never forwarded
                rec["llm_proposed_confidence"] = round(proposed, 3) if math.isfinite(proposed) else None
                if disputes and check in CRITIC_CHECKS:
                    rec["dispute"] = {"finding_id": f.id, "check": check}
                elif disputes:
                    rec["unbacked_dispute"] = True  # recorded; changes nothing
        # the model never sets a confidence: the findings leave unchanged (3B.3)
        return findings, (str(parsed.get("overall", ""))[:1000] or None), degradations


def _ev_view(e: Any) -> dict[str, Any]:
    return {
        "id": e.id,
        "type": e.type.value,
        "description": e.description,
        "attributes": {
            k: v
            for k, v in e.attributes.items()
            if k in ("digest", "tool_id", "arguments") or isinstance(v, (int, float, str))
        },
    }
