"""Report agent: the LLM writes the explanation; the numbers come from evidence.

After the narrative is generated, a *number audit* checks that every
figure quoted in the narrative can be traced to the structured facts that
were handed to the model. Discrepancies are attached to the report as
warnings so reviewers can see exactly where the prose drifted.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from ceap.agents.base import BaseAgent
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.common import utc_now
from ceap.domain.reports import InvestigationReport
from ceap.llm.client import LLMClient
from ceap.llm.models import LLMRequest
from ceap.llm.prompts import REPORT_SYSTEM_PROMPT

SECTION_NAMES = (
    "EXECUTIVE SUMMARY",
    "PRIMARY OBSERVATIONS",
    "EXECUTION",
    "MARKET CONDITIONS",
    "TECHNOLOGY",
    "CONCLUSION",
    "ALTERNATIVE EXPLANATIONS",
    "POLICY CONTEXT",
    "CRITIC",
    "EVIDENCE",
)


class ReportAgent(BaseAgent):
    agent_id = "reporter"
    agent_type = "reporter"

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def execute(self, context: AgentContext) -> AgentResult:
        memory = self.memory(context)
        w = self.window(context)
        outputs = context.state.get("agent_outputs") or {}
        quant = outputs.get("quant") or {}
        engineering = outputs.get("engineering") or {}
        critique = (outputs.get("critic") or {}).get("critique") or {}
        attribution = quant.get("attribution") or {}

        findings = sorted(
            context.findings, key=lambda f: (f.attributes.get("role") == "rejected", -f.confidence)
        )
        alternatives = self._alternatives(attribution, quant)
        title = f"{w.symbol} EXECUTION INVESTIGATION {_short(w.start)}-{_short(w.end)} London"
        facts: dict[str, Any] = {
            "title": title,
            "symbol": w.symbol,
            "window": {"start": w.start, "end": w.end},
            "baseline": {"start": w.baseline_start, "end": w.baseline_end},
            "metrics": quant.get("metrics") or {},
            "market": quant.get("market") or {},
            "attribution": attribution,
            "engineering": {
                k: engineering.get(k)
                for k in ("latency_ratio", "slo_breached", "deployments", "error_logs", "feed_gap_count")
            },
            "findings": [
                {
                    "id": f.id,
                    "statement": f.statement,
                    "confidence": f.confidence,
                    "category": f.category,
                    "evidence": list(f.supporting_evidence),
                    "contradicting": list(f.contradicting_evidence),
                }
                for f in findings
            ],
            "critique": {
                "overall": critique.get("overall"),
                "contradictions": critique.get("contradictions", []),
            },
            "alternatives": alternatives,
            "policy_context": self._policy_context(memory),
            "evidence_ids": sorted(memory.evidence),
        }
        response = await self.llm.complete(
            LLMRequest(
                system_prompt=REPORT_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": "Write the report from these facts:\n\n" + json.dumps(facts, default=str),
                    }
                ],
                purpose="narrative",
                max_tokens=3000,
            )
        )
        narrative = response.content.strip()
        sections = _split_sections(narrative)
        number_warnings = audit_numbers(narrative, facts)
        id_warnings = audit_evidence_ids(narrative, set(memory.evidence))

        report = InvestigationReport(
            task_id=context.task_id,
            execution_id=context.execution_id,
            title=title,
            executive_summary=sections.get("EXECUTIVE SUMMARY") or _default_summary(attribution),
            primary_observations=tuple(
                f.statement for f in findings if f.attributes.get("role") != "rejected"
            )[:8],
            conclusion=sections.get("CONCLUSION") or _default_conclusion(attribution),
            alternative_explanations=tuple(alternatives),
            findings=tuple(findings),
            evidence=memory.evidence_list(),
            metrics={
                "execution": facts["metrics"],
                "market": facts["market"],
                "engineering": facts["engineering"],
            },
            generated_at=utc_now(),
            narrative=narrative,
            attribution=attribution,
            critique={
                **critique,
                "narrative_number_warnings": number_warnings,
                "narrative_evidence_warnings": id_warnings,
                "llm_model": response.model,
            },
        )
        return AgentResult(
            agent_id=self.id,
            success=True,
            output={
                "report": report,
                "llm_model": response.model,
                "tokens": {"input": response.input_tokens, "output": response.output_tokens},
            },
            summary=f"report generated ({len(narrative)} chars, {len(number_warnings)} number warnings)",
        )

    @staticmethod
    def _policy_context(memory: Any) -> list[dict[str, Any]]:
        out = memory.find("knowledge.search_documents")
        if not out:
            return []
        return [
            {
                "title": h.get("title"),
                "section": h.get("section"),
                "excerpt": str(h.get("text", ""))[:400],
                "evidence": list(out.evidence_ids),
            }
            for h in (out.data.get("items") or [])[:3]
        ]

    @staticmethod
    def _alternatives(attribution: dict[str, Any], quant: dict[str, Any]) -> list[str]:
        alts: list[str] = []
        ranked = attribution.get("ranked", [])
        for r in ranked[1:]:
            if 0.1 <= r.get("score", 0) < 0.35:
                alts.append(
                    f"{r['cause'].replace('_', ' ').capitalize()} was considered but scored {r['score']:.2f} ({r['rationale']})."
                )
        part = ((quant.get("metrics") or {}).get("window") or {}).get("participation_rate")
        if (
            isinstance(part, (int, float))
            and part > 0.10
            and attribution.get("primary") != "LARGE_ORDER_IMPACT"
        ):
            alts.append(
                f"Participation of {part:.1%} during the window may also have increased market impact."
            )
        return alts[:4]


def _short(iso: str) -> str:
    m = re.search(r"T(\d{2}:\d{2})", iso)
    return m.group(1) if m else iso


def _split_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().rstrip(":")
        if stripped.upper() in SECTION_NAMES or (
            stripped.upper().startswith("#") and stripped.lstrip("# ").upper() in SECTION_NAMES
        ):
            if current:
                sections[current] = "\n".join(buf).strip()
            current = stripped.lstrip("# ").upper()
            buf = []
        elif current:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    return sections


def _default_summary(attribution: dict[str, Any]) -> str:
    primary = attribution.get("primary", "NORMAL")
    if primary == "NORMAL":
        return "Execution quality was consistent with the baseline; no material anomaly identified."
    return f"Execution quality changed materially; the evidence attributes it primarily to {primary.replace('_', ' ').lower()}."


def _default_conclusion(attribution: dict[str, Any]) -> str:
    return _default_summary(attribution)


# ----------------------------------------------------------------- audits
_NUM = re.compile(r"(?<![\w:.-])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?![\w:])")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
_ID = re.compile(r"\b[A-Z]{1,8}-[0-9a-f]{8}\b")


def _collect_numbers(obj: Any, out: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)
    elif isinstance(obj, str):
        for m in _NUM.finditer(_ISO.sub(" ", obj)):
            try:
                out.add(float(m.group().replace(",", "")))
            except ValueError:
                pass


def audit_numbers(narrative: str, facts: dict[str, Any]) -> list[str]:
    """Return the numbers in ``narrative`` that cannot be traced to ``facts``."""
    fact_numbers: set[float] = set()
    _collect_numbers(facts, fact_numbers)
    derived: set[float] = set()
    for v in fact_numbers:
        derived.update({v, v * 100.0, (v - 1.0) * 100.0, abs(v), -v})
    warnings: list[str] = []
    text = _ISO.sub(" ", narrative)
    for m in _NUM.finditer(text):
        token = m.group()
        try:
            n = float(token.replace(",", ""))
        except ValueError:
            continue
        decimals = len(token.split(".")[1]) if "." in token else 0
        if decimals == 0 and abs(n) <= 12:  # list numbering / small counts
            continue
        if any(round(d, decimals) == round(n, decimals) for d in derived):
            continue
        warnings.append(f"number {token} not traceable to structured facts")
    return warnings[:25]


def audit_evidence_ids(narrative: str, known: set[str]) -> list[str]:
    return [f"evidence id {m} not found" for m in sorted(set(_ID.findall(narrative))) if m not in known]
