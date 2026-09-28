"""Critic agent: are these conclusions actually supported?

Deterministic checks run first (they cannot be talked out of by a model):

1. every cited evidence id must resolve;
2. a finding that asserts an anomaly must be consistent with the
   attribution scores (otherwise its confidence is cut and the attribution
   record is attached as contradicting evidence);
3. cross-agent contradictions (e.g. "technology caused it" vs
   "latency normal") are flagged;
4. thinly evidenced findings are capped.

An optional LLM critique then runs and may only *lower* confidence.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import replace
from typing import Any

from ceap.agents.base import BaseAgent, finite
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
from ceap.llm.client import LLMClient, extract_json
from ceap.llm.models import LLMRequest
from ceap.llm.prompts import CRITIC_SYSTEM_PROMPT

log = logging.getLogger(__name__)

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
            adjusted, research_assessment = self._research_assessment(context, adjusted, assessments, contradictions)

        overall = self._overall(assessments, contradictions)
        if research_assessment is not None:
            flags = ", ".join(research_assessment["flags"]) or "no flags"
            overall += f" Research assessment: {research_assessment['verdict']} ({flags})."
        llm_overall = None
        llm_fallback: str | None = None
        if self.llm is not None:
            try:
                adjusted, llm_overall, llm_fallback = await self._llm_critique(adjusted, memory, assessments)
            except Exception as exc:  # noqa: BLE001 - the deterministic critique must survive a model failure
                log.warning("LLM critique failed (%s); keeping deterministic assessment", exc)
                llm_overall = None
        unsupported = [a["finding_id"] for a in assessments if not a["supported"]]
        critique: dict[str, Any] = {
            "assessments": assessments,
            "contradictions": contradictions,
            "unsupported": unsupported,
            "overall": overall if llm_overall is None else f"{overall} {llm_overall}",
        }
        calc = self.calc_evidence(
            context,
            "Critic assessment of findings",
            {"assessments": assessments, "contradictions": contradictions},
        )
        output: dict[str, Any] = {"critique": critique, "adjusted_findings": tuple(adjusted)}
        if research_assessment is not None:
            output["assessment"] = research_assessment
        if llm_fallback:
            output["llm_fallback"] = llm_fallback
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
        stats = signal_statistics_from_dict(alpha_out["signal_statistics"]) if alpha_out.get("signal_statistics") else None
        backtest = backtest_result_from_dict(bt_out["backtest"]) if bt_out.get("backtest") else None
        risk = portfolio_risk_report_from_dict(risk_out["risk"]) if risk_out.get("risk") else None
        assessment = assess_research(stats, backtest, risk)
        source = tuple(
            e for e in (alpha_out.get("signal_evidence_id"), bt_out.get("backtest_evidence_id")) if isinstance(e, str)
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
            elif claim == "ROBUST" and ResearchFlag.OVERFIT.value in flags:
                cap, note = 0.4, "claims robustness but the assessment flags OVERFIT"
            elif (
                claim == "PROFITABLE"
                and assessment.verdict is ResearchVerdict.REJECT
                and flags & {ResearchFlag.OVERFIT.value, ResearchFlag.COST_DRAG.value}
            ):
                cap, note = 0.5, "claims profitability but the assessment rejects the strategy"
            elif f.attributes.get("coverage") == "COMPLETE" and ResearchFlag.INCOMPLETE_ANALYSIS.value in flags:
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
                    attributes={**f.attributes, "critic_notes": [*f.attributes.get("critic_notes", []), note]},
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
        self, findings: list[Finding], memory: Any, assessments: list[dict[str, Any]]
    ) -> tuple[list[Finding], str | None, str | None]:
        payload = {
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
        assert self.llm is not None
        response = await self.llm.complete(
            LLMRequest(
                system_prompt=CRITIC_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(payload)}],
                purpose="critique",
                max_tokens=2048,
            )
        )
        parsed = extract_json(response.content) or {}
        by_id = {a.get("finding_id"): a for a in (parsed.get("assessments") or []) if isinstance(a, dict)}
        out: list[Finding] = []
        for f in findings:
            a = by_id.get(f.id)
            if a is None:
                out.append(f)
                continue
            try:
                proposed = float(a.get("adjusted_confidence", f.confidence))
            except (TypeError, ValueError):
                proposed = f.confidence
            if not math.isfinite(proposed):
                proposed = f.confidence
            new_conf = min(f.confidence, max(0.0, proposed))  # model may only lower confidence
            for rec in assessments:
                if rec["finding_id"] == f.id:
                    rec["llm_comment"] = str(a.get("comment", ""))[:500]
                    rec["adjusted_confidence"] = round(new_conf, 3)
            out.append(replace(f, confidence=round(new_conf, 3)))
        return out, (str(parsed.get("overall", ""))[:1000] or None), response.fallback_reason


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
