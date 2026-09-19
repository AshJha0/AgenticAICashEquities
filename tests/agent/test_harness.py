"""Harness + agents end to end with the deterministic LLM."""

from __future__ import annotations

import asyncio

import pytest

from ceap.agents import CriticAgent, PlannerAgent, ReportAgent
from ceap.domain.agents import Agent, AgentContext, AgentResult
from ceap.domain.common import new_id
from ceap.domain.plans import Plan, PlanStep, StepType
from ceap.domain.tools import RiskLevel, Tool, ToolMetadata, ToolRequest, ToolResult, ToolStatus
from ceap.harness.cancellation import CancellationToken
from ceap.harness.engine import AgentHarness
from ceap.harness.state_machine import HarnessState
from ceap.llm.client import MockLLMClient
from ceap.platform import InvestigationRequest, Platform, default_request
from ceap.policy.approvals import DenyApprovalGateway, QueuedApprovalGateway
from ceap.policy.engine import RulePolicyEngine

REQUIRED_STATES = [
    "PLANNING",
    "VALIDATING_PLAN",
    "EXECUTING",
    "CRITIQUING",
    "EXECUTING",
    "VALIDATING_EVIDENCE",
    "EXECUTING",
    "FINALISING",
    "COMPLETED",
]


async def test_full_investigation_normal_scenario(platform):
    result = await platform.investigate(default_request("AAPL", "T01"))
    assert result.success and result.state is HarnessState.COMPLETED and result.error is None
    assert [h["to"] for h in result.state_history] == REQUIRED_STATES
    assert result.plan is not None and result.plan.generated_by.startswith("llm:")
    step_types = [s.type for s in result.plan.ordered_steps()]
    assert step_types[-3:] == [StepType.AGENT_CALL, StepType.VALIDATION, StepType.FINALISE]
    assert {s.agent_id for s in result.plan.steps if s.agent_id} == {
        "market",
        "execution",
        "engineering",
        "risk",
        "quant",
        "critic",
    }
    report = result.report
    assert report is not None and report.attribution["primary"] == "NORMAL"
    assert "EXECUTIVE SUMMARY" in report.narrative and "EVIDENCE" in report.narrative
    assert (
        report.critique["narrative_number_warnings"] == []
        and report.critique["narrative_evidence_warnings"] == []
    )
    assert not result.warnings


async def test_every_finding_cites_resolvable_evidence(platform):
    result = await platform.investigate(default_request("AAPL", "T06"))
    ids = {e.id for e in result.evidence}
    assert result.findings
    for f in result.findings:
        assert f.supporting_evidence, f.statement
        assert set(f.supporting_evidence) <= ids, f.statement
        assert set(f.contradicting_evidence) <= ids
    assert result.report.attribution["primary"] == "TECHNOLOGY_LATENCY"
    assert any(
        f.category == "TECHNOLOGY" and f.attributes.get("anomaly") == "TECHNOLOGY_LATENCY"
        for f in result.findings
    )
    assert any(f.attributes.get("anomaly") == "CODE_CHANGE" for f in result.findings)
    assert all(d["decision"] in ("ALLOW", "APPROVED") for d in result.policy_log)


async def test_tool_calls_are_traced_and_recorded(platform):
    result = await platform.investigate(default_request("AAPL", "T03"))
    tool_steps = [s for s in result.step_results if s["type"] == "TOOL_CALL"]
    assert tool_steps and all(s["status"] == "SUCCESS" for s in tool_steps)
    span_names = [s["name"] for s in result.trace["spans"]]
    assert span_names[0] == "harness.execute" and "harness.plan" in span_names
    assert sum(1 for n in span_names if n.startswith("tool:")) >= len(tool_steps)
    assert sum(1 for n in span_names if n.startswith("agent:")) >= 8
    assert result.report.attribution["primary"] == "WIDE_SPREADS"
    assert any("POLICY CONTEXT" in result.report.narrative for _ in [0])


async def test_report_sections_and_metrics_are_deterministic(platform):
    r1 = await platform.investigate(default_request("AAPL", "T02"))
    r2 = await platform.investigate(default_request("AAPL", "T02"))
    m1, m2 = r1.report.metrics["execution"]["window"], r2.report.metrics["execution"]["window"]
    assert m1["implementation_shortfall_bps"] == m2["implementation_shortfall_bps"]
    assert r1.report.attribution["ranked"][0]["score"] == r2.report.attribution["ranked"][0]["score"]
    assert r1.report.primary_observations and r1.report.conclusion and r1.report.executive_summary


