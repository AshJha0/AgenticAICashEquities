"""Report agent: the LLM writes the explanation; the numbers come from evidence.

The model names a figure as a typed placeholder, ``{fact.key}`` (for example
``{metrics.window.slippage_vs_vwap_bps}``), and code renders the value with its unit (A1). A
free-text *number audit* is the backstop for figures the model writes itself: each must trace to
the structured facts by a unit-matched conversion and without a sign flip. An evidence-id audit and
a citation span audit run alongside it. Prose that fails any of them is not shown: the report
carries the deterministic template narrative instead, the warnings explain why, and the rejected
prose goes only to the audit log (A2).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ceap.agents.base import (
    REQUIRED_INVESTIGATION_AGENTS,
    BaseAgent,
    degradation_records,
    finite,
    llm_disabled,
)
from ceap.analytics.hypotheses import table_groups
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.common import utc_now
from ceap.domain.evidence import Evidence, document_citation
from ceap.domain.findings import Finding
from ceap.domain.reports import InvestigationReport
from ceap.domain.research import ResearchFlag, ResearchScope
from ceap.harness.cancellation import TaskCancelled
from ceap.harness.memory import AuditHook, InvestigationMemory
from ceap.llm.client import LLMClient, _mock_narrative, token_view
from ceap.llm.models import (
    RUNG_DETERMINISTIC,
    RUNG_KILL_SWITCH,
    Degradation,
    LLMRequest,
    LLMResponse,
)
from ceap.llm.prompts import (
    DATA_BLOCK_NOTE,
    REPORT_SYSTEM_PROMPT,
    REPORTER_CLAIMS_SYSTEM_PROMPT,
    REPORTER_PROSE_SYSTEM_PROMPT,
    RESEARCH_REPORT_SYSTEM_PROMPT,
    data_block,
    get_prompt,
    mask_nonces,
)

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
KILL_SWITCH_BANNER = (
    "LLM DISABLED (CEAP_LLM_DISABLED=true): plan, critique and narrative are deterministic templates; "
    "no model was called."
)
STAGE_TOOL_ID = "execution.stage_orders"
MAX_CLAIMS = 24


class Claim(BaseModel):
    """R3C 3C.4 call 1: one claim the report will make, tied to facts and evidence."""

    model_config = ConfigDict(extra="forbid")
    section: Literal["EXECUTIVE SUMMARY", "FINDINGS", "CONCLUSION", "ALTERNATIVE EXPLANATIONS"]
    text: str = Field(max_length=600)
    fact_keys: list[str] = Field(max_length=12)
    evidence_ids: list[str] = Field(max_length=12)
    needs_policy_support: bool


class ClaimsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[Claim] = Field(max_length=MAX_CLAIMS)


class ReportAgent(BaseAgent):
    agent_id = "reporter"
    agent_type = "reporter"

    def __init__(self, llm: LLMClient, audit: AuditHook | None = None) -> None:
        self.llm = llm
        self._audit = audit  # A2: where rejected prose goes (the hash-chained audit log)

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
        degradations: list[Degradation] = []
        response: LLMResponse | None = None
        narrative = ""
        claims: dict[str, list[dict[str, Any]]] = {"kept": [], "dropped": []}
        if llm_disabled(self.llm):
            degradations.append(
                Degradation(RUNG_KILL_SWITCH, "LLM_DISABLED", "kill switch on: templated narrative used")
            )
        else:
            try:
                if research:
                    response = await self.call_llm(
                        context,
                        self.llm,
                        LLMRequest(
                            system_prompt=system_prompt,
                            messages=[{"role": "user", "content": report_request_content(facts)}],
                            purpose="narrative",  # max_tokens 8000 and effort from model_pins.toml
                        ),
                    )
                else:
                    # R3C 3C.4: call 1 claims (structured), call 2 prose with citations
                    claims = await self._claims(context, facts, degradations)
                    response = await self.call_llm(context, self.llm, prose_request(claims["kept"], facts))
                degradations.extend(response.degradations)
                if response.stop_reason in ("max_tokens", "refusal") or not response.content.strip():
                    degradations.append(
                        Degradation(
                            RUNG_DETERMINISTIC,
                            "LLM_TEMPLATE_NARRATIVE",
                            f"templated narrative used (stop_reason={response.stop_reason})",
                        )
                    )
                else:
                    narrative = response.content.strip()
            except (TaskCancelled, asyncio.CancelledError):
                raise
            except Exception as exc:  # noqa: BLE001 - rung 3: the template always produces a report
                degradations.append(
                    Degradation(
                        RUNG_DETERMINISTIC,
                        "LLM_TEMPLATE_NARRATIVE",
                        f"templated narrative used ({type(exc).__name__}: {str(exc)[:200]})",
                    )
                )
        # A1/A2: audit the model's own prose before anything code-written is added to it. Numbers
        # the model names as {fact.key} are rendered by code; the free-text audit is the backstop.
        number_warnings: list[str] = []
        id_warnings: list[str] = []
        citation_warnings: list[str] = []
        cited: list[Evidence] = []
        suppressed: dict[str, Any] | None = None
        if narrative:
            rendered, placeholder_warnings = render_placeholders(narrative, facts)
            number_warnings = [*placeholder_warnings, *audit_numbers(strip_placeholders(narrative), facts)]
            id_warnings = audit_evidence_ids(narrative, set(memory.evidence))
            if not research and response is not None:
                # R3C 3C.4: span audit of every citation, DOC evidence for each verified one
                cited, citation_warnings = audit_citations(response.citations, facts)
                id_warnings = [*id_warnings, *citation_warnings]  # a fabricated quote is blocking
            if number_warnings or id_warnings:
                suppressed = self._suppress(context, narrative, number_warnings, id_warnings)
                degradations.append(
                    Degradation(
                        RUNG_DETERMINISTIC,
                        "LLM_NARRATIVE_SUPPRESSED",
                        f"model narrative failed the audit ({len(number_warnings)} number, "
                        f"{len(id_warnings)} evidence warning(s)); templated narrative used",
                    )
                )
                narrative, cited = "", []
            else:
                narrative = rendered
        if not narrative:
            narrative = _mock_narrative(facts)
        if facts.get("hypotheses"):
            narrative = f"{narrative}\n\n{hypotheses_section(facts['hypotheses'])}"
        incomplete = _failed_required_agents(outputs) if not research else []
        if incomplete:
            narrative = f"{incomplete_banner(incomplete)}\n\n{narrative}"
        if any(d.rung == RUNG_KILL_SWITCH for d in degradations):
            narrative = f"{KILL_SWITCH_BANNER}\n\n{narrative}"
        sections = _split_sections(narrative)
        knowledge: dict[str, Any] = {}
        if not research:
            knowledge = {
                "corpus_hash": facts.get("knowledge_corpus_hash"),
                "claims": claims["kept"],
                "dropped_claims": claims["dropped"],
                "unsupported_policy_claims": unsupported_policy_claims(claims["kept"], cited),
                "citations": [e.id for e in cited],
                "citation_warnings": citation_warnings,
            }
            if facts.get("knowledge_corpus_hash"):
                narrative = f"{narrative}\n\nKnowledge corpus sha256:{facts['knowledge_corpus_hash']}"
        critique_out = {
            **critique,
            "narrative_number_warnings": number_warnings,
            "narrative_evidence_warnings": id_warnings,
            "llm_model": response.model if response else None,
            "incomplete_analysis": incomplete,
        }
        if suppressed is not None:
            critique_out["narrative_suppressed"] = suppressed  # hash and reasons only, never the prose
        if knowledge:
            critique_out["knowledge"] = knowledge
        if facts.get("hypotheses"):
            critique_out["hypotheses"] = facts["hypotheses"]  # R3B 3B.2: verdicts from code only
        if facts.get("prior_cases"):
            critique_out["prior_cases"] = facts["prior_cases"]  # R3B 3B.7: cited, read-only
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
                executive_summary=(
                    incomplete_banner(incomplete)
                    if incomplete
                    else sections.get("EXECUTIVE SUMMARY") or _default_summary(attribution)
                ),
                primary_observations=tuple(
                    f.statement for f in findings if f.attributes.get("role") != "rejected"
                )[:8],
                conclusion=(
                    incomplete_banner(incomplete)
                    if incomplete
                    else sections.get("CONCLUSION") or _default_conclusion(attribution)
                ),
                alternative_explanations=tuple(alternatives),
                findings=tuple(findings),
                evidence=(*memory.evidence_list(), *cited),
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
            "llm_model": response.model if response else None,
            "tokens": token_view(response),  # A20: labelled when the counts are a mock estimate
        }
        if degradations:
            output["degradations"] = degradation_records(degradations)
        if incomplete:
            output["incomplete_analysis"] = incomplete
        return AgentResult(
            agent_id=self.id,
            success=True,
            output=output,
            evidence=tuple(cited),
            summary=f"report generated ({len(narrative)} chars, {len(number_warnings)} number warnings)",
        )

    def _suppress(
        self, context: AgentContext, prose: str, number_warnings: list[str], id_warnings: list[str]
    ) -> dict[str, Any]:
        """A2: the rejected prose goes only to the audit record; the report keeps its hash and reasons."""
        record = {
            "sha256": hashlib.sha256(prose.encode("utf-8")).hexdigest(),
            "chars": len(prose),
            "number_warnings": list(number_warnings),
            "evidence_warnings": list(id_warnings),
            "audited": self._audit is not None,
        }
        if self._audit is not None:
            self._audit(
                self.agent_id,
                "reporter.narrative_rejected",
                context.task_id,
                {**record, "execution_id": context.execution_id, "prose": prose},
            )
        return record

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
            "title": f"{w.symbol} EXECUTION INVESTIGATION {_short(w.start)}-{_short(w.end)} New York",
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
            "knowledge_corpus_hash": self._corpus_hash(memory),
            "evidence_ids": sorted(memory.evidence),
            "hypotheses": table_groups(memory.scratch.get("hypotheses"))
            if memory.scratch.get("hypotheses")
            else {},
            "prior_cases": list(((outputs.get("scope") or {}).get("context_cases")) or []),
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
            "knowledge_corpus_hash": self._corpus_hash(memory),
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

        cost_share, ratio, concentration = (
            number("cost_share"),
            number("sharpe_ratio_oos_is"),
            number("pnl_concentration_top3"),
        )
        if ResearchFlag.OOS_DECAY.value in flags:
            alts.append(
                "The signal may be regime-specific: the in-sample premium did not persist out-of-sample, "
                "so a longer out-of-sample period would be needed to distinguish decay from a temporary regime."
            )
        if "NO_ALPHA" in flags:
            alts.append(
                "A weak cross-sectional effect may fail to reach significance in a universe of this size; "
                "a broader universe would tighten the IC estimate."
            )
        if ResearchFlag.OOS_DECAY.value not in flags and finite(ratio) and ratio < 0.8:
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
                # R3C 3C.4/3C.5: what a citation is checked against, and which revision it was
                "chunk_id": h.get("chunk_id"),
                "document": h.get("document"),
                "version": h.get("version"),
                "effective_date": h.get("effective_date"),
                "text": str(h.get("text", "")),
            }
            for h in (out.data.get("items") or [])[:3]
        ]

    @staticmethod
    def _corpus_hash(memory: Any) -> str | None:
        out = memory.find("knowledge.search_documents")
        value = out.data.get("corpus_hash") if out else None
        return str(value) if value else None

    async def _claims(
        self, context: AgentContext, facts: dict[str, Any], degradations: list[Degradation]
    ) -> dict[str, list[dict[str, Any]]]:
        """R3C 3C.4 call 1: structured claims, validated deterministically. Never fails the report."""
        prompt = get_prompt("reporter_claims")
        request = LLMRequest(
            system_prompt=REPORTER_CLAIMS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": report_request_content(facts)}],
            purpose="claims",  # pins fall back to the narrative pin
            metadata={"prompt": prompt.ref(), "task_id": context.task_id},
            output_model=ClaimsOutput,
        )
        try:
            response = await self.call_llm(context, self.llm, request)
            degradations.extend(response.degradations)
            raw = response.parsed
            if raw is None:
                raise ValueError(f"no structured claims (stop_reason={response.stop_reason})")
            parsed = ClaimsOutput.model_validate(raw)
        except (TaskCancelled, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001 - the prose call still runs, from the facts alone
            degradations.append(
                Degradation(
                    RUNG_DETERMINISTIC,
                    "LLM_CLAIMS_UNAVAILABLE",
                    f"claims call unusable ({type(exc).__name__}: {str(exc)[:160]}); prose from facts only",
                )
            )
            return {"kept": [], "dropped": []}
        kept, dropped = validate_claims([c.model_dump() for c in parsed.claims], facts)
        return {"kept": kept, "dropped": dropped}

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


def _failed_required_agents(outputs: dict[str, Any]) -> list[str]:
    """Required specialists that did not succeed; their absence must never read as a pass (PLAN 1.6)."""
    return [a for a in REQUIRED_INVESTIGATION_AGENTS if not (outputs.get(a) or {}).get("success", False)]


def incomplete_banner(failed: list[str]) -> str:
    return (
        f"INCOMPLETE ANALYSIS: required agents did not complete ({', '.join(failed)}). "
        "No conclusion is drawn; this report must not be read as 'no material anomaly'."
    )


def _default_summary(attribution: dict[str, Any]) -> str:
    primary = attribution.get("primary")  # no default: a missing attribution is not "NORMAL"
    if not primary:
        return "No cause attribution is available; no conclusion about execution quality is drawn."
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
# A list marker at the start of a line ("1. ", "2) ") is layout, not a figure.
_LIST_MARKER = re.compile(r"(?m)^[ \t]*(?:[-*][ \t]+)?\d{1,3}[.)](?=[ \t])")
# The unit written right after a number decides which conversions of a fact may explain it (A1).
_UNIT = re.compile(
    r"\s*(?P<unit>%|pct\b|percent\b|per ?cent\b|bps\b|bp\b|basis points?\b|x\b)", re.IGNORECASE
)
# Direction words that make an unsigned magnitude agree with a negative fact ("fell by 12.3 bps").
_DOWN_WORDS = re.compile(
    r"\b(?:decreas\w*|declin\w*|fell|fall\w*|lower\w*|down|drop\w*|reduc\w*|narrow\w*|shr[ai]nk\w*|"
    r"negative|minus|below)\b",
    re.IGNORECASE,
)
_DIRECTION_WINDOW = 48  # characters before the number searched for a direction word
_FRACTION_MAX = 1.5  # a fact above this is not treated as a fraction for % / bps conversions
_BPS_KEY = re.compile(r"(?:_bps|bps_\w+|_bp)$")
_PCT_KEY = re.compile(r"(?:_pct|_percent|pct_\w+)$")
_LITERAL = "#text"  # key for numbers found inside fact strings


def _unit_of(text: str, end: int) -> str:
    m = _UNIT.match(text, end)
    if not m:
        return ""
    unit = m.group("unit").lower().replace(" ", "")
    if unit in ("%", "pct", "percent"):
        return "pct"
    if unit.startswith(("bps", "bp", "basispoint")):
        return "bps"
    return "x"


def _collect_facts(obj: Any, out: list[tuple[str, float]], key: str = "") -> None:
    """Every number in ``facts`` with the key it sits under (its unit is read from the key name)."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return
        out.append((key, float(obj)))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _collect_facts(v, out, str(k))
    elif isinstance(obj, (list, tuple)):
        out.append((f"{key}.__len__", float(len(obj))))  # "three findings" is a count of a fact list
        for v in obj:
            _collect_facts(v, out, key)
    elif isinstance(obj, str):
        for m in _NUM.finditer(_ISO.sub(" ", obj)):
            try:
                # a figure quoted inside fact text was already written with its unit by code
                out.append((_LITERAL, float(m.group().replace(",", ""))))
            except ValueError:
                pass


