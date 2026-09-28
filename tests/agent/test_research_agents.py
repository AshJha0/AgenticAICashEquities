"""Research agents and the critic's research branch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ceap.agents import CriticAgent
from ceap.domain.agents import AgentContext, ToolInvoker
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.findings import Finding
from ceap.domain.policy import PolicyContext
from ceap.domain.research import research_tool_arguments
from ceap.harness.cancellation import CancellationToken
from ceap.harness.memory import InvestigationMemory
from ceap.llm.client import canonical_plan, canonical_research_plan
from ceap.platform import default_research_request
from ceap.policy.permissions import capabilities_for


def _outputs(result) -> dict[str, dict[str, Any]]:
    return result.agent_outputs


def _findings(result, agent: str) -> list[Finding]:
    return [f for f in result.findings if f.produced_by == agent]


async def test_agents_describe_a_promotable_signal(platform):
    result = await platform.research(default_research_request("R01"))
    out = _outputs(result)
    assert out["research"]["coverage"]["complete"] and "momentum" in out["research"]["hypothesis"]
    assert out["alpha"]["predictive"] and out["alpha"]["signal_evidence_id"].startswith("TCA-")
    assert out["backtest"]["backtest_evidence_id"].startswith("TCA-")
    assert out["portfolio_risk"]["any_breached"] is False
    claims = {f.attributes.get("claim") for f in result.findings}
    assert {"HYPOTHESIS", "ALPHA", "ROBUST", "PROFITABLE"} <= claims
    assert not any(f.attributes.get("flag") in {"OVERFIT", "COST_DRAG", "LIMIT_BREACH"} for f in result.findings)
    assert {f.category for f in result.findings} == {"RESEARCH", "SIGNAL", "BACKTEST", "RISK"}
    assessment = out["critic"]["assessment"]
    assert assessment["verdict"] == "PROMOTE" and assessment["evidence_id"].startswith("TCA-")
    assert "Research assessment: PROMOTE" in out["critic"]["critique"]["overall"]


async def test_agents_flag_what_the_scenarios_embed(platform):
    no_alpha = await platform.research(default_research_request("R02"))
    assert any(f.attributes.get("claim") == "NO_ALPHA" for f in _findings(no_alpha, "alpha"))
    assert "NO_ALPHA" in _outputs(no_alpha)["critic"]["assessment"]["flags"]

    costly = await platform.research(default_research_request("R05"))
    assert any(f.attributes.get("flag") == "COST_DRAG" for f in _findings(costly, "backtest"))
    assert any(f.attributes.get("flag") == "HIGH_TURNOVER" for f in _findings(costly, "alpha"))
    assert _outputs(costly)["critic"]["assessment"]["flags"] == ["COST_DRAG"]

    concentrated = await platform.research(default_research_request("R06"))
    breach = next(f for f in _findings(concentrated, "portfolio_risk") if f.attributes.get("flag") == "LIMIT_BREACH")
    assert breach.confidence >= 0.9 and breach.attributes["breached"]
    assert {"CONCENTRATION", "LIMIT_BREACH"} <= set(_outputs(concentrated)["critic"]["assessment"]["flags"])
    for result in (no_alpha, costly, concentrated):
        assert result.report.proposal["verdict"] == "REJECT"
        assert result.report.critique["narrative_number_warnings"] == []


class NoTools(ToolInvoker):
    async def invoke(self, tool_id: str, arguments: dict[str, Any]) -> Any:
        raise AssertionError("the critic must not call tools")


async def test_critic_caps_claims_the_assessment_contradicts(platform, mcp_client, registry, history):
    """A ROBUST claim against a regime-broken dataset is capped and marked contradicted."""
    req = default_research_request("R04")
    task = platform.build_research_task(req)
    args = research_tool_arguments(task.input)
    alpha = await mcp_client.invoke("alpha", "evaluate_signal", args["evaluate_signal"])
    backtest = await mcp_client.invoke("backtest", "run_backtest", args["run_backtest"])
    exposure = await mcp_client.invoke("risk", "get_portfolio_exposure", args["portfolio"])
    limits = await mcp_client.invoke("risk", "check_portfolio_limits", args["portfolio"])
    stress = await mcp_client.invoke("risk", "calculate_portfolio_stress", args["portfolio"])
    memory = InvestigationMemory()
    memory.add_evidence(
        Evidence("SIG-x", EvidenceType.SIGNAL, "alpha.evaluate_signal", "signal statistics"),
        Evidence("BT-x", EvidenceType.BACKTEST, "backtest.run_backtest", "backtest"),
    )
    memory.agent_outputs.update(
        {
            "alpha": {"signal_statistics": alpha, "signal_evidence_id": "SIG-x"},
            "backtest": {"backtest": backtest, "backtest_evidence_id": "BT-x"},
            "portfolio_risk": {"risk": {"exposure": exposure, "limits": limits, "stress": stress}},
        }
    )
    cited = ["SIG-x", "BT-x"]
    robust = Finding.create("Holds up out-of-sample.", cited, 0.9, "BACKTEST", "backtest", attributes={"claim": "ROBUST"})
    alpha_claim = Finding.create("Predictive in-sample.", cited, 0.8, "SIGNAL", "alpha", attributes={"claim": "ALPHA"})
    stray = Finding.create("Costs kill it.", cited, 0.85, "BACKTEST", "backtest", attributes={"flag": "COST_DRAG"})
    info = Finding.create("Turnover is high.", cited, 0.8, "SIGNAL", "alpha", attributes={"flag": "HIGH_TURNOVER"})
    ctx = AgentContext(
        task_id=task.id,
        execution_id="RUN-x",
        deadline=datetime.now(UTC) + timedelta(minutes=1),
        task_input=dict(task.input),
        current_plan=None,
        evidence=(),
        state={"memory": memory, "agent_outputs": memory.agent_outputs, "scratch": memory.scratch, "task": task},
        tool_registry=registry,
        policy_context=PolicyContext("p", frozenset({"quant"}), capabilities_for({"quant"}), task.id),
        cancellation_event=CancellationToken(),
        tools=NoTools(),
        findings=(robust, alpha_claim, stray, info),
    )
    result = await CriticAgent(None).execute(ctx)
    assessment = result.output["assessment"]
    assert assessment["verdict"] == "REJECT" and "OVERFIT" in assessment["flags"]
    adjusted = {f.id: f for f in result.output["adjusted_findings"]}
    assert adjusted[robust.id].confidence <= 0.4 and assessment["evidence_id"] in adjusted[robust.id].contradicting_evidence
    assert adjusted[stray.id].confidence <= 0.6  # a COST_DRAG flag the assessment did not raise
    assert adjusted[info.id].confidence == 0.8  # informational flags are left alone
    assert adjusted[alpha_claim.id].confidence == 0.8  # in-sample alpha is real in R04
    assert any("OVERFIT" in c for c in result.output["critique"]["contradictions"])


def test_canonical_research_plan_uses_the_shared_tool_arguments(platform):
    task = platform.build_research_task(default_research_request("R03"))
    facts = {"kind": "research", **{k: task.input[k] for k in task.input if k != "universe"}}
    plan = canonical_plan(facts)
    assert plan == canonical_research_plan(facts)
    args = research_tool_arguments(task.input)
    by_tool = {s["tool_id"]: s["arguments"] for s in plan["steps"] if s["type"] == "TOOL_CALL"}
    assert by_tool["alpha.evaluate_signal"] == args["evaluate_signal"]
    assert by_tool["backtest.run_backtest"] == args["run_backtest"] and by_tool["backtest.run_backtest"]["rebalance_days"] == 5
    assert by_tool["risk.check_portfolio_limits"] == args["portfolio"]
    agents = [s["agent_id"] for s in plan["steps"] if s["type"] == "AGENT_CALL"]
    assert agents == ["research", "alpha", "backtest", "portfolio_risk", "critic"]
    assert [s["type"] for s in plan["steps"]][-2:] == ["VALIDATION", "FINALISE"]
    assert not any(s["type"] == "HUMAN_APPROVAL" for s in plan["steps"])
