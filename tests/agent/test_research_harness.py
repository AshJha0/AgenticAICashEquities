"""Stage 2 research pipeline through the harness with the deterministic LLM."""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from ceap.agents import ReportAgent
from ceap.domain.agents import Agent, AgentContext, AgentResult
from ceap.domain.common import new_id
from ceap.domain.plans import Plan, PlanStep, StepType
from ceap.domain.research import research_tool_arguments
from ceap.domain.tools import ToolRequest
from ceap.harness.engine import AgentHarness
from ceap.harness.state_machine import HarnessState
from ceap.llm.client import MockLLMClient
from ceap.platform import Platform, RequestNotPermitted, ResearchRequest, default_research_request
from ceap.policy.approvals import ApprovalDecision, ApprovalGateway, ApprovalRequest, DenyApprovalGateway

RESEARCH_STATES = [
    "PLANNING",
    "VALIDATING_PLAN",
    "EXECUTING",
    "CRITIQUING",
    "EXECUTING",
    "VALIDATING_EVIDENCE",
    "EXECUTING",
    "AWAITING_APPROVAL",
    "EXECUTING",
    "FINALISING",
    "COMPLETED",
]
RESEARCH_AGENTS = {"research", "alpha", "backtest", "portfolio_risk", "critic"}


def _tool_spans(result) -> Counter:
    return Counter(s["name"] for s in result.trace["spans"] if s["name"].startswith("tool:"))


async def test_research_end_to_end_promotes_the_momentum_premium(platform):
    result = await platform.research(default_research_request("R01"))
    assert result.success and result.state is HarnessState.COMPLETED and not result.warnings
    assert [h["to"] for h in result.state_history] == RESEARCH_STATES
    assert result.plan is not None and result.plan.generated_by.startswith("llm:")
    ids = [s.id for s in result.plan.ordered_steps()]
    assert ids[-4:] == ["gov-critic", "gov-validation", "gov-approval", "gov-finalise"]
    assert {s.agent_id for s in result.plan.steps if s.agent_id} == RESEARCH_AGENTS
    assert max(_tool_spans(result).values()) == 1  # every tool exactly once: agents reuse the plan's outputs
    report = result.report
    assert report is not None and report.kind == "research"
    assert report.proposal["verdict"] == "PROMOTE" and report.proposal["flags"] == []
    assert report.proposal["approval"]["approved"] and report.proposal["staging"]["requested"] is False
    assert report.proposal["target_portfolio"]["names"] == 20
    assert report.critique["narrative_number_warnings"] == [] and report.critique["narrative_evidence_warnings"] == []
    assert {"HYPOTHESIS", "SIGNAL STATISTICS", "BACKTEST", "RISK", "VERDICT", "APPROVAL"} <= set(
        line.strip() for line in report.narrative.splitlines()
    )
    evidence_ids = {e.id for e in result.evidence}
    assert result.findings and all(set(f.supporting_evidence) <= evidence_ids for f in result.findings)
    assert any(e.id.startswith("SIG-") for e in result.evidence) and any(e.id.startswith("BT-") for e in result.evidence)
    assert [s["status"] for s in result.step_results if s["type"] == "HUMAN_APPROVAL"] == ["APPROVED"]


async def test_research_rejects_a_regime_break(platform):
    result = await platform.research(default_research_request("R04"))
    assert result.success and result.report is not None
    assert result.report.proposal["verdict"] == "REJECT" and "OVERFIT" in result.report.proposal["flags"]
    assert "OVERFIT" in result.report.conclusion
    assert result.report.metrics["backtest"]["sharpe_ratio_oos_is"] < 0.5


async def test_stage_orders_adds_two_gates_and_stages_paper_orders(platform):
    result = await platform.research(default_research_request("R01", stage_orders=True))
    assert result.success and not result.warnings
    ids = [s.id for s in result.plan.ordered_steps()]
    assert ids[-6:] == [
        "gov-critic",
        "gov-validation",
        "gov-approval",
        "gov-stage-approval",
        "gov-stage-orders",
        "gov-finalise",
    ]
    assert [h["to"] for h in result.state_history].count("AWAITING_APPROVAL") == 2
    approvals = {s["step_id"]: s["status"] for s in result.step_results if s["type"] == "HUMAN_APPROVAL"}
    assert approvals == {"gov-approval": "APPROVED", "gov-stage-approval": "APPROVED"}
    staged = next(s for s in result.step_results if s.get("tool_id") == "execution.stage_orders")
    assert staged["status"] == "SUCCESS" and staged["step_id"] == "gov-stage-orders"
    staging = result.report.proposal["staging"]
    assert staging["staged"] and staging["count"] == 20 and staging["staging_id"].startswith("STG-")
    assert "STAGED ORDERS" in result.report.narrative and staging["staging_id"] in result.report.narrative
    assert any(e.id.startswith("ORDER-") for e in result.evidence)
    assert result.report.critique["narrative_number_warnings"] == []