def _candidates(key: str, v: float, unit: str) -> set[float]:
    """Values a fact may be written as, given the unit next to the number (unit-matched only, A1)."""
    if key == _LITERAL:
        return {v}  # quoted text is repeated as written, never rescaled
    is_bps, is_pct = bool(_BPS_KEY.search(key)), bool(_PCT_KEY.search(key))
    # a ratio (window over baseline) may be written as a percent change: 3.5 -> +250%
    is_ratio = not (is_bps or is_pct)
    fraction = not (is_bps or is_pct) and abs(v) <= _FRACTION_MAX
    if unit == "bps":
        out = {v} if is_bps or not (is_pct or fraction) else set()
        if fraction:
            out.add(v * 10_000.0)  # fraction -> basis points
        if is_pct:
            out.add(v * 100.0)  # percent -> basis points
        return out
    if unit == "pct":
        out = {v} if is_pct or not (is_bps or fraction) else set()
        if fraction:
            out.add(v * 100.0)  # fraction -> percent
        if is_ratio:
            out.add((v - 1.0) * 100.0)  # ratio -> percent change
        if is_bps:
            out.add(v / 100.0)  # basis points -> percent
        return out
    return {v}  # no unit, or "x": the fact as it stands


def _traced(
    n: float, decimals: int, signed: bool, down: bool, facts: list[tuple[str, float]], unit: str
) -> bool:
    for key, v in facts:
        for c in _candidates(key, v, unit):
            if round(c, decimals) == round(n, decimals):
                return True
            # an unsigned magnitude may stand for a negative fact only when a direction word agrees
            if not signed and down and c < 0 and round(-c, decimals) == round(n, decimals):
                return True
    return False


