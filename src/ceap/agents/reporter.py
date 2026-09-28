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

from ceap.agents.base import BaseAgent, finite
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.common import utc_now
from ceap.domain.findings import Finding
from ceap.domain.reports import InvestigationReport
from ceap.domain.research import ResearchScope
from ceap.harness.memory import InvestigationMemory
from ceap.llm.client import LLMClient
from ceap.llm.models import LLMRequest
from ceap.llm.prompts import REPORT_SYSTEM_PROMPT, RESEARCH_REPORT_SYSTEM_PROMPT

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
    # research proposal sections
    "HYPOTHESIS",
    "SIGNAL STATISTICS",
    "BACKTEST",
    "RISK",
    "VERDICT",
    "APPROVAL",
    "STAGED ORDERS",
)
RESEARCH_KIND = "research"
STAGE_TOOL_ID = "execution.stage_orders"


class ReportAgent(BaseAgent):
    agent_id = "reporter"
    agent_type = "reporter"

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def execute(self, context: AgentContext) -> AgentResult:
        memory = self.memory(context)
        outputs = context.state.get("agent_outputs") or {}
        critique = (outputs.get("critic") or {}).get("critique") or {}
        research = str(context.task_input.get("kind") or "") == RESEARCH_KIND
        findings = sorted(
            context.findings, key=lambda f: (f.attributes.get("role") == "rejected", -f.confidence)
        )
        if research:
            facts = self._research_facts(context, memory, outputs, findings, critique)
            system_prompt = RESEARCH_REPORT_SYSTEM_PROMPT
        else:
            facts = self._investigation_facts(context, memory, outputs, findings, critique)
            system_prompt = REPORT_SYSTEM_PROMPT
        title = str(facts["title"])
        response = await self.llm.complete(
            LLMRequest(
                system_prompt=system_prompt,
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
        critique_out = {
            **critique,
            "narrative_number_warnings": number_warnings,
            "narrative_evidence_warnings": id_warnings,
            "llm_model": response.model,
        }
        alternatives = list(facts.get("alternatives") or [])
        if research:
            assessment = facts.get("assessment") or {}
            report = InvestigationReport(
                task_id=context.task_id,
                execution_id=context.execution_id,
                title=title,
                executive_summary=sections.get("EXECUTIVE SUMMARY") or _default_research_summary(assessment),
                primary_observations=tuple(f.statement for f in findings)[:8],
                conclusion=sections.get("VERDICT") or _default_research_summary(assessment),
                alternative_explanations=tuple(alternatives),
                findings=tuple(findings),
                evidence=memory.evidence_list(),
                metrics={
                    "signal_statistics": facts.get("signal_statistics") or {},
                    "backtest": facts.get("backtest") or {},
                    "risk": facts.get("risk") or {},
                },
                generated_at=utc_now(),
                narrative=narrative,
                critique=critique_out,
                kind=RESEARCH_KIND,
                proposal=self._proposal(facts),
            )
        else:
            attribution = facts.get("attribution") or {}
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
                critique=critique_out,
            )
        output: dict[str, Any] = {
            "report": report,
            "llm_model": response.model,
            "tokens": {"input": response.input_tokens, "output": response.output_tokens},
        }
        if response.fallback_reason:
            output["llm_fallback"] = response.fallback_reason
        return AgentResult(
            agent_id=self.id,
            success=True,
            output=output,
            summary=f"report generated ({len(narrative)} chars, {len(number_warnings)} number warnings)",
        )

    # ------------------------------------------------------------- facts
    @staticmethod
    def _findings_view(findings: list[Finding]) -> list[dict[str, Any]]:
        return [
            {
                "id": f.id,
                "statement": f.statement,
                "confidence": f.confidence,
                "category": f.category,
                "evidence": list(f.supporting_evidence),
                "contradicting": list(f.contradicting_evidence),
            }
            for f in findings
        ]

    def _investigation_facts(
        self,
        context: AgentContext,
        memory: InvestigationMemory,
        outputs: dict[str, Any],
        findings: list[Finding],
        critique: dict[str, Any],
    ) -> dict[str, Any]:
        w = self.window(context)
        quant = outputs.get("quant") or {}
        engineering = outputs.get("engineering") or {}
        attribution = quant.get("attribution") or {}
        return {
            "title": f"{w.symbol} EXECUTION INVESTIGATION {_short(w.start)}-{_short(w.end)} London",
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
            "findings": self._findings_view(findings),
            "critique": {
                "overall": critique.get("overall"),
                "contradictions": critique.get("contradictions", []),
            },
            "alternatives": self._alternatives(attribution, quant),
            "policy_context": self._policy_context(memory),
            "evidence_ids": sorted(memory.evidence),
        }

    def _research_facts(
        self,
        context: AgentContext,
        memory: InvestigationMemory,
        outputs: dict[str, Any],
        findings: list[Finding],
        critique: dict[str, Any],
    ) -> dict[str, Any]:
        s = ResearchScope.from_task_input(context.task_input)
        research = outputs.get("research") or {}
        alpha = outputs.get("alpha") or {}
        backtest = outputs.get("backtest") or {}
        risk = outputs.get("portfolio_risk") or {}
        assessment = (outputs.get("critic") or {}).get("assessment") or {}
        approvals = memory.scratch.get("approvals") or {}
        staged = memory.find(STAGE_TOOL_ID)
        staging_state = memory.scratch.get("staging") or {}
        staging: dict[str, Any] = {
            "requested": s.stage_orders,
            "staged": staged is not None,
            "status": staging_state.get("status"),
            "error": staging_state.get("error"),
            "approval": approvals.get("gov-stage-approval"),
        }
        if staged is not None and isinstance(staged.data, dict):
            staging.update(
                {
                    "staging_id": staged.data.get("staging_id"),
                    "count": staged.data.get("count"),
                    "buy_notional": staged.data.get("buy_notional"),
                    "sell_notional": staged.data.get("sell_notional"),
                    "evidence": list(staged.evidence_ids),
                }
            )
        return {
            "kind": RESEARCH_KIND,
            "title": f"{s.signal.upper()} RESEARCH PROPOSAL {s.dataset} {s.start} to {s.end}",
            "scope": {
                "dataset": s.dataset,
                "signal": s.signal,
                "start": s.start,
                "end": s.end,
                "in_sample_end": s.in_sample_end,
                "rebalance_days": s.rebalance_days,
                "long_short": s.long_short,
                "gross_notional": s.gross_notional,
                "universe_size": len(context.task_input.get("universe") or []),
            },
            "hypothesis": research.get("hypothesis"),
            "coverage": research.get("coverage") or {},
            "signal_statistics": alpha.get("signal_statistics") or {},
            "backtest": backtest.get("backtest") or {},
            "risk": risk.get("risk") or {},
            "assessment": assessment,
            "findings": self._findings_view(findings),
            "critique": {
                "overall": critique.get("overall"),
                "contradictions": critique.get("contradictions", []),
            },
            "alternatives": self._research_alternatives(assessment),
            "approval": approvals.get("gov-approval"),
            "staging": staging,
            "policy_context": self._policy_context(memory),
            "evidence_ids": sorted(memory.evidence),
        }

    @staticmethod
    def _proposal(facts: dict[str, Any]) -> dict[str, Any]:
        assessment = facts.get("assessment") or {}
        scope = facts.get("scope") or {}
        exposure = (facts.get("risk") or {}).get("exposure") or {}
        return {
            "verdict": assessment.get("verdict"),
            "flags": list(assessment.get("flags") or []),
            "scores": assessment.get("scores") or {},
            "rationale": list(assessment.get("rationale") or []),
            "signal": scope.get("signal"),
            "dataset": scope.get("dataset"),
            "window": {k: scope.get(k) for k in ("start", "end", "in_sample_end")},
            "target_portfolio": {
                "as_of": exposure.get("as_of"),
                "names": len(exposure.get("positions") or []),
                "gross": exposure.get("gross"),
                "net": exposure.get("net"),
                "max_abs_weight": exposure.get("max_abs_weight"),
                "beta": exposure.get("beta"),
            },
            "approval": facts.get("approval"),
            "staging": facts.get("staging") or {},
        }

    @staticmethod
    def _research_alternatives(assessment: dict[str, Any]) -> list[str]:
        metrics = assessment.get("metrics") or {}
        flags = set(assessment.get("flags") or [])
        alts: list[str] = []

        def number(key: str) -> float:
            value = metrics.get(key)
            return float(value) if isinstance(value, (int, float)) and finite(value) else float("nan")

        cost_share, ratio, concentration = number("cost_share"), number("sharpe_ratio_oos_is"), number("pnl_concentration_top3")
        if "OVERFIT" in flags:
            alts.append(
                "The signal may be regime-specific: the in-sample premium did not persist out-of-sample, "
                "so a longer out-of-sample period would be needed to distinguish decay from a temporary regime."
            )
        if "NO_ALPHA" in flags:
            alts.append(
                "A weak cross-sectional effect may fail to reach significance in a universe of this size; "
                "a broader universe would tighten the IC estimate."
            )
        if "OVERFIT" not in flags and finite(ratio) and ratio < 0.8:
            alts.append(
                f"Out-of-sample Sharpe is {ratio:.2f} of in-sample; part of the in-sample fit may not persist."
            )
        if "COST_DRAG" not in flags and finite(cost_share) and cost_share > 0.3:
            alts.append(
                f"Costs consume {cost_share * 100:.0f}% of gross return; rebalancing less often would reduce "
                "the cost drag at the expense of signal freshness."
            )
        if "CONCENTRATION" not in flags and finite(concentration) and concentration > 0.35:
            alts.append(
                f"The top three names carry {concentration * 100:.0f}% of positive P&L; the result is partly "
                "name-specific."
            )
        return alts[:4]

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


def _default_research_summary(assessment: dict[str, Any]) -> str:
    verdict = assessment.get("verdict", "REJECT")
    flags = list(assessment.get("flags") or [])
    if verdict == "PROMOTE":
        return "The signal clears every promotion hurdle: predictive in-sample, robust out-of-sample, net of costs and within limits."
    return f"The signal is not promoted: {', '.join(flags) if flags else 'it failed the promotion hurdles'}."


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