# ------------------------------------------------------------- governance
class MinimalPlanner(Agent):
    """Planner that omits critic/validation/finalise - the harness must add them."""

    def __init__(self, steps):
        self._steps = steps

    id = "planner"
    type = "planner"

    async def execute(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            "planner",
            True,
            output={"plan": Plan(new_id("PLAN"), context.task_id, tuple(self._steps), "minimal")},
        )


class FailingAgent(Agent):
    id = "market"
    type = "specialist"

    async def execute(self, context: AgentContext) -> AgentResult:
        raise RuntimeError("market agent exploded")


async def _harness(platform: Platform, planner: Agent, agents=None, **kw) -> AgentHarness:
    base = await platform.harness()
    return AgentHarness(
        planner=planner,
        agents=agents or base.agents,
        tools=base.tools,
        policy=kw.get("policy", base.policy),
        reporter=ReportAgent(MockLLMClient()),
        approvals=kw.get("approvals", base.approvals),
        settings=platform.settings,
    )


def _win_step(seq: int, tool: str, dataset: str = "T01", **extra) -> PlanStep:
    args = {
        "symbol": "AAPL",
        "start": "2026-09-18T14:00:00+01:00",
        "end": "2026-09-18T15:00:00+01:00",
        "dataset": dataset,
        **extra,
    }
    return PlanStep(f"s{seq}", seq, StepType.TOOL_CALL, tool, ToolRequest(tool, args))


async def test_harness_enforces_governance_steps(platform):
    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    harness = await _harness(platform, MinimalPlanner([_win_step(1, "execution.get_execution_metrics")]))
    result = await harness.execute(task, platform.policy_context(req, task))
    assert result.success
    ids = [s.id for s in result.plan.ordered_steps()]
    assert ids[-3:] == ["gov-critic", "gov-validation", "gov-finalise"]
    assert "CRITIQUING" in [h["to"] for h in result.state_history]


async def test_unknown_tool_in_plan_fails_validation(platform):
    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    harness = await _harness(
        platform,
        MinimalPlanner(
            [PlanStep("s1", 1, StepType.TOOL_CALL, "x", ToolRequest("shell.exec", {"cmd": "rm -rf /"}))]
        ),
    )
    result = await harness.execute(task, platform.policy_context(req, task))
    assert result.state is HarnessState.FAILED and "unknown tool shell.exec" in result.error
    assert not result.step_results  # nothing executed


async def test_policy_denial_at_validation_time(platform):
    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    policy = RulePolicyEngine(denied_tools=frozenset({"execution.get_execution_metrics"}))
    harness = await _harness(
        platform, MinimalPlanner([_win_step(1, "execution.get_execution_metrics")]), policy=policy
    )
    result = await harness.execute(task, platform.policy_context(req, task))
    assert result.state is HarnessState.FAILED and "policy denies" in result.error


async def test_agent_failure_is_contained(platform):
    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    base = await platform.harness()
    agents = dict(base.agents, market=FailingAgent())
    harness = await _harness(platform, PlannerAgent(MockLLMClient()), agents=agents)
    result = await harness.execute(task, platform.policy_context(req, task))
    assert result.success
    assert any("market agent exploded" in w for w in result.warnings)
    assert any(s.get("agent_id") == "market" and s["status"] == "ERROR" for s in result.step_results)


async def test_cancellation_produces_cancelled_state(platform):
    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    harness = await platform.harness()
    token = CancellationToken()
    token.cancel("user pressed stop")
    result = await harness.execute(task, platform.policy_context(req, task), token)
    assert result.state is HarnessState.CANCELLED and "user pressed stop" in result.error