async def test_only_admins_may_request_order_staging(platform):
    with pytest.raises(RequestNotPermitted):
        await platform.research(default_research_request("R01", stage_orders=True, roles=frozenset({"quant"})))
    with pytest.raises(RequestNotPermitted):
        await platform.research(default_research_request("R01", roles=frozenset({"trader"})))


async def test_research_validation_rejects_bad_requests(platform):
    base = default_research_request("R01")
    for bad in (
        {"signal": "nope"},
        {"dataset": "R99"},
        {"in_sample_end": date(2026, 9, 18)},
        {"start": date(2022, 10, 3)},
        {"rebalance_days": 0},
        {"gross_notional": 0.0},
        {"start": date(2026, 9, 1), "end": date(2026, 8, 1)},
    ):
        with pytest.raises(ValueError):
            await platform.research(ResearchRequest(**{**base.__dict__, **bad}))


async def test_declined_proposal_fails_cleanly(platform, store, settings):
    p = Platform(
        settings=settings, llm=MockLLMClient(), store=store, history=platform.history, approvals=DenyApprovalGateway()
    )
    result = await p.research(default_research_request("R01"))
    assert result.state is HarnessState.FAILED and result.report is None
    assert "human approval declined at step gov-approval" in (result.error or "")
    assert any("declined" in w for w in result.warnings)


class DeclineStagingGateway(ApprovalGateway):
    """Approves the proposal, declines order staging."""

    def __init__(self) -> None:
        self.requests: list[ApprovalRequest] = []

    async def request(self, approval: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(approval)
        approved = approval.step_id != "gov-stage-approval"
        return ApprovalDecision(approval.id, approved, "tester", "" if approved else "not today")


async def test_declined_staging_completes_without_orders_and_shows_the_proposal(platform, store, settings):
    gateway = DeclineStagingGateway()
    p = Platform(settings=settings, llm=MockLLMClient(), store=store, history=platform.history, approvals=gateway)
    result = await p.research(default_research_request("R01", stage_orders=True))
    assert result.success
    statuses = {s["step_id"]: s["status"] for s in result.step_results}
    assert statuses["gov-approval"] == "APPROVED" and statuses["gov-stage-approval"] == "REJECTED"
    assert statuses["gov-stage-orders"] == "SKIPPED" and statuses["gov-finalise"] == "SUCCESS"
    staging = result.report.proposal["staging"]
    assert staging["requested"] and not staging["staged"] and staging["status"] == "SKIPPED"
    assert "Orders were not staged" in result.report.narrative
    assert any("declined" in w for w in result.warnings)
    # what the approver saw
    by_step = {r.step_id: r for r in gateway.requests}
    proposal = by_step["gov-approval"].arguments
    assert proposal["proposal"]["verdict"] == "PROMOTE" and proposal["target_portfolio"]["names"] == 20
    assert proposal["signal"] == "momentum_12_1" and proposal["dataset"] == "R01"
    preview = by_step["gov-stage-approval"].arguments["orders_preview"]
    assert len(preview) == 20 and {o["side"] for o in preview} == {"BUY", "SELL"}
    assert by_step["gov-stage-approval"].arguments["tool_id"] == "execution.stage_orders"


class ApprovalFirstPlanner(Agent):
    """A plan that schedules its own approval step and a tool call - the harness must own approvals."""

    id, type = "planner", "planner"

    async def execute(self, context: AgentContext) -> AgentResult:
        args = research_tool_arguments(context.task_input)
        steps = (
            PlanStep("p1", 1, StepType.HUMAN_APPROVAL, "premature approval"),
            PlanStep("p2", 2, StepType.TOOL_CALL, "signal", ToolRequest("alpha.evaluate_signal", args["evaluate_signal"])),
            PlanStep("p3", 3, StepType.AGENT_CALL, "alpha", agent_id="alpha"),
        )
        return AgentResult("planner", True, output={"plan": Plan(new_id("PLAN"), context.task_id, steps, "r")})


async def test_plan_supplied_approval_steps_are_stripped(platform):
    req = default_research_request("R01")
    task = platform.build_research_task(req)
    base = await platform.harness()
    harness = AgentHarness(
        ApprovalFirstPlanner(), base.agents, base.tools, base.policy, ReportAgent(MockLLMClient()), settings=platform.settings
    )
    result = await harness.execute(task, platform.policy_context(req, task))
    assert result.success
    approvals = [s for s in result.plan.ordered_steps() if s.type is StepType.HUMAN_APPROVAL]
    assert [s.id for s in approvals] == ["gov-approval"]
    assert any("p1 (HUMAN_APPROVAL)" in w and "governance" in w for w in result.warnings)
    assert result.report.proposal["approval"]["approved"]
