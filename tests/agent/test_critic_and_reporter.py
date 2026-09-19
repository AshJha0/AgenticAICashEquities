"""Critic and reporter behaviour in isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ceap.agents.critic import CriticAgent
from ceap.agents.reporter import audit_evidence_ids, audit_numbers
from ceap.domain.agents import AgentContext, ToolInvoker
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.findings import Finding
from ceap.domain.policy import PolicyContext
from ceap.domain.tools import ToolRegistry
from ceap.harness.memory import InvestigationMemory


class NoTools(ToolInvoker):
    async def invoke(self, tool_id, arguments):
        raise AssertionError("critic must not call tools")


def _ctx(memory: InvestigationMemory, findings, agent_outputs) -> AgentContext:
    return AgentContext(
        task_id="t",
        execution_id="x",
        deadline=datetime.now(UTC) + timedelta(minutes=1),
        task_input={},
        current_plan=None,
        evidence=memory.evidence_list(),
        state={"memory": memory, "agent_outputs": agent_outputs},
        tool_registry=ToolRegistry(),
        policy_context=PolicyContext("p", frozenset(), frozenset(), "t"),
        cancellation_event=None,
        tools=NoTools(),
        findings=tuple(findings),
    )


async def test_critic_flags_missing_evidence_and_uncorroborated_anomalies():
    mem = InvestigationMemory()
    ev = Evidence.create(EvidenceType.EXECUTION_DATA, "t", "d")
    ev2 = Evidence.create(EvidenceType.CALCULATION, "t", "d")
    att = Evidence.create(EvidenceType.CALCULATION, "quant", "attribution")
    mem.add_evidence(ev, ev2, att)
    good = Finding.create("good", [ev.id, ev2.id], 0.9)
    ghost = Finding.create("ghost", ["EXEC-00000000"], 0.9)
    uncorroborated = Finding.create(
        "spreads wide", [ev.id, ev2.id], 0.9, attributes={"anomaly": "WIDE_SPREADS"}
    )
    thin = Finding.create("thin", [ev.id], 0.95)
    outputs = {
        "quant": {
            "attribution": {"ranked": [{"cause": "WIDE_SPREADS", "score": 0.1}]},
            "attribution_evidence_id": att.id,
        },
        "engineering": {"latency_ratio": 1.0, "error_logs": 0},
    }
    result = await CriticAgent().execute(_ctx(mem, [good, ghost, uncorroborated, thin], outputs))
    adjusted = {f.statement: f for f in result.output["adjusted_findings"]}
    assert adjusted["good"].confidence == 0.9
    assert adjusted["ghost"].confidence == 0.1 and result.output["critique"]["unsupported"] == [ghost.id]
    assert (
        adjusted["spreads wide"].confidence <= 0.6
        and att.id in adjusted["spreads wide"].contradicting_evidence
    )
    assert adjusted["thin"].confidence == 0.7
    assert result.output["critique"]["contradictions"]


async def test_critic_contradiction_between_quant_and_engineering():
    mem = InvestigationMemory()
    ev = Evidence.create(EvidenceType.CALCULATION, "t", "d")
    ev2 = Evidence.create(EvidenceType.CALCULATION, "t", "d2")
    mem.add_evidence(ev, ev2)
    f = Finding.create(
        "tech caused it",
        [ev.id, ev2.id],
        0.9,
        produced_by="quant",
        attributes={"anomaly": "TECHNOLOGY_LATENCY"},
    )
    outputs = {
        "quant": {"attribution": {"ranked": [{"cause": "TECHNOLOGY_LATENCY", "score": 0.9}]}},
        "engineering": {"latency_ratio": 1.0, "error_logs": 0},
    }
    result = await CriticAgent().execute(_ctx(mem, [f], outputs))
    assert result.output["adjusted_findings"][0].confidence <= 0.4
    assert any("engineering telemetry" in c for c in result.output["critique"]["contradictions"])


def test_number_audit_traces_percentages_and_ratios():
    facts = {
        "metrics": {"window": {"fill_rate": 0.817, "vwap": 230.4221, "is": -21.97}},
        "market": {"ratio": 1.02},
    }
    ok = "Fill rate was 81.7% with VWAP 230.4221 and IS -22.0 bps; volatility +2% vs baseline. 1. first item"
    assert audit_numbers(ok, facts) == []
    bad = "The fill rate was 93.5% and VWAP 231.9999."
    warnings = audit_numbers(bad, facts)
    assert len(warnings) == 2 and "93.5" in warnings[0]


def test_evidence_id_audit():
    assert audit_evidence_ids("see EXEC-0123abcd and TCA-deadbeef", {"EXEC-0123abcd"}) == [
        "evidence id TCA-deadbeef not found"
    ]
    assert audit_evidence_ids("no ids here 2026-09-18T14:00", set()) == []