async def test_approval_flow_transitions_state(platform):
    req = InvestigationRequest(**{**default_request("AAPL", "T01").__dict__, "roles": frozenset({"viewer"})})
    task = platform.build_task(req)
    gateway = QueuedApprovalGateway(timeout_seconds=5)
    step = PlanStep(
        "s1",
        1,
        StepType.TOOL_CALL,
        "stress",
        ToolRequest("risk.calculate_stress", {"symbol": "AAPL", "shock_bps": -200, "dataset": "T01"}),
    )
    harness = await _harness(
        platform, MinimalPlanner([step, _win_step(2, "execution.get_execution_metrics")]), approvals=gateway
    )

    async def approve_when_pending():
        for _ in range(200):
            if gateway.pending():
                gateway.decide(gateway.pending()[0].id, True, "desk-head", "ok")
                return
            await asyncio.sleep(0.01)

    result, _ = await asyncio.gather(
        harness.execute(task, platform.policy_context(req, task)), approve_when_pending()
    )
    states = [h["to"] for h in result.state_history]
    assert "AWAITING_APPROVAL" in states and result.success
    assert any(d["decision"] == "APPROVED" for d in result.policy_log)
    assert gateway.history and gateway.history[0].approved


async def test_approval_denied_marks_step_denied(platform):
    req = InvestigationRequest(**{**default_request("AAPL", "T01").__dict__, "roles": frozenset({"viewer"})})
    task = platform.build_task(req)
    step = PlanStep(
        "s1",
        1,
        StepType.TOOL_CALL,
        "stress",
        ToolRequest("risk.calculate_stress", {"symbol": "AAPL", "dataset": "T01"}),
    )
    harness = await _harness(
        platform,
        MinimalPlanner([step, _win_step(2, "execution.get_execution_metrics")]),
        approvals=DenyApprovalGateway(),
    )
    result = await harness.execute(task, platform.policy_context(req, task))
    assert result.success  # investigation continues without the denied tool
    denied = [s for s in result.step_results if s.get("status") == "DENIED"]
    assert denied and "approval not granted" in denied[0]["error"]


class SlowTool(Tool):
    id = "slow.tool"
    metadata = ToolMetadata("slow.tool", "tool", "", True, RiskLevel.LOW)

    async def execute(self, request, context):
        await asyncio.sleep(5)
        return ToolResult(ToolStatus.SUCCESS)


async def test_tool_timeout_is_reported(platform):
    from ceap.config import Settings

    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    base = await platform.harness()
    base.tools.register(SlowTool()) if not base.tools.has("slow.tool") else None
    harness = AgentHarness(
        planner=MinimalPlanner(
            [
                PlanStep("s1", 1, StepType.TOOL_CALL, "slow", ToolRequest("slow.tool", {})),
                _win_step(2, "execution.get_execution_metrics"),
            ]
        ),
        agents=base.agents,
        tools=base.tools,
        policy=base.policy,
        reporter=ReportAgent(MockLLMClient()),
        settings=Settings(llm_provider="mock", step_timeout_seconds=0.2, max_retries=0),
    )
    result = await harness.execute(task, platform.policy_context(req, task))
    slow = next(s for s in result.step_results if s["step_id"] == "s1")
    assert slow["status"] == "TIMEOUT" and result.success


async def test_platform_validation_rejects_bad_requests(platform):
    req = default_request("AAPL", "T01")
    with pytest.raises(ValueError):
        await platform.investigate(InvestigationRequest(**{**req.__dict__, "symbol": "TSLA"}))
    with pytest.raises(ValueError):
        await platform.investigate(InvestigationRequest(**{**req.__dict__, "dataset": "S37"}))  # MSFT dataset
    with pytest.raises(ValueError):
        await platform.investigate(
            InvestigationRequest(
                **{
                    **req.__dict__,
                    "window_start": req.window_start.replace(hour=8),
                    "window_end": req.window_end.replace(hour=9),
                }
            )
        )


async def test_critic_with_llm_can_only_lower_confidence(platform):
    class RaisingLLM(MockLLMClient):
        async def complete(self, request):
            resp = await super().complete(request)
            if request.purpose == "critique":
                import json

                data = json.loads(resp.content)
                for a in data["assessments"]:
                    a["adjusted_confidence"] = 1.0
                return type(resp)(json.dumps(data), [], 1, 1, "raising")
            return resp

    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    base = await platform.harness()
    agents = dict(base.agents, critic=CriticAgent(RaisingLLM()))
    harness = await _harness(platform, PlannerAgent(MockLLMClient()), agents=agents)
    result = await harness.execute(task, platform.policy_context(req, task))
    assert result.success and all(f.confidence < 1.0 for f in result.findings)