def audit_numbers(narrative: str, facts: dict[str, Any]) -> list[str]:
    """Return the numbers in ``narrative`` that cannot be traced to ``facts`` (A1 backstop).

    A number traces when it equals a fact, or a conversion of a fact that matches the unit written
    after it: a fraction as a percent or basis points, a ratio as a percent change, basis points as a
    percent. A sign is never flipped: "-12.34" does not trace to 12.34, and an unsigned "12.34" traces
    to -12.34 only when a direction word ("fell", "lower", ...) comes just before it. There is no
    small-integer exemption; only a list marker at the start of a line is skipped.
    """
    fact_numbers: list[tuple[str, float]] = []
    _collect_facts(facts, fact_numbers)
    warnings: list[str] = []
    text = _LIST_MARKER.sub(
        lambda m: " " * len(m.group()), _ISO.sub(lambda m: " " * len(m.group()), narrative)
    )
    for m in _NUM.finditer(text):
        token = m.group()
        try:
            n = float(token.replace(",", ""))
        except ValueError:
            continue
        decimals = len(token.split(".")[1]) if "." in token else 0
        signed = token[0] in "+-"
        down = bool(_DOWN_WORDS.search(text[max(0, m.start() - _DIRECTION_WINDOW) : m.start()]))
        if _traced(n, decimals, signed, down, fact_numbers, _unit_of(text, m.end())):
            continue
        warnings.append(f"number {token} not traceable to structured facts")
    return warnings[:25]


