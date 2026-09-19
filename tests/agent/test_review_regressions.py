"""Regression tests for findings from the independent code review (v0.2.0).

Each test pins one defect that the review found and the fix that closed it.
"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import date

import pytest

from ceap.agents import PlannerAgent, ReportAgent
from ceap.analytics.attribution import attribute_causes
from ceap.analytics.market_statistics import calculate_market_statistics
from ceap.api.parsing import parse_question
from ceap.config import Settings, SettingsError
from ceap.domain.agents import Agent, AgentContext, AgentResult
from ceap.domain.common import to_jsonable
from ceap.domain.plans import Plan, PlanStep, StepType
from ceap.domain.tools import RiskLevel, Tool, ToolMetadata, ToolRequest, ToolResult, ToolStatus
from ceap.harness.cancellation import CancellationToken
from ceap.harness.engine import AgentHarness
from ceap.harness.state_machine import HarnessState
from ceap.llm.client import MockLLMClient
from ceap.llm.models import LLMRequest, LLMResponse
from ceap.llm.router import LLMRouter
from ceap.mcp.common import downsample
from ceap.observability.metrics import MetricsRegistry
from ceap.observability.tracing import InMemoryTracer
from ceap.platform import InvestigationRequest, Platform, default_request
from ceap.policy.approvals import QueuedApprovalGateway

WIN = {
    "symbol": "AAPL",
    "start": "2026-09-18T14:00:00+01:00",
    "end": "2026-09-18T15:00:00+01:00",
    "dataset": "T01",
}


class FixedPlanner(Agent):
    id, type = "planner", "planner"

    def __init__(self, steps):
        self._steps = steps

    async def execute(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            "planner", True, output={"plan": Plan("p", context.task_id, tuple(self._steps), "r")}
        )


async def _harness(platform: Platform, planner: Agent, **kw) -> AgentHarness:
    base = await platform.harness()
    return AgentHarness(
        planner,
        kw.get("agents", base.agents),
        kw.get("tools", base.tools),
        base.policy,
        ReportAgent(MockLLMClient()),
        approvals=kw.get("approvals", base.approvals),
        settings=kw.get("settings", platform.settings),
    )


# ------------------------------------------------------- governance ordering
async def test_critic_placed_before_specialists_is_moved_after_them(platform):
    """H1: a plan with the critic first could reach the report un-critiqued."""
    steps = [
        PlanStep("s1", 1, StepType.AGENT_CALL, "critic first", agent_id="critic"),
        PlanStep("s2", 2, StepType.TOOL_CALL, "metrics", ToolRequest("execution.get_execution_metrics", WIN)),
        PlanStep("s3", 3, StepType.AGENT_CALL, "execution", agent_id="execution"),
        PlanStep("s4", 4, StepType.VALIDATION, "v"),
        PlanStep("s5", 5, StepType.FINALISE, "f"),
        PlanStep("s6", 6, StepType.AGENT_CALL, "late agent", agent_id="market"),
    ]
    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    result = await (await _harness(platform, FixedPlanner(steps))).execute(
        task, platform.policy_context(req, task)
    )
    assert result.success
    ordered = [(s.type, s.agent_id) for s in result.plan.ordered_steps()]
    assert ordered[-3:] == [
        (StepType.AGENT_CALL, "critic"),
        (StepType.VALIDATION, None),
        (StepType.FINALISE, None),
    ]
    assert all(t is not StepType.FINALISE for t, _ in ordered[:-1])
    assert all("critic_notes" in f.attributes for f in result.findings), (
        "every reported finding was critiqued"
    )
    assert any("governance" in w for w in result.warnings)


# ------------------------------------------------- argument pinning / caching
async def test_plan_cannot_point_at_another_dataset_or_symbol(platform):
    """H2: LLM-controlled dataset/symbol arguments are pinned to the task."""
    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    foreign = PlanStep(
        "s1",
        1,
        StepType.TOOL_CALL,
        "x",
        ToolRequest("execution.get_execution_metrics", {**WIN, "dataset": "S07"}),
    )
    result = await (await _harness(platform, FixedPlanner([foreign]))).execute(
        task, platform.policy_context(req, task)
    )
    assert result.state is HarnessState.FAILED and "not pinned to the task" in result.error
    off_window = PlanStep(
        "s1",
        1,
        StepType.TOOL_CALL,
        "x",
        ToolRequest("execution.get_execution_metrics", {**WIN, "start": "2026-09-18T12:30:00+01:00"}),
    )
    result = await (await _harness(platform, FixedPlanner([off_window]))).execute(
        task, platform.policy_context(req, task)
    )
    assert result.state is HarnessState.FAILED and "not a task window boundary" in result.error


async def test_planner_repins_and_records_foreign_arguments(platform):
    class Rogue(MockLLMClient):
        async def complete(self, request):
            if request.purpose != "planning":
                return await super().complete(request)
            plan = {
                "rationale": "x",
                "steps": [
                    {
                        "type": "TOOL_CALL",
                        "description": "d",
                        "tool_id": "execution.get_execution_metrics",
                        "arguments": {**WIN, "dataset": "S07", "symbol": "MSFT", "bogus": 1},
                    },
                    {
                        "type": "TOOL_CALL",
                        "description": "missing",
                        "tool_id": "execution.get_execution_metrics",
                        "arguments": {"symbol": "AAPL"},
                    },
                    {
                        "type": "TOOL_CALL",
                        "description": "type",
                        "tool_id": "execution.get_child_orders",
                        "arguments": {**WIN, "limit": "many"},
                    },
                ],
            }
            return LLMResponse(json.dumps(plan), [], 1, 1, "rogue")

    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    base = await platform.harness()
    result = await PlannerAgent(Rogue()).execute(
        AgentContext(
            task.id,
            "x",
            task.created_at,
            dict(task.input),
            None,
            (),
            {"task": task, "tool_catalogue": base.tools.list(), "memory": None},
            base.tools,
            platform.policy_context(req, task),
            None,
            None,
        )
    )
    plan = result.output["plan"]
    args = [s.tool_request.arguments for s in plan.steps if s.tool_request]
    assert args[0]["dataset"] == "T01" and args[0]["symbol"] == "AAPL" and "bogus" not in args[0]
    assert len(args) == 2 and "limit" not in args[1]  # step missing required args dropped; bad type dropped
    reasons = " ".join(r["reason"] for r in result.output["rejected_steps"])
    assert "re-pinned" in reasons and "missing required" in reasons and "wrong type" in reasons


async def test_agents_do_not_reuse_tool_output_with_different_arguments(platform):
    """H2b: ensure() must match the full argument set, not just symbol/window."""
    steps = [
        PlanStep(
            "s1",
            1,
            StepType.TOOL_CALL,
            "wrong qty",
            ToolRequest("risk.check_limit", {"symbol": "AAPL", "order_quantity": 1, "dataset": "T01"}),
        ),
        PlanStep("s2", 2, StepType.AGENT_CALL, "risk", agent_id="risk"),
    ]
    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    result = await (await _harness(platform, FixedPlanner(steps))).execute(
        task, platform.policy_context(req, task)
    )
    assert result.success
    risk_calls = [s for s in result.step_results if s.get("tool_id") == "risk.check_limit"]
    invoked = [e for e in result.evidence if e.attributes.get("tool_id") == "risk.check_limit"]
    assert len(invoked) == 2, "the risk agent fetched its own check_limit with the real quantity"
    assert any(e.attributes["arguments"]["order_quantity"] == 60_000 for e in invoked)
    assert risk_calls


# -------------------------------------------------------- cancellation in batch
class SlowTool(Tool):
    id = "slow.tool"
    metadata = ToolMetadata("slow.tool", "tool", "", True, RiskLevel.LOW)

    async def execute(self, request, context):
        await asyncio.sleep(3)
        return ToolResult(ToolStatus.SUCCESS)


async def test_cancel_during_tool_batch_is_cancelled_not_failed(platform):
    """H3: TaskCancelled inside a TaskGroup surfaced as an ExceptionGroup -> FAILED."""
    base = await platform.harness()
    if not base.tools.has("slow.tool"):
        base.tools.register(SlowTool())
    steps = [
        PlanStep(f"s{i}", i, StepType.TOOL_CALL, "slow", ToolRequest("slow.tool", {})) for i in range(1, 4)
    ]
    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    token = CancellationToken()
    harness = await _harness(platform, FixedPlanner(steps), tools=base.tools)

    async def cancel_soon():
        await asyncio.sleep(0.2)
        token.cancel("user pressed stop")

    result, _ = await asyncio.gather(
        harness.execute(task, platform.policy_context(req, task), token), cancel_soon()
    )
    assert result.state is HarnessState.CANCELLED, result.error
    assert result.duration_ms < 2500, "in-flight tool call was abandoned, not awaited to completion"


# ----------------------------------------------------- tracer under concurrency
async def test_tracer_parents_are_correct_under_concurrency():
    """H6: a shared stack gave concurrent spans the wrong parent and popped siblings."""
    tr = InMemoryTracer()

    async def child(i):
        async with tr.span(f"tool:{i}"):
            await asyncio.sleep(0.01 * (3 - i))
            tr.event("policy.decision", i=i)

    async with tr.span("harness.execute") as root:
        async with asyncio.TaskGroup() as tg:
            for i in range(3):
                tg.create_task(child(i))
    spans = {s.name: s for s in tr.spans}
    assert all(spans[f"tool:{i}"].parent_id == root.id for i in range(3))
    assert all(spans[f"tool:{i}"].events[0]["i"] == i for i in range(3))


async def test_investigation_trace_tool_spans_hang_off_execute(platform):
    result = await platform.investigate(default_request("AAPL", "T01"))
    spans = result.trace["spans"]
    root = next(s for s in spans if s["name"] == "harness.execute")
    tool_spans = [
        s
        for s in spans
        if s["name"].startswith("tool:") and "/" not in (s["attributes"].get("step_id") or "")
    ]
    assert tool_spans and all(s["parent_id"] == root["id"] for s in tool_spans)


# ---------------------------------------------------------- approvals counter
async def test_concurrent_approvals_do_not_flap_state(platform):
    """M5: two parallel approvals moved the state back to EXECUTING while one was pending."""
    req = InvestigationRequest(**{**default_request("AAPL", "T01").__dict__, "roles": frozenset({"viewer"})})
    task = platform.build_task(req)
    gateway = QueuedApprovalGateway(timeout_seconds=5)
    steps = [
        PlanStep(
            f"s{i}",
            i,
            StepType.TOOL_CALL,
            "stress",
            ToolRequest("risk.calculate_stress", {"symbol": "AAPL", "dataset": "T01"}),
        )
        for i in (1, 2)
    ]
    harness = await _harness(platform, FixedPlanner(steps), approvals=gateway)

    async def approve_both():
        seen = set()
        for _ in range(300):
            for pending in gateway.pending():
                if pending.id not in seen:
                    seen.add(pending.id)
                    gateway.decide(pending.id, True, "desk-head")
            if len(seen) == 2:
                return
            await asyncio.sleep(0.01)

    result, _ = await asyncio.gather(
        harness.execute(task, platform.policy_context(req, task)), approve_both()
    )
    transitions = [h["to"] for h in result.state_history]
    assert transitions.count("AWAITING_APPROVAL") == 1 and result.success


def test_decide_twice_is_a_conflict():
    async def run():
        g = QueuedApprovalGateway(timeout_seconds=2)
        from ceap.policy.approvals import ApprovalRequest

        req = ApprovalRequest.create("t", "s", "tool", {}, "why", "me")
        task = asyncio.create_task(g.request(req))
        await asyncio.sleep(0.01)
        g.decide(req.id, True, "a")
        with pytest.raises((RuntimeError, KeyError)):
            g.decide(req.id, False, "b")
        assert (await task).approved

    asyncio.run(run())


# ------------------------------------------------------------ config safety
def test_dev_keys_do_not_apply_outside_dev(monkeypatch):
    monkeypatch.setenv("CEAP_ENV", "prod")
    monkeypatch.delenv("CEAP_API_KEYS", raising=False)
    monkeypatch.delenv("CEAP_AUTO_APPROVE", raising=False)
    s = Settings()
    assert s.api_keys == {} and s.auto_approve is False
    with pytest.raises(SettingsError):
        s.validate_for_serving()
    monkeypatch.setenv("CEAP_API_KEYS", "dev-admin-key:admin")
    with pytest.raises(SettingsError):
        Settings().validate_for_serving()
    monkeypatch.setenv("CEAP_API_KEYS", "prodkey-123456:trader")
    Settings().validate_for_serving()
    monkeypatch.setenv("CEAP_API_KEYS", "short:trader")
    with pytest.raises(SettingsError):
        Settings()
    monkeypatch.setenv("CEAP_API_KEYS", "prodkey-123456:wizard")
    with pytest.raises(SettingsError):
        Settings()
    monkeypatch.setenv("CEAP_LLM_PROVIDER", "openai")
    with pytest.raises(SettingsError):
        Settings()


# -------------------------------------------------------- mock LLM injection
async def test_mock_planner_ignores_braces_in_the_question(platform):
    """H7: a question containing '{}' replaced the planner parameters."""
    req = InvestigationRequest(
        **{**default_request("AAPL", "T01").__dict__, "question": "Analyse AAPL between 14:00 and 15:00 {}"}
    )
    result = await platform.investigate(req)
    assert result.success and not result.warnings
    assert all(s["status"] == "SUCCESS" for s in result.step_results if s["type"] == "TOOL_CALL")


# ---------------------------------------------------------- numerics / data
def test_venue_volume_is_summed_not_last_print(store):
    ds = store.get("T01")
    trades = [t for t in ds.trades if ds.window_start <= t.timestamp < ds.window_end]
    stats = calculate_market_statistics("AAPL", ds.window_start, ds.window_end, [], trades, [])
    assert sum(stats.venue_volume.values()) == stats.traded_volume > 100_000


def test_nan_is_scrubbed_from_json_boundary():
    out = to_jsonable({"a": float("nan"), "b": float("inf"), "c": 1.5, "d": [float("nan")]})
    assert out == {"a": None, "b": None, "c": 1.5, "d": [None]}
    json.dumps(out, allow_nan=False)


def test_downsample_keeps_first_and_last():
    items = list(range(3599))
    out = downsample(items, 600)
    assert out[0] == 0 and out[-1] == 3598 and len(out) <= 600


def test_attribution_is_none_and_nan_safe(store):
    from ceap.domain.serialization import execution_metrics_from_dict, market_statistics_from_dict

    base = execution_metrics_from_dict(
        {
            "symbol": "AAPL",
            "start": "2026-09-18T13:00:00",
            "end": "2026-09-18T14:00:00",
            "implementation_shortfall_bps": None,
            "slippage_vs_vwap_bps": 3.0,
        }
    )
    win = execution_metrics_from_dict(
        {
            "symbol": "AAPL",
            "start": "2026-09-18T14:00:00",
            "end": "2026-09-18T15:00:00",
            "implementation_shortfall_bps": None,
            "slippage_vs_vwap_bps": 6.0,
            "reject_rate": None,
        }
    )
    mkt = market_statistics_from_dict(
        {"symbol": "AAPL", "start": "2026-09-18T14:00:00", "end": "2026-09-18T15:00:00"}
    )
    res = attribute_causes(win, base, mkt, mkt, engineering={"latency_ratio": None, "reject_rate": None})
    assert res.deteriorated is True  # VWAP slippage worsened by 3 bps even though IS is unknown
    assert math.isnan(res.is_delta_bps)


async def test_post_trade_impact_uses_horizon_after_last_fill(mcp_client):
    m = await mcp_client.invoke("execution", "get_execution_metrics", {**WIN, "dataset": "T01"})
    assert m["market_impact_bps"] is not None and m["temporary_impact_bps"] is not None
    # temporary impact is measured against a post-trade mid, so it is no longer identical to
    # (exec VWAP - mid at last fill); sanity: it stays within a few bps on the benign scenario
    assert abs(m["temporary_impact_bps"]) < 60


async def test_order_book_at_exact_timestamp_is_returned(mcp_client):
    book = await mcp_client.invoke(
        "market_data", "get_order_book", {"symbol": "AAPL", "timestamp": "2026-09-18T14:00:00"}
    )
    assert book["timestamp"].startswith("2026-09-18T14:00:00")


async def test_check_limit_uses_current_position_and_side(mcp_client):
    buy = await mcp_client.invoke(
        "risk", "check_limit", {"symbol": "AAPL", "order_quantity": 10_000, "side": "BUY"}
    )
    sell = await mcp_client.invoke(
        "risk", "check_limit", {"symbol": "AAPL", "order_quantity": 10_000, "side": "SELL"}
    )
    assert buy["projected_position"] - sell["projected_position"] == 20_000


# ----------------------------------------------------------- parsing / cli
def test_numeric_range_with_units_is_not_a_time_window():
    p = parse_question("Spread widened from 3 to 5 bps for AAPL between 14:00 and 15:00", date(2026, 9, 18))
    assert (p.window_start.hour, p.window_end.hour) == (14, 15)
    p2 = parse_question("AAPL on 2026-13-45 between 14:00 and 15:00", date(2026, 9, 18))
    assert p2.session_date == date(2026, 9, 18)  # malformed date ignored, no exception
    p3 = parse_question("Compare MSFT then AAPL between 9 and 10", date(2026, 9, 18))
    assert p3.symbol == "MSFT"


# ------------------------------------------------------------ memory bounds
def test_metrics_registry_is_bounded_and_escapes_labels():
    m = MetricsRegistry()
    for i in range(2000):
        m.observe("lat", float(i), tool=f"t{i}")
    assert sum(1 for k in m.snapshot() if k.startswith("lat_count")) <= 501
    m.inc("c", tool='a"b\n')
    assert 'tool="a\\"b\\n"' in m.render()
    m.observe("lat", float("nan"))  # ignored


async def test_router_marks_fallback_and_agents_surface_it(platform):
    class Broken(MockLLMClient):
        async def complete(self, request):
            raise ConnectionError("model unreachable")

    router = LLMRouter(Broken(), fallback=MockLLMClient())
    resp = await router.complete(LLMRequest("s", [{"role": "user", "content": "{}"}], purpose="narrative"))
    assert resp.fallback_reason and router.usage["fallbacks"] == 1
    p = Platform(settings=platform.settings, llm=router, store=platform.store)
    result = await p.investigate(default_request("AAPL", "T01"))
    assert result.success and any("LLM fallback used" in w for w in result.warnings)


def test_mock_llm_call_log_is_bounded():
    llm = MockLLMClient(keep_calls=3)
    for _ in range(10):
        asyncio.run(llm.complete(LLMRequest("s", [{"role": "user", "content": "x"}])))
    assert len(llm.calls) == 3
