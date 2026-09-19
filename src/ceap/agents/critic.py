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
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.findings import Finding
from ceap.llm.client import LLMClient, extract_json
from ceap.llm.models import LLMRequest
from ceap.llm.prompts import CRITIC_SYSTEM_PROMPT

log = logging.getLogger(__name__)

SINGLE_EVIDENCE_CAP = 0.7


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

        overall = self._overall(assessments, contradictions)
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
        return AgentResult(
            agent_id=self.id,
            success=True,
            evidence=(calc,),
            output=(
                {"critique": critique, "adjusted_findings": tuple(adjusted)}
                | ({"llm_fallback": llm_fallback} if llm_fallback else {})
            ),
            summary=f"{len(assessments)} findings reviewed, {len(unsupported)} unsupported, {len(contradictions)} contradictions",
        )

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