# ------------------------------------------------------------------ A1 typed placeholders
_PLACEHOLDER = re.compile(r"\{(?:fact\.)?(?P<key>[A-Za-z_][\w]*(?:\.(?:[A-Za-z_]\w*|\d+))*)\}")


def _lookup(facts: dict[str, Any], path: str) -> Any:
    node: Any = facts
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise KeyError(path)
    return node


def format_fact(key: str, value: Any) -> str:
    """Code renders a fact with its unit; the model only names it (A1)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return "n/a"
    leaf = key.rsplit(".", 1)[-1]
    if _BPS_KEY.search(leaf):
        return f"{value:+.2f} bps"
    if _PCT_KEY.search(leaf):
        return f"{value:.2f}%"
    if isinstance(value, int):
        return f"{value:d}"
    return f"{value:.4f}".rstrip("0").rstrip(".") if abs(value) < 1_000 else f"{value:,.2f}"


def render_placeholders(text: str, facts: dict[str, Any]) -> tuple[str, list[str]]:
    """Replace each ``{fact.key}`` with the fact rendered by code. Unknown keys are warnings."""
    warnings: list[str] = []

    def sub(m: re.Match[str]) -> str:
        key = m.group("key")
        try:
            value = _lookup(facts, key)
        except KeyError:
            warnings.append(f"placeholder {{{key}}} not in structured facts")
            return m.group()
        if isinstance(value, (dict, list)):
            warnings.append(f"placeholder {{{key}}} is not a single value")
            return m.group()
        return format_fact(key, value)

    return _PLACEHOLDER.sub(sub, text), warnings


def strip_placeholders(text: str) -> str:
    """The model's own words: placeholders are code-rendered, so the free-text audit skips them."""
    return _PLACEHOLDER.sub(" ", text)


