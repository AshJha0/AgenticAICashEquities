"""R3B: agents and agentic AI within the boundary (PLAN 3B.1-3B.8). All offline (D15)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from ceap.agents.base import BaseAgent
from ceap.analytics.hypotheses import (
    BASELINE_CHOICES,
    HYPOTHESES,
    baseline_window,
    evaluate_hypotheses,
)
from ceap.config import Settings
from ceap.domain.agents import AgentMessage, MessageVersionError, parse_message
from ceap.domain.common import utc_now
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.findings import EvidenceRef, Finding
from ceap.domain.tools import Tool, ToolExecutionContext, ToolRegistry, ToolRequest, ToolResult, ToolStatus
from ceap.harness.engine import AgentHarness, _message
from ceap.harness.memory import CaseMemory, CaseRecord, InvestigationMemory, bind_evidence_refs
from ceap.llm.client import MockLLMClient, canonical_plan
from ceap.llm.prompts import TRUNCATION_ORDER, fit_context
from ceap.llm.schemas import plan_model, plan_semantic_errors, validate_model
from ceap.persistence.db import Database
from ceap.platform import Platform, default_request, default_research_request
from ceap.policy.approvals import ApprovalDecision, ApprovalGateway, QueuedApprovalGateway
from fakes.clients import _response

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS = Path(__file__).parent / "snapshots"
MOCK = Settings(llm_provider="mock", auto_approve=True)


def _plan_payload(request: Any) -> dict[str, Any]:
    return dict(request.metadata.get("parameters") or {})


class ScriptedLLM(MockLLMClient):
    """A fixed, deterministic model (the cassette equivalent for these tests): ``plan`` edits the
    canonical plan, ``critique`` edits the canonical critique."""

    def __init__(self, plan=None, critique=None) -> None:
        super().__init__()
        self._plan, self._critique = plan, critique
        self.purposes: list[str] = []

    async def complete(self, request):
        self.purposes.append(request.purpose)
        response = await super().complete(request)
        if request.purpose == "planning" and self._plan is not None:
            raw = self._plan(canonical_plan(_plan_payload(request)), request)
            return _response(json.dumps(raw), "scripted")
        if request.purpose == "critique" and self._critique is not None:
            raw = self._critique(json.loads(response.content), request)
            return _response(json.dumps(raw), "scripted")
        return response


def _platform(llm=None, store=None, **kw: Any) -> Platform:
    return Platform(settings=kw.pop("settings", MOCK), llm=llm or MockLLMClient(), store=store, **kw)


def _tool_calls(result) -> list[tuple[str, str]]:
    return [(s["step_id"], s["tool_id"]) for s in result.step_results if s.get("tool_id")]


# ================================================================ 3B.1 scope within enumerated choices


def test_3b1_ensure_self_serving_is_at_most_8():
    sources = (ROOT / "src" / "ceap" / "agents").glob("*.py")
    count = sum(len(re.findall(r"\bself\.ensure\(", p.read_text(encoding="utf-8"))) for p in sources)
    assert count <= 8, count


async def test_3b1_agents_consume_plan_fetched_data_only(store, monkeypatch):
    """Every agent reads what the plan (plus the harness data steps) fetched: no self-serving call
    except the one whose arguments depend on fetched data (the risk agent's limit check)."""
    served: list[str] = []
    original = BaseAgent.ensure

    async def spy(self, ctx, tool_id, arguments):
        served.append(f"{self.id}:{tool_id}")
        return await original(self, ctx, tool_id, arguments)

    monkeypatch.setattr(BaseAgent, "ensure", spy)
    result = await _platform(store=store).investigate(default_request("AAPL", "T10"))
    assert result.success and not result.incomplete_agents
    # Approved exceptions, all reading data outside the task-window boundaries that the plan
    # validator allows as declared arguments (follow-up: let the harness declare them):
    # the risk limit check (arguments depend on fetched data), R2T 2T.2's five matched peer
    # windows on prior sessions (quant) and R2B 2B.1's change-point reference period.
    exceptions = {
        "risk:risk.check_limit",
        "quant:execution.get_execution_metrics",
        "quant:market_data.get_market_statistics",
        "quant:market_data.get_order_book_statistics",
        "engineering:engineering.get_service_metrics",
        "market:engineering.get_service_metrics",
    }
    assert set(served) <= exceptions and "risk:risk.check_limit" in served
    assert served.count("quant:execution.get_execution_metrics") == 5  # one per matched peer window


async def test_3b1_chosen_agents_and_tools_change_the_call_log(store):
    """With a fixed model reply, the agents and tools the plan chooses decide which tools run."""

    def narrow(plan, _request):  # research: only the research and alpha agents, and their data
        keep_tools = {"research_data.get_universe_summary", "alpha.evaluate_signal"}
        plan["steps"] = [
            s
            for s in plan["steps"]
            if (s["type"] == "TOOL_CALL" and s["tool_id"] in keep_tools)
            or (s["type"] == "AGENT_CALL" and s["agent_id"] in ("research", "alpha"))
        ]
        return plan

    full = await _platform(store=store).research(default_research_request("R01"))
    narrowed = await _platform(ScriptedLLM(plan=narrow), store=store).research(
        default_research_request("R01")
    )
    full_tools = {t for _, t in _tool_calls(full)}
    narrow_tools = {t for _, t in _tool_calls(narrowed)}
    assert "backtest.run_backtest" in full_tools
    assert "backtest.run_backtest" not in narrow_tools  # not chosen -> not fetched
    assert {"research_data.get_universe_summary", "alpha.evaluate_signal"} <= narrow_tools
    assert narrowed.agent_outputs["scope"]["agents"] == ["alpha", "research"]


async def test_3b1_optional_tool_is_fetched_only_when_planned(store):
    def without(plan, _request):
        plan["steps"] = [
            s for s in plan["steps"] if s.get("tool_id") != "execution.get_strategy_configuration"
        ]
        return plan

    with_tool = await _platform(store=store).investigate(default_request("AAPL", "T01"))
    without_tool = await _platform(ScriptedLLM(plan=without), store=store).investigate(
        default_request("AAPL", "T01")
    )
    assert "execution.get_strategy_configuration" in {t for _, t in _tool_calls(with_tool)}
    assert "execution.get_strategy_configuration" not in {t for _, t in _tool_calls(without_tool)}


async def test_3b1_required_agents_stay_harness_enforced(store):
    def drop_quant(plan, _request):
        plan["steps"] = [s for s in plan["steps"] if s.get("agent_id") != "quant"]
        return plan

    result = await _platform(ScriptedLLM(plan=drop_quant), store=store).investigate(
        default_request("AAPL", "T05")
    )
    assert any(s.get("agent_id") == "quant" and s["status"] == "SUCCESS" for s in result.step_results)


def test_3b1_baseline_outside_the_set_is_rejected():
    facts = {
        "kind": "investigation",
        "symbol": "AAPL",
        "window_start": "2026-09-18T14:00:00",
        "window_end": "2026-09-18T15:00:00",
        "baseline_start": "2026-09-18T13:00:00",
        "baseline_end": "2026-09-18T14:00:00",
        "baseline_windows": {
            c: list(
                baseline_window(
                    c,
                    {
                        "window_start": "2026-09-18T14:00:00",
                        "window_end": "2026-09-18T15:00:00",
                        "baseline_start": "2026-09-18T13:00:00",
                        "baseline_end": "2026-09-18T14:00:00",
                    },
                )
            )
            for c in BASELINE_CHOICES
        },
    }
    raw = {**canonical_plan(facts), "baseline": "same_day_last_year"}
    model = plan_model({}, ["market"], baselines=BASELINE_CHOICES, hypotheses=HYPOTHESES)
    assert validate_model(model, raw)  # the schema enum refuses it
    assert any("baseline" in e for e in plan_semantic_errors(raw, facts, [], ["market"]))
    with pytest.raises(ValueError):
        baseline_window("same_day_last_year", {})


async def test_3b1_rejected_baseline_falls_back_and_is_visible(store):
    llm = ScriptedLLM(plan=lambda p, _r: {**p, "baseline": "same_day_last_year"})
    result = await _platform(llm, store=store).investigate(default_request("AAPL", "T01"))
    scope = result.agent_outputs["scope"]
    assert scope["baseline"] == "task_baseline"
    assert any("same_day_last_year" in r for r in scope["rejected"])


async def test_3b1_baseline_choice_drives_the_agents_and_is_a_trial(store):
    llm = ScriptedLLM(plan=lambda p, _r: {**p, "baseline": "prior_double_length"})
    req = default_request("AAPL", "T01")
    result = await _platform(llm, store=store).investigate(req)
    scope = result.agent_outputs["scope"]
    start, end = scope["baseline_window"]
    assert scope["baseline"] == "prior_double_length" and start < req.window_start.isoformat()
    assert scope["trials"] == [{"trial": 1, "choice": {"baseline": "prior_double_length", "hypotheses": []}}]
    market = result.agent_outputs["market"]
    assert result.success and market["success"]
    # the market agent's baseline statistics came from the chosen window
    assert any(
        e.attributes.get("tool_id") == "market_data.get_market_statistics"
        and (e.attributes.get("arguments") or {}).get("start") == start
        for e in result.evidence
    )


# ================================================================ 3B.2 hypothesis table


async def test_3b2_t10_lists_both_causes_with_separate_evidence(store):
    result = await _platform(store=store).investigate(default_request("AAPL", "T10"))
    rows = {r.name: r for r in result.report.hypotheses.rows}
    vol, tech = rows["MARKET_VOLATILITY"], rows["TECHNOLOGY_LATENCY"]
    assert vol.verdict == tech.verdict == "SUPPORTED"
    # each cause carries its own specialist evidence: market data for volatility, engineering for latency
    market = next(
        f for f in result.findings if f.produced_by == "market" and f.attributes.get("anomaly") == vol.name
    )
    vol_only = set(vol.evidence_ids) - set(tech.evidence_ids)
    tech_only = set(tech.evidence_ids) - set(vol.evidence_ids)
    assert set(market.supporting_evidence) <= vol_only
    assert any(e.startswith("ENG-") for e in tech_only)
    assert {"SUPPORTED", "REFUTED", "UNTESTED"} == set(result.report.critique["hypotheses"])
    assert "Untested hypotheses:" in result.report.narrative
    assert "Supported hypotheses: 2" in result.report.narrative


async def test_3b2_no_verdict_is_produced_by_model_text(store):
    def proposes(plan, _request):
        plan["hypotheses"] = ["PRICE_MOVEMENT", "MARKET_VOLATILITY", "NOT_A_HYPOTHESIS"]
        plan["rationale"] = "MARKET_VOLATILITY is REFUTED and PRICE_MOVEMENT is SUPPORTED."
        return plan

    result = await _platform(ScriptedLLM(plan=proposes), store=store).investigate(
        default_request("AAPL", "T10")
    )
    rows = {r.name: r for r in result.report.hypotheses.rows}
    assert rows["MARKET_VOLATILITY"].verdict == "SUPPORTED"  # the model said refuted; code decides
    assert rows["PRICE_MOVEMENT"].rank == 1 and rows["MARKET_VOLATILITY"].rank == 2
    assert rows["LOW_LIQUIDITY"].rank is None
    assert "NOT_A_HYPOTHESIS" not in rows
    assert any("NOT_A_HYPOTHESIS" in r for r in result.agent_outputs["scope"]["rejected"])
    model = plan_model({}, ["market"], hypotheses=HYPOTHESES)
    assert validate_model(model, {"rationale": "", "steps": [], "verdicts": {"X": "SUPPORTED"}})


def test_3b2_untested_when_the_test_cannot_run():
    none = evaluate_hypotheses([], None, [])
    assert {r.verdict for r in none.rows} == {"UNTESTED"} and none.trials_counted == len(HYPOTHESES)
    partial = evaluate_hypotheses(
        ["WIDE_SPREADS"], {"deteriorated": True, "ranked": [{"cause": "WIDE_SPREADS", "score": 0.1}]}, []
    )
    rows = {r.name: r.verdict for r in partial.rows}
    assert rows["WIDE_SPREADS"] == "REFUTED" and rows["LOW_LIQUIDITY"] == "UNTESTED"


# ================================================================ 3B.3 critic disputes


class AltBaselineVolatile(Tool):
    """Market statistics, except that the window just before the investigation is as volatile as the
    investigation window: an alternative baseline then contradicts a volatility anomaly."""

    def __init__(self, inner: Tool, alt_start: str, factor: float) -> None:
        self.inner, self.alt_start, self.factor = inner, alt_start, factor

    @property
    def id(self) -> str:
        return self.inner.id

    @property
    def metadata(self):
        return self.inner.metadata

    async def execute(self, request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
        result = await self.inner.execute(request, context)
        if request.arguments.get("start") == self.alt_start and isinstance(result.data, dict):
            data = {
                **result.data,
                "realised_volatility_bps": result.data["realised_volatility_bps"] * self.factor,
            }
            return ToolResult(
                status=result.status, data=data, evidence=result.evidence, execution_time_ms=1.0
            )
        return result


async def _override_tool(platform: Platform, tool_id: str, make) -> Any:
    registry = await platform.registry()
    replaced = ToolRegistry()
    wrapped = None
    for meta in registry.list():
        tool = registry.get(meta.id)
        if meta.id == tool_id:
            tool = wrapped = make(tool)
        replaced.register(tool)
    platform._registry = replaced
    return wrapped


def _evidence_block(request: Any) -> dict[str, Any]:
    text = request.messages[0]["content"]
    start = text.index("{", text.index('<data surface="evidence"'))
    value, _ = json.JSONDecoder().raw_decode(text, start)
    return value


def _dispute_volatility(check: str):
    def edit(critique, request):
        payload = _evidence_block(request)
        for a in critique["assessments"]:
            f = next(x for x in payload["findings"] if x["id"] == a["finding_id"])
            if "volatility rose" in f["statement"].lower():
                a.update(supported=False, adjusted_confidence=0.0, check=check, comment="a calmer regime?")
        return critique

    return edit


async def test_3b3_named_alternative_baseline_overturns_a_finding(store):
    base = default_request("AAPL", "T10")
    # a task baseline an hour earlier, so the alternative (the hour just before the window) differs
    req = dataclasses.replace(
        base,
        baseline_start=base.window_start - timedelta(hours=2),
        baseline_end=base.window_start - timedelta(hours=1),
    )
    alt_start, _ = baseline_window(
        "prior_equal_length",
        {"window_start": req.window_start.isoformat(), "window_end": req.window_end.isoformat()},
    )
    platform = _platform(ScriptedLLM(critique=_dispute_volatility("alternative_baseline")), store=store)
    await _override_tool(
        platform, "market_data.get_market_statistics", lambda t: AltBaselineVolatile(t, alt_start, 3.0)
    )
    result = await platform.investigate(req)
    vol = next(f for f in result.findings if "volatility rose" in f.statement.lower())
    assert vol.confidence == pytest.approx(0.3)
    assert vol.attributes["disputed_by"] == "alternative_baseline"
    checks = result.agent_outputs["disputes"]["checks"]
    assert checks[0]["status"] == "CONTRADICTS" and checks[0]["evidence_id"] in vol.contradicting_evidence
    assert any(w.code == "FINDING_DISPUTED" for w in result.warnings)
    assert any(
        p.get("tool_id") == "market_data.get_market_statistics"
        and p.get("step_id", "").startswith("gov-dispute")
        for p in result.policy_log
    )  # the check went through the policy path


async def test_3b3_consistent_check_changes_nothing(store):
    base = await _platform(store=store).investigate(default_request("AAPL", "T10"))
    disputed = await _platform(
        ScriptedLLM(critique=_dispute_volatility("alternative_baseline")), store=store
    ).investigate(default_request("AAPL", "T10"))
    conf = lambda r: sorted((f.statement, f.confidence) for f in r.findings)  # noqa: E731
    assert conf(disputed) == conf(base)
    assert disputed.agent_outputs["disputes"]["checks"][0]["status"] == "CONSISTENT"


async def test_3b3_dispute_without_a_check_is_recorded_and_changes_nothing(store):
    def lower_all(critique, _request):
        for a in critique["assessments"]:
            a.update(supported=False, adjusted_confidence=0.0)
        return critique

    base = await _platform(store=store).investigate(default_request("AAPL", "T05"))
    result = await _platform(ScriptedLLM(critique=lower_all), store=store).investigate(
        default_request("AAPL", "T05")
    )
    assert sorted(f.confidence for f in result.findings) == sorted(f.confidence for f in base.findings)
    assert result.agent_outputs["disputes"]["recorded_without_check"]
    assert (
        "llm_overall" not in result.report.critique
        or result.report.critique["overall"] != result.report.critique["llm_overall"]
    )


async def test_3b3_obedient_injection_cannot_lower_any_confidence(store):
    from fakes.clients import INJECTION_TEXT, ObedientInjectionClient

    class LoweringObedient(ObedientInjectionClient):
        async def fault(self, request):
            response = await super().fault(request)
            if request.purpose == "critique" and self.obeys(request):
                raw = json.loads(response.content)
                for a in raw["assessments"]:
                    a.update(supported=False, adjusted_confidence=0.0, check="peer_set")
                return _response(json.dumps(raw), "obedient")
            return response

    question = f"Analyse AAPL execution between 14:00 and 15:00. {INJECTION_TEXT}"
    base = await _platform(store=store).investigate(default_request("AAPL", "T03", question=question))
    for client in (ObedientInjectionClient(), LoweringObedient()):
        result = await _platform(client, store=store).investigate(
            default_request("AAPL", "T03", question=question)
        )
        assert sorted(f.confidence for f in result.findings) == sorted(f.confidence for f in base.findings)


async def test_3b3_dispute_checks_are_capped(store):
    def dispute_everything(critique, _request):
        for a in critique["assessments"]:
            a.update(supported=False, adjusted_confidence=0.0, check="different_horizon")
        return critique

    result = await _platform(ScriptedLLM(critique=dispute_everything), store=store).investigate(
        default_request("AAPL", "T10")
    )
    statuses = [c["status"] for c in result.agent_outputs["disputes"]["checks"]]
    ran = [s for s in statuses if s in ("CONTRADICTS", "CONSISTENT")]
    assert 0 < len(ran) <= 3
    assert sum(1 for s in result.step_results if s["step_id"].startswith("gov-dispute")) == len(ran)


# ================================================================ 3B.4 evidence binding


def _calc(**attrs: Any) -> Evidence:
    return Evidence.create(EvidenceType.CALCULATION, "agent:test", "calc", utc_now(), attrs)


def test_3b4_mismatched_cited_value_is_dropped_with_a_warning():
    memory = InvestigationMemory()
    calc = _calc(volatility_ratio=2.5, derived_from=[])
    memory.add_evidence(calc)
    good = Finding.create("ok", [calc.id], 0.8, attributes={"volatility_ratio": 2.5})
    bad = Finding(
        "F-bad", "lies", (calc.id,), (), 0.9, evidence_refs=(EvidenceRef(calc.id, "volatility_ratio", 9.9),)
    )
    memory.add_findings(bind_evidence_refs(good, memory.evidence), bad)
    warnings: list[Any] = []
    dropped = AgentHarness._validate_evidence(memory, warnings)
    assert dropped == ["F-bad"]
    assert [f.statement for f in memory.findings] == ["ok"]
    assert memory.findings[0].evidence_refs == (EvidenceRef(calc.id, "volatility_ratio", 2.5),)
    assert warnings and warnings[0].code == "EVIDENCE_VALUE_MISMATCH"


def test_3b4_calculation_derived_from_unknown_evidence_is_dropped():
    memory = InvestigationMemory()
    calc = _calc(x=1.0, derived_from=["EXEC-00000000"])
    memory.add_evidence(calc)
    memory.add_findings(Finding.create("derived from nothing", [calc.id], 0.7, attributes={"x": 1.0}))
    assert AgentHarness._validate_evidence(memory, []) and not memory.findings


async def test_3b4_findings_are_bound_and_every_binding_holds(store):
    result = await _platform(store=store).investigate(default_request("AAPL", "T10"))
    ev = {e.id: e for e in result.evidence}
    bound = [f for f in result.findings if f.evidence_refs]
    assert len(bound) >= len(result.findings) * 0.7
    vol = next(f for f in result.findings if "volatility rose" in f.statement.lower())
    assert vol.evidence_refs[0].path == "volatility_ratio"
    for f in bound:
        for ref in f.evidence_refs:
            assert ref.id in ev
    knowledge = [e for e in result.evidence if e.attributes.get("tool_id") == "knowledge.search_documents"]
    assert knowledge and all(e.type is EvidenceType.DOCUMENT for e in knowledge)
    assert all("values" in e.attributes for e in result.evidence if e.attributes.get("tool_id"))


async def test_3b4_id_fabricator_leaves_zero_fabricated_findings(store):
    from fakes.clients import IdFabricatorClient

    result = await _platform(IdFabricatorClient(), store=store).investigate(default_request("AAPL", "T05"))
    ev = {e.id for e in result.evidence}
    assert all(f.id != "FIND-fabricated" for f in result.findings)
    assert all(
        set(f.supporting_evidence) <= ev and all(r.id in ev for r in f.evidence_refs) for f in result.findings
    )


# ================================================================ 3B.6 typed messages, re-plan, budget


def test_3b6_drifted_message_version_is_rejected():
    msg = AgentMessage(sender="market", success=True, data={"x": 1})
    assert parse_message(msg.model_dump()) == msg
    with pytest.raises(MessageVersionError):
        parse_message({**msg.model_dump(), "schema_version": 2})
    with pytest.raises(MessageVersionError):
        parse_message({**msg.model_dump(), "unexpected": True})
    with pytest.raises(ValueError):
        AgentMessage(sender="market", success=True, schema_version=2)  # type: ignore[arg-type]


def test_3b6_harness_builds_typed_messages_from_results():
    from ceap.domain.agents import AgentResult

    msg = _message(
        "quant", AgentResult("quant", True, output={"attribution": {"primary": "NORMAL"}, "plan": object()})
    )
    assert msg.sender == "quant" and msg.data == {"attribution": {"primary": "NORMAL"}}


class FailingTool(Tool):
    def __init__(self, inner: Tool) -> None:
        self.inner = inner
        self.calls = 0

    @property
    def id(self) -> str:
        return self.inner.id

    @property
    def metadata(self):
        return self.inner.metadata

    async def execute(self, request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
        self.calls += 1
        return ToolResult(status=ToolStatus.ERROR, error="venue feed unavailable")


async def test_3b6_forced_tool_failure_triggers_exactly_one_replan(store):
    llm = ScriptedLLM()
    platform = _platform(llm, store=store)
    failing = await _override_tool(platform, "execution.get_venue_statistics", FailingTool)
    result = await platform.investigate(default_request("AAPL", "T05"))
    assert llm.purposes.count("planning") == 2  # the plan, then exactly one re-plan
    assert sum(1 for w in result.warnings if w.code == "REPLANNED") == 1
    assert sum(1 for s in result.step_results if s["step_id"] == "replan") == 1
    assert "REQUIRED_AGENT_FAILED" in {w.code for w in result.warnings}  # execution could not get its data
    assert failing.calls >= 2  # the first plan and the re-plan both tried (each with harness retries)


def test_3b6_oversized_payload_is_truncated_in_the_documented_order():
    findings = [
        {
            "id": f"F{i}",
            "confidence": i / 10,
            "statement": "s" * 900,
            "supporting_evidence": [{"attributes": {"a": "b" * 900}}],
        }
        for i in range(10)
    ]
    out, notes = fit_context({"findings": findings}, budget=2_000)
    assert notes[:3] == list(TRUNCATION_ORDER)
    assert all("attributes" not in json.dumps(f) for f in out["findings"])
    kept = [f["confidence"] for f in out["findings"]]
    assert kept and max(kept) == 0.9 and len(kept) < 10  # the lowest confidence went first
    same, none = fit_context({"findings": findings[:1]}, budget=10**6)
    assert none == [] and same == {"findings": findings[:1]}


# ================================================================ 3B.7 case memory (D10)


async def test_3b7_evaluation_shows_zero_memory_reads():
    from ceap.evaluation import evaluation_platform, run_evaluation

    platform = evaluation_platform()
    assert not platform.case_memory.enabled
    out = await run_evaluation(limit=1, platform=platform)
    assert out["case_memory"] == {"enabled": False, "reads": 0}
    assert platform.case_memory.writes == 0 and len(platform.case_memory) == 0


async def test_3b7_second_similar_investigation_cites_a_prior_case(store):
    audit: list[tuple[str, str]] = []
    memory = CaseMemory(audit=lambda actor, action, subject, payload: audit.append((action, subject)))
    platform = _platform(store=store, case_memory=memory)
    req = default_request("AAPL", "T10")
    req.attributes["signoff"] = True
    first = await platform.investigate(req)
    case_id = first.agent_outputs["case_memory"]["written"]
    assert case_id.startswith("CASE") and audit == [("case_memory.append", case_id)]
    second = await platform.investigate(default_request("AAPL", "T10"))
    assert second.agent_outputs["case_memory"]["retrieved"] == [case_id]
    assert case_id in second.agent_outputs["scope"]["context_cases"]
    assert case_id in second.report.critique["prior_cases"]
    other = await platform.investigate(default_request("AAPL", "T05"))  # another dataset partition
    assert other.agent_outputs["case_memory"]["retrieved"] == []


def test_3b7_case_memory_is_append_only_bounded_and_read_only():
    memory = CaseMemory(max_cases=1)
    rec = CaseRecord("CASE-1", "T-1", "T01", "investigation", "AAPL", "why", "APPROVED", "ann", "", (), "now")
    assert memory.append(rec) and not memory.append(rec)  # duplicate refused
    full = CaseRecord(
        "CASE-2", "T-2", "T01", "investigation", "AAPL", "why", "APPROVED", "ann", "", (), "now"
    )
    assert not memory.append(full) and len(memory) == 1  # the partition is full: refused, nothing deleted
    assert not any(hasattr(memory, m) for m in ("update", "delete", "remove", "clear"))
    view = memory.nearest("T01", "why AAPL")[0]
    view["outcome"] = "TAMPERED"
    assert memory.nearest("T01", "why AAPL")[0]["outcome"] == "APPROVED"
    assert memory.nearest("T02", "why AAPL") == ()
    off = CaseMemory(enabled=False)
    assert not off.append(rec) and off.nearest("T01", "why") == () and off.reads == 0


# ================================================================ 3B.8 human-in-the-loop payload


class CapturingGateway(ApprovalGateway):
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def request(self, approval):
        self.requests.append(approval)
        return ApprovalDecision(approval.id, True, "approver.b", "looks right")


def _shape(review: dict[str, Any]) -> dict[str, Any]:
    """The snapshot: structure and deterministic content, ids and timings removed."""
    return {
        "version": review["version"],
        "keys": sorted(review),
        "hypotheses": {
            g: [(r["name"], r["verdict"]) for r in rows] for g, rows in review["hypotheses"].items()
        },
        "evidence_links": sorted(
            (link["statement"], sorted(r["path"] for r in link["evidence_refs"]))
            for link in review["evidence_links"]
        ),
        "pre_trade_diff_keys": sorted(review["pre_trade_diff"]),
        "disputes_keys": sorted(review["disputes"]),
    }


async def test_3b8_approval_payload_snapshot(store):
    gateway = CapturingGateway()
    platform = _platform(store=store, approvals=gateway)
    req = default_request("AAPL", "T10")
    req.attributes["signoff"] = True
    result = await platform.investigate(req)
    assert result.success and len(gateway.requests) == 1
    review = gateway.requests[0].arguments["review"]
    assert review["pre_trade_diff"]["diff_bps"] == pytest.approx(
        review["pre_trade_diff"]["realised_bps"] - review["pre_trade_diff"]["estimate_bps"], abs=1e-3
    )
    shape = json.loads(json.dumps(_shape(review)))
    path = SNAPSHOTS / "approval_review_T10.json"
    if os.environ.get("CEAP_UPDATE_SNAPSHOTS") == "1" or not path.exists():
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(json.dumps(shape, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == shape


async def test_3b8_rejection_reason_appears_in_the_audit_chain(store):
    db = Database(":memory:")
    platform = Platform(
        settings=Settings(llm_provider="mock", auto_approve=False, approval_timeout_s=30),
        llm=MockLLMClient(),
        store=store,
        db=db,
    )
    assert isinstance(platform.approvals, QueuedApprovalGateway)
    req = default_request("AAPL", "T10")
    req.attributes["signoff"] = True
    run = asyncio.create_task(platform.investigate(req))
    for _ in range(400):
        if platform.approvals.pending():
            break
        await asyncio.sleep(0.05)
    pending = platform.approvals.pending()[0]
    assert pending.arguments["review"]["hypotheses"]["SUPPORTED"]
    platform.approvals.decide(pending.id, False, "approver.b", "venue data looks stale", action="reject")
    result = await run
    rows = db.query("SELECT action, payload_json FROM audit_log ORDER BY seq")
    decided = [json.loads(r["payload_json"]) for r in rows if r["action"] == "approval.decided"]
    assert decided and decided[0]["reason"] == "venue data looks stale" and decided[0]["action"] == "reject"
    case = [json.loads(r["payload_json"]) for r in rows if r["action"] == "case_memory.append"]
    assert case and case[0]["outcome"] == "REJECTED" and case[0]["reason"] == "venue data looks stale"
    assert db.verify_chain().ok
    assert result.status.value in ("COMPLETED", "DEGRADED")  # an optional sign-off: the task completes
    assert any(w.code == "APPROVAL_DECLINED" for w in result.warnings)


def test_3b8_modify_needs_a_reason_and_matches_approved():
    from pydantic import ValidationError

    from ceap.api.models import ApprovalDecisionIn

    assert ApprovalDecisionIn(approved=False, action="modify", comment="split by venue").action == "modify"
    for bad in (
        {"approved": False, "action": "modify"},
        {"approved": True, "action": "reject", "comment": "x"},
    ):
        with pytest.raises(ValidationError):
            ApprovalDecisionIn(**bad)