# Only ids shaped like an actual Evidence record (see ceap.domain.evidence._PREFIX) are audited;
# other id-shaped tokens in the facts (e.g. an approval request id, "APR-...") are legitimate
# and are not expected to resolve as evidence.
_EVIDENCE_PREFIXES = (
    "MARKET",
    "ORDER",
    "EXEC",
    "BOOK",
    "ENG",
    "LOG",
    "DOC",
    "TCA",
    "CHANGE",
    "RISK",
    "SIG",
    "BT",
)
_ID = re.compile(r"\b(?:" + "|".join(_EVIDENCE_PREFIXES) + r")-[0-9a-f]{8}\b")


def audit_evidence_ids(narrative: str, known: set[str]) -> list[str]:
    return [f"evidence id {m} not found" for m in sorted(set(_ID.findall(narrative))) if m not in known]


# ------------------------------------------------------------------ R3B helpers


def report_request_content(facts: dict[str, Any]) -> str:
    """R3B 3B.5: the facts travel in a data block; retrieved policy text gets one block per chunk."""
    policy = facts.get("policy_context") or []
    head = {k: v for k, v in facts.items() if k != "policy_context"}
    parts = [
        DATA_BLOCK_NOTE,
        "Write the report from these facts:",
        data_block("evidence", "facts", json.dumps({**head, "policy_context": policy}, default=str)),
    ]
    for i, hit in enumerate(policy):
        if isinstance(hit, dict):
            parts.append(data_block("rag", f"policy-{i + 1}", hit))
    return "\n\n".join(parts)


def hypotheses_section(groups: dict[str, list[dict[str, Any]]]) -> str:
    """R3B 3B.2: the hypothesis table as text, written by code from the deterministic verdicts."""
    titles = (
        ("SUPPORTED", "Supported hypotheses"),
        ("REFUTED", "Refuted hypotheses"),
        ("UNTESTED", "Untested hypotheses"),
    )
    lines = ["HYPOTHESES", ""]
    for key, title in titles:
        rows = groups.get(key) or []
        lines.append(f"{title}: {len(rows)}")
        for r in rows:
            evidence = ", ".join(r.get("evidence_ids") or []) or "none"
            # numbers in this section are the facts' own statistics, so the number audit holds
            detail = f"score {r['statistic']}" if r.get("statistic") is not None else r.get("reason", "")
            lines.append(f"- {r['name']}: {detail} (evidence: {evidence})")
    return "\n".join(lines)


# ------------------------------------------------------------------ R3C 3C.4 helpers


def _resolves(facts: dict[str, Any], path: str) -> bool:
    node: Any = facts
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False
    return True


def validate_claims(
    claims: list[dict[str, Any]], facts: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep a claim only if its fact keys resolve, its evidence ids are known and its numbers trace.

    A claim with neither fact keys nor evidence ids is dropped too. Dropped claims carry their
    reasons, so the report shows what the model wanted to say and why it was not allowed to.
    """
    known = set(facts.get("evidence_ids") or [])
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for c in claims[:MAX_CLAIMS]:
        reasons = []
        if not c.get("fact_keys") and not c.get("evidence_ids"):
            reasons.append("no fact keys and no evidence ids")
        reasons += [
            f"fact key {k!r} not in facts" for k in c.get("fact_keys") or [] if not _resolves(facts, k)
        ]
        reasons += [f"evidence id {e!r} unknown" for e in c.get("evidence_ids") or [] if e not in known]
        text = str(c.get("text", ""))
        reasons += render_placeholders(text, facts)[1]  # A1: a placeholder must name a fact
        reasons += audit_numbers(strip_placeholders(text), facts)
        if reasons:
            dropped.append({**c, "reasons": reasons})
        else:
            kept.append(c)
    return kept, dropped


def document_block(hit: dict[str, Any], last: bool) -> dict[str, Any]:
    """One knowledge chunk as a citable ``document`` block of custom content (one content block)."""
    block: dict[str, Any] = {
        "type": "document",
        "source": {"type": "content", "content": [{"type": "text", "text": document_text(hit)}]},
        "title": f"{hit.get('title')} / {hit.get('section')}"[:250],
        "context": f"chunk {hit.get('chunk_id')} version {hit.get('version')} effective {hit.get('effective_date')}",
        "citations": {"enabled": True},
    }
    if last:  # one breakpoint after the last document caches them all (2 of the 4 allowed, with system)
        block["cache_control"] = {"type": "ephemeral"}
    return block


def prose_request(claims: list[dict[str, Any]], facts: dict[str, Any]) -> LLMRequest:
    """R3C 3C.4 call 2: documents first (stable, cached), then the facts and claims (volatile).

    Citations are on and there is no structured output: the API does not allow both in one call.
    """
    policy = [p for p in (facts.get("policy_context") or []) if isinstance(p, dict) and p.get("text")]
    head = {
        **facts,
        "policy_context": [{k: v for k, v in p.items() if k != "text"} for p in policy],
    }
    text = "\n\n".join(
        [
            DATA_BLOCK_NOTE,
            "Write the report from these facts and validated claims:",
            data_block("evidence", "facts", json.dumps(head, default=str)),
            data_block("evidence", "claims", json.dumps(claims, default=str)),
        ]
    )
    blocks = [document_block(p, i == len(policy) - 1) for i, p in enumerate(policy)]
    return LLMRequest(
        system_prompt=REPORTER_PROSE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [*blocks, {"type": "text", "text": text}]}],
        purpose="narrative",
        metadata={"prompt": get_prompt("reporter_prose").ref()},
    )


def document_text(hit: dict[str, Any]) -> str:
    """The exact text of a chunk's document block: the chunk wrapped as untrusted ``rag`` data (3B.5).

    The citation audit compares ``cited_text`` with this string, byte for byte after whitespace
    normalisation; the minted evidence stores the unwrapped chunk text.
    """
    return data_block("rag", str(hit.get("chunk_id") or "chunk"), str(hit.get("text", "")))


def _norm(text: str) -> str:
    return " ".join(str(text).split())


def audit_citations(citations: Any, facts: dict[str, Any]) -> tuple[list[Evidence], list[str]]:
    """Check each citation's ``cited_text`` equals the cited chunk's text; mint DOC evidence for each.

    ``document_index`` maps to the chunk sent at that position in the prose call. A citation that
    points nowhere, or whose text is not the chunk text (a fabricated quote), is a warning and mints
    nothing.
    """
    policy = [p for p in (facts.get("policy_context") or []) if isinstance(p, dict) and p.get("text")]
    corpus = str(facts.get("knowledge_corpus_hash") or "")
    minted: list[Evidence] = []
    warnings: list[str] = []
    seen: set[tuple[Any, ...]] = set()
    for c in citations or ():
        idx = c.get("document_index")
        if (
            c.get("type") != "content_block_location"
            or not isinstance(idx, int)
            or not 0 <= idx < len(policy)
        ):
            warnings.append(f"citation to unknown document {idx!r}")
            continue
        chunk = policy[idx]
        cited = str(c.get("cited_text") or "")
        # the block's nonce is random per call, so the comparison masks it (A8)
        if _norm(mask_nonces(cited)) != _norm(mask_nonces(document_text(chunk))):
            warnings.append(
                f"citation span mismatch for chunk {chunk.get('chunk_id')}: cited text is not the chunk text"
            )
            continue
        key = (idx, c.get("text_start"), c.get("text_end"))
        if key in seen:
            continue
        seen.add(key)
        span = (int(c.get("text_start") or 0), int(c.get("text_end") or 0))
        minted.append(document_citation(chunk, str(chunk["text"]), span, str(c.get("text") or ""), corpus))
    return minted, warnings


_WORD = re.compile(r"[a-z0-9]+")
CLAIM_SPAN_OVERLAP = 0.6  # share of a claim's words that its citing sentence must carry


def _words(text: str) -> set[str]:
    return set(_WORD.findall(unicodedata.normalize("NFKC", str(text)).lower()))


def claim_citations(claim: dict[str, Any], cited: list[Evidence]) -> list[str]:
    """A7: the verified citations whose citing sentence carries this claim.

    The citing sentence (``cited_sentence``, the narrative span the citation is attached to) must
    contain the claim text, or share at least ``CLAIM_SPAN_OVERLAP`` of the claim's words. A
    citation elsewhere in the prose does not support a claim it is not attached to.
    """
    claim_norm = _norm(str(claim.get("text", ""))).lower()
    claim_words = _words(claim_norm)
    out: list[str] = []
    for ev in cited:
        sentence = _norm(str(ev.attributes.get("cited_sentence") or "")).lower()
        if not sentence or not claim_words:
            continue
        if claim_norm in sentence or len(claim_words & _words(sentence)) >= CLAIM_SPAN_OVERLAP * len(
            claim_words
        ):
            out.append(ev.id)
    return out


def unsupported_policy_claims(claims: list[dict[str, Any]], cited: list[Evidence]) -> list[str]:
    """Claims flagged ``needs_policy_support`` that lack their own verified citation span (A7)."""
    return [
        str(c.get("text", ""))[:200]
        for c in claims
        if c.get("needs_policy_support") and not claim_citations(c, cited)
    ]
