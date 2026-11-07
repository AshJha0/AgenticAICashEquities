"""Round R1 (PLAN section 2, items 1.1-1.10): fail closed by default.

Every test name starts with the plan item it proves (``test_1_5_...`` proves item 1.5). No test
calls a real model (D15): model behaviour is scripted with fake clients.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from ceap.agents.base import BaseAgent
from ceap.agents.planner import PlannerAgent
from ceap.config import Settings, SettingsError
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.tasks import TaskStatus
from ceap.domain.tools import RiskLevel, ToolRequest
from ceap.harness.cancellation import CancellationToken, TaskCancelled
from ceap.harness.engine import AgentHarness
from ceap.llm import router as router_mod
from ceap.llm.client import AnthropicLLMClient, LLMClient, MockLLMClient, canonical_plan, is_retryable
from ceap.llm.models import LLMRequest, LLMResponse
from ceap.llm.pins import UnpinnedModelError, load_pins
from ceap.llm.router import LLMRouter, build_router
from ceap.mcp.adapter import MCPToolAdapter
from ceap.mcp.client import InProcessMCPClient, _read_only_hint
from ceap.mcp.registry import build_tool_registry
from ceap.mcp.server import MCPServerDefinition
from ceap.platform import Platform, default_request, default_research_request
from ceap.policy.approvals import (
    DISTINCT_FROM_KEY,
    ApprovalDecision,
    ApprovalGateway,
    ApprovalRequest,
    FourEyesViolationError,
    QueuedApprovalGateway,
    check_four_eyes,
)
from ceap.policy.engine import RulePolicyEngine
from ceap.policy.permissions import capabilities_for

ROOT = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------- fakes
class ScriptedLLM(LLMClient):
    """Returns scripted responses per purpose; anything unscripted is answered by the mock."""

    def __init__(self, script: dict[str, list[Any]] | None = None) -> None:
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.mock = MockLLMClient()
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        queue = self.script.get(request.purpose) or []
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, LLMResponse):
                return item
        return await self.mock.complete(request)


def _resp(content: str, stop_reason: str = "end_turn", model: str = "claude-opus-5-5", **kw: Any) -> LLMResponse:
    return LLMResponse(content, [], 10, 10, model, stop_reason=stop_reason, **kw)


async def _investigate(platform: Platform, llm: LLMClient, dataset: str = "T01", **kw: Any):
    p = Platform(settings=platform.settings, llm=llm, store=platform.store, history=platform.history, **kw)
    return await p.investigate(default_request("AAPL", dataset))


def _codes(result: Any) -> set[str]:
    return {w.code for w in result.warnings}


# =========================================================== 1.1 fail closed
def test_1_1_build_router_raises_when_anthropic_client_cannot_be_built(monkeypatch):
    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("anthropic package not installed")

    monkeypatch.setattr(router_mod, "AnthropicLLMClient", boom)
    with pytest.raises(SettingsError, match="cannot be built"):
        build_router(Settings(llm_provider="anthropic"))


def test_1_1_provider_auto_is_removed(monkeypatch):
    monkeypatch.setenv("CEAP_LLM_PROVIDER", "auto")
    with pytest.raises(SettingsError, match="removed in 0.4.0"):
        Settings()


def test_1_1_mock_only_when_requested(monkeypatch):
    monkeypatch.setenv("CEAP_ENV", "prod")
    monkeypatch.delenv("CEAP_LLM_PROVIDER", raising=False)
    with pytest.raises(SettingsError, match="must be set explicitly"):
        Settings()
    router = build_router(Settings(llm_provider="mock"))
    assert isinstance(router.primary, MockLLMClient) and router.fallback is None


# ================================================================ 1.2 Docker
def test_1_2_dockerfile_is_hardened():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.12" in text and "@sha256:" in text
    assert "--require-hashes" in text and ".[llm]" in text
    assert "HEALTHCHECK" in text and "\nUSER " in text and "USER root" not in text.split("\nUSER ")[-1]


def test_1_2_dockerignore_excludes_secrets_and_state():
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for entry in (".git", ".env", ".venv", "tests"):
        assert any(line.strip().rstrip("/") == entry for line in lines), entry


# ============================================================ 1.3 four-eyes
def test_1_3_approver_role_holds_decide_and_admin_does_not():
    assert "approvals:decide" in capabilities_for({"approver"})
    assert "approvals:decide" not in capabilities_for({"admin"})
    assert "trading:execute" not in capabilities_for({"approver"})


def _req(requested_by: str = "quant.a", **arguments: Any) -> ApprovalRequest:
    return ApprovalRequest.create("TASK-1", "gov-approval", "harness.human_approval", arguments, "r", requested_by)


def test_1_3_same_principal_cannot_request_and_approve():
    with pytest.raises(FourEyesViolationError):
        check_four_eyes(_req("alice"), "alice")


def test_1_3_proposal_approver_cannot_approve_staging():
    with pytest.raises(FourEyesViolationError, match="earlier stage"):
        check_four_eyes(_req("quant.a", **{DISTINCT_FROM_KEY: ["approver.b"]}), "approver.b")
    check_four_eyes(_req("quant.a", **{DISTINCT_FROM_KEY: ["approver.b"]}), "approver.c")  # distinct: allowed


def test_1_3_system_actor_is_exempt():
    check_four_eyes(_req("timeout"), "timeout")  # no exception


async def test_1_3_queued_gateway_rejects_self_approval():
    gw = QueuedApprovalGateway(timeout_seconds=5)
    request = _req("alice")
    waiter = asyncio.create_task(gw.request(request))
    await asyncio.sleep(0)
    with pytest.raises(FourEyesViolationError):
        gw.decide(request.id, True, "alice")
    gw.decide(request.id, True, "bob")
    assert (await waiter).approved


class _ScriptedGateway(ApprovalGateway):
    def __init__(self, deciders: list[str]) -> None:
        self.deciders = list(deciders)
        self.requests: list[ApprovalRequest] = []

    async def request(self, approval: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(approval)
        return ApprovalDecision(approval.id, True, self.deciders.pop(0), "ok")


async def test_1_3_two_distinct_principals_can_promote_and_stage(platform):
    gw = _ScriptedGateway(["approver.b", "approver.c"])
    p = Platform(settings=platform.settings, llm=MockLLMClient(), store=platform.store, history=platform.history, approvals=gw)
    result = await p.research(default_research_request("R01", stage_orders=True))
    assert result.success and result.report.proposal["staging"]["staged"]
    assert gw.requests[1].arguments[DISTINCT_FROM_KEY] == ["approver.b"]


async def test_1_3_harness_blocks_same_approver_for_staging(platform):
    gw = _ScriptedGateway(["approver.b", "approver.b"])  # a gateway that ignores four-eyes
    p = Platform(settings=platform.settings, llm=MockLLMClient(), store=platform.store, history=platform.history, approvals=gw)
    result = await p.research(default_research_request("R01", stage_orders=True))
    assert "FOUR_EYES_VIOLATION" in _codes(result)
    assert not result.report.proposal["staging"].get("staged")


# ===================================================== 1.4 default gateway
async def test_1_4_harness_without_gateway_denies(platform):
    base = await platform.harness()
    harness = AgentHarness(base.planner, base.agents, base.tools, base.policy, base.reporter, settings=platform.settings)
    req = default_research_request("R01")
    task = platform.build_research_task(req)
    result = await harness.execute(task, platform.policy_context(req, task))
    assert not result.success and "declined" in (result.error or "")


def test_1_4_non_dev_auto_approve_refuses_to_serve(monkeypatch):
    monkeypatch.setenv("CEAP_ENV", "prod")
    monkeypatch.setenv("CEAP_LLM_PROVIDER", "mock")
    monkeypatch.setenv("CEAP_API_KEYS", "prodkey-123456:trader")
    monkeypatch.setenv("CEAP_AUTO_APPROVE", "true")
    with pytest.raises(SettingsError, match="CEAP_AUTO_APPROVE"):
        Settings().validate_for_serving()


# ===================================================== 1.5 degradation ladder
def _pinned_router(primary: LLMClient, fallback: LLMClient | None = None) -> LLMRouter:
    return LLMRouter(primary, fallback=fallback, pins=load_pins())


async def test_1_5_rung1_refusal_falls_back_to_pinned_model(platform):
    refusal = _resp("", "refusal", stop_details={"type": "refusal", "category": "cyber"})
    llm = ScriptedLLM({"planning": [refusal]})
    result = await _investigate(platform, _pinned_router(llm))
    assert "LLM_REFUSAL_FALLBACK" in _codes(result) and result.status is TaskStatus.DEGRADED
    assert llm.requests[1].model == load_pins().for_purpose("planning").fallback_model
    assert any("refused (category cyber)" in w.message for w in result.warnings)


async def test_1_5_rung2_repairs_unparsable_plan_once(platform):
    llm = ScriptedLLM({"planning": [_resp("not json at all")]})
    result = await _investigate(platform, _pinned_router(llm))
    assert "LLM_REPAIR_RETRY" in _codes(result) and result.status is TaskStatus.DEGRADED
    assert result.plan.generated_by.startswith("llm:")  # the repaired plan was used
    assert len([r for r in llm.requests if r.purpose == "planning"]) == 2


async def test_1_5_rung3_client_failure_uses_deterministic_path(platform):
    llm = ScriptedLLM({"planning": [ConnectionError("down")], "narrative": [ConnectionError("down")]})
    result = await _investigate(platform, _pinned_router(llm, fallback=MockLLMClient()))
    assert "LLM_ERROR_FALLBACK" in _codes(result) and result.status is TaskStatus.DEGRADED
    assert any("LLM fallback used" in w.message for w in result.warnings)


async def test_1_5_rung3_without_fallback_canonical_plan_and_template(platform):
    llm = ScriptedLLM({"planning": [RuntimeError("x")], "narrative": [RuntimeError("y")], "critique": [RuntimeError("z")]})
    result = await _investigate(platform, _pinned_router(llm))
    assert result.success and result.status is TaskStatus.DEGRADED
    assert {"LLM_CALL_FAILED", "LLM_TEMPLATE_NARRATIVE", "LLM_CRITIQUE_FAILED"} <= _codes(result)
    assert result.plan.generated_by == "canonical-fallback"


async def test_1_5_max_tokens_stop_is_recorded_with_details(platform):
    truncated = _resp("EXECUTIVE SUMMARY\n\nExecution qual", "max_tokens", model="claude-sonnet-5-5")
    result = await _investigate(platform, _pinned_router(ScriptedLLM({"narrative": [truncated]})))
    assert {"LLM_STOP_MAX_TOKENS", "LLM_TEMPLATE_NARRATIVE"} <= _codes(result)
    assert "Execution qual" not in result.report.narrative.split("\n")[2]


async def test_1_5_rung4_kill_switch_forces_deterministic_path_with_banner(platform):
    settings = Settings(llm_provider="mock", llm_disabled=True)
    router = build_router(settings)
    p = Platform(settings=settings, llm=router, store=platform.store, history=platform.history)
    result = await p.investigate(default_request("AAPL", "T01"))
    assert result.success and result.status is TaskStatus.DEGRADED
    assert result.banner and "LLM DISABLED" in result.banner
    assert result.report.narrative.startswith("LLM DISABLED")
    assert result.to_dict()["banner"] == result.banner
    with pytest.raises(router_mod.LLMDisabledError):
        await router.complete(LLMRequest("s", [], purpose="planning"))


async def test_1_5_clean_run_is_completed_not_degraded(platform):
    result = await platform.investigate(default_request("AAPL", "T01"))
    assert result.status is TaskStatus.COMPLETED and not result.warnings


# ===================================================== 1.6 required agents
class _PlanWithoutQuant(LLMClient):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        plan = canonical_plan(dict(request.metadata.get("parameters") or {}))
        plan["steps"] = [s for s in plan["steps"] if s.get("agent_id") != "quant"]
        return _resp(json.dumps(plan))


async def test_1_6_plan_without_quant_still_runs_quant(platform):
    base = await platform.harness()
    harness = AgentHarness(
        PlannerAgent(_PlanWithoutQuant()), base.agents, base.tools, base.policy, base.reporter, settings=platform.settings
    )
    req = default_request("AAPL", "T05")
    task = platform.build_task(req)
    result = await harness.execute(task, platform.policy_context(req, task))
    assert result.success and "gov-required-quant" in [s.id for s in result.plan.steps]
    assert result.agent_outputs["quant"]["success"]


class _Exploding(BaseAgent):
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id

    async def execute(self, context: AgentContext) -> AgentResult:
        raise RuntimeError(f"{self.agent_id} exploded")


async def test_1_6_failed_agent_yields_incomplete_analysis_not_a_pass(platform):
    base = await platform.harness()
    agents = {**base.agents, "quant": _Exploding("quant")}
    harness = AgentHarness(base.planner, agents, base.tools, base.policy, base.reporter, settings=platform.settings)
    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    result = await harness.execute(task, platform.policy_context(req, task))
    assert result.status is TaskStatus.INCOMPLETE_ANALYSIS and result.incomplete_agents == ["quant"]
    assert "REQUIRED_AGENT_FAILED" in _codes(result)
    text = (result.report.executive_summary + result.report.conclusion).lower()
    assert "incomplete analysis" in text and "no material anomaly identified" not in text


class _SlowWithCheckpoint(BaseAgent):
    agent_id = "market"

    async def execute(self, context: AgentContext) -> AgentResult:
        self.checkpoint(context, regime="partial: volatility computed")
        await asyncio.sleep(30)
        raise AssertionError("unreachable")


async def test_1_6_timed_out_agent_returns_partial_output(platform):
    base = await platform.harness()
    settings = Settings(llm_provider="mock", task_timeout_seconds=5.0)
    harness = AgentHarness(
        base.planner, {**base.agents, "market": _SlowWithCheckpoint()}, base.tools, base.policy, base.reporter, settings=settings
    )
    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    result = await harness.execute(task, platform.policy_context(req, task))
    record = next(r for r in result.step_results if r.get("agent_id") == "market")
    assert record["status"] == "PARTIAL"
    assert result.agent_outputs["market"]["regime"] == "partial: volatility computed"


# =========================================================== 1.7 MCP trust
def _untrusted_server() -> MCPServerDefinition:
    server = MCPServerDefinition("thirdparty", "untrusted")

    async def write_something(value: str) -> dict[str, Any]:
        return {"ok": value}

    async def drifted() -> dict[str, Any]:
        return {"schema_version": 99, "rows": []}

    server.tool("Writes.")(write_something)
    server.tool("Drifted.")(drifted)
    return server


def test_1_7_missing_annotations_fail_closed():
    adapter = MCPToolAdapter(InProcessMCPClient(), "thirdparty", {"name": "t", "inputSchema": {}})
    assert adapter.metadata.read_only is False and adapter.metadata.risk_level is RiskLevel.HIGH
    bogus = MCPToolAdapter(InProcessMCPClient(), "x", {"name": "t", "annotations": {"riskLevel": "TRIVIAL"}})
    assert bogus.metadata.risk_level is RiskLevel.HIGH


def test_1_7_stdio_hint_parsing_fails_closed():
    class Ann:
        readOnlyHint = None  # noqa: N815 - MCP wire name

    assert _read_only_hint(None) is False and _read_only_hint(Ann()) is False
    Ann.readOnlyHint = True
    assert _read_only_hint(Ann()) is True


async def test_1_7_untrusted_server_denied_any_write_in_process():
    client = InProcessMCPClient({"thirdparty": _untrusted_server()})
    descriptors = await client.discover_tools("thirdparty")
    for d in descriptors:  # an unannotated server: strip every hint
        d.pop("annotations", None)
    adapter = MCPToolAdapter(client, "thirdparty", descriptors[0])
    from ceap.domain.policy import PolicyContext, PolicyDecision

    ctx = PolicyContext(principal="admin.a", roles=frozenset({"admin"}), capabilities=capabilities_for({"admin"}), task_id="T")
    ev = await RulePolicyEngine().evaluate(ToolRequest(adapter.id, {"value": "x"}), adapter.metadata, ctx)
    assert ev.decision is PolicyDecision.DENY and ev.rule in ("untrusted-server", "read-only")
    trusted = RulePolicyEngine(trusted_servers=frozenset({"thirdparty"}))
    ev2 = await trusted.evaluate(ToolRequest(adapter.id, {"value": "x"}), adapter.metadata, ctx)
    assert ev2.rule != "untrusted-server"


async def test_1_7_untrusted_server_lying_read_only_hint_still_cannot_write():
    from ceap.domain.policy import PolicyContext, PolicyDecision
    from ceap.domain.tools import ToolMetadata

    meta = ToolMetadata("thirdparty.w", "w", "", read_only=False, server="thirdparty")
    ctx = PolicyContext(principal="admin.a", roles=frozenset({"admin"}), capabilities=capabilities_for({"admin"}), task_id="T")
    ev = await RulePolicyEngine().evaluate(ToolRequest(meta.id), meta, ctx)
    assert ev.decision is PolicyDecision.DENY and ev.rule == "untrusted-server"


async def test_1_7_drifted_schema_version_is_rejected():
    client = InProcessMCPClient({"thirdparty": _untrusted_server()})
    registry = await build_tool_registry(client)
    from ceap.domain.common import utc_now
    from ceap.domain.tools import ToolExecutionContext

    tool = registry.get("thirdparty.drifted")
    result = await tool.execute(ToolRequest(tool.id), ToolExecutionContext("T", "E", "s", utc_now(), "admin.a"))
    assert not result.ok and "schema_version" in (result.error or "")


# ================================================== 1.8 standalone servers
async def test_1_8_stdio_server_enforces_required_capabilities(monkeypatch, store, history):
    pytest.importorskip("mcp")
    from ceap.mcp.execution.server import build_server

    monkeypatch.delenv("CEAP_MCP_PRINCIPAL_ROLES", raising=False)
    fast = build_server(store, history=history).to_fastmcp()
    with pytest.raises(Exception, match="trading:execute"):
        await fast.call_tool("stage_orders", {"signal": "momentum_12_1"})
    monkeypatch.setenv("CEAP_MCP_PRINCIPAL_ROLES", "quant")
    fast = build_server(store, history=history).to_fastmcp()
    with pytest.raises(Exception, match="policy"):
        await fast.call_tool("stage_orders", {"signal": "momentum_12_1"})


# =========================================================== 1.9 pins etc.
def test_1_9_unpinned_model_is_refused():
    with pytest.raises(SettingsError, match="not pinned"):
        Settings(llm_provider="mock", llm_model="claude-sonnet-4-5")
    with pytest.raises(UnpinnedModelError):
        AnthropicLLMClient(model="gpt-whatever", client=object())


def test_1_9_wire_shape_has_thinking_effort_and_headroom():
    client = AnthropicLLMClient(client=object())
    assert "temperature" not in LLMRequest.__dataclass_fields__
    expected = {"planning": 16000, "critique": 8000, "narrative": 8000}
    for purpose, max_tokens in expected.items():
        kw = client.request_kwargs(LLMRequest("s", [{"role": "user", "content": "x"}], purpose=purpose))
        assert kw["max_tokens"] == max_tokens and kw["thinking"] == {"type": "adaptive"}
        assert kw["output_config"]["effort"] in {"low", "medium", "high", "xhigh", "max"}
        assert kw["model"] in load_pins().allowed and kw["timeout"] == 60.0


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _FakeMessages:
    def __init__(self, errors: list[Exception]) -> None:
        self.errors, self.calls = list(errors), 0

    async def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)

        class _U:
            input_tokens = output_tokens = 1

        class _B:
            type, text = "text", "ok"

        class _R:
            content, usage, model, stop_reason, stop_details = [_B()], _U(), kwargs["model"], "end_turn", None

        return _R()


class _FakeSDK:
    def __init__(self, errors: list[Exception]) -> None:
        self.messages = _FakeMessages(errors)


async def test_1_9_400_not_retried_429_and_5xx_retried():
    assert not is_retryable(_StatusError(400)) and is_retryable(_StatusError(429)) and is_retryable(_StatusError(503))
    sdk = _FakeSDK([_StatusError(400)])
    with pytest.raises(_StatusError):
        await AnthropicLLMClient(client=sdk, retry_base_delay_s=0).complete(LLMRequest("s", []))
    assert sdk.messages.calls == 1
    sdk = _FakeSDK([_StatusError(429), _StatusError(503)])
    out = await AnthropicLLMClient(client=sdk, retry_base_delay_s=0).complete(LLMRequest("s", []))
    assert out.content == "ok" and sdk.messages.calls == 3


class _Hang(LLMClient):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        await asyncio.sleep(60)
        raise AssertionError("unreachable")


def _ctx(token: CancellationToken, scratch: dict[str, Any]) -> AgentContext:
    from datetime import timedelta

    from ceap.domain.common import utc_now

    return AgentContext(
        task_id="T", execution_id="E", deadline=utc_now() + timedelta(minutes=1), task_input={},
        current_plan=None, evidence=(), tool_registry=None, policy_context=None, tools=None,  # type: ignore[arg-type]
        state={"scratch": scratch}, cancellation_event=token,
    )


async def test_1_9_cancel_mid_call_returns_under_one_second():
    token = CancellationToken()
    agent = _Plain()
    asyncio.get_running_loop().call_later(0.1, token.cancel, "user")
    started = time.perf_counter()
    with pytest.raises(TaskCancelled):
        await agent.call_llm(_ctx(token, {}), _Hang(), LLMRequest("s", []))
    assert time.perf_counter() - started < 1.0


async def test_1_9_per_task_call_cap():
    router = LLMRouter(MockLLMClient(), max_calls_per_task=2)
    scratch: dict[str, Any] = {}
    agent = _Plain()
    for _ in range(2):
        await agent.call_llm(_ctx(CancellationToken(), scratch), router, LLMRequest("s", [{"role": "user", "content": "x"}]))
    with pytest.raises(router_mod.LLMBudgetExceededError):
        await agent.call_llm(_ctx(CancellationToken(), scratch), router, LLMRequest("s", []))


async def test_1_9_router_timeout_is_enforced():
    with pytest.raises(TimeoutError):
        await LLMRouter(_Hang(), timeout_s=0.05).complete(LLMRequest("s", []))


def test_1_9_sdk_pin_matches_lock_and_no_retired_model_ids():
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert f"anthropic=={load_pins().sdk_version}" in lock
    hits = subprocess.run(
        ["git", "grep", "-l", "sonnet-4-5", "--", "src", "tests/unit", "README.md", "LEARN.md", "Dockerfile"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.strip()
    assert hits == "", hits


# ===================================================== 1.10 public hygiene
def test_1_10_readme_banner_on_first_screen():
    head = "\n".join((ROOT / "README.md").read_text(encoding="utf-8").splitlines()[:30]).lower()
    for phrase in ("synthetic data", "nothing trades", "paper orders", "no live model"):
        assert phrase in head, phrase


def test_1_10_no_banned_job_prep_word():
    word = "inter" + "view"
    hits = subprocess.run(["git", "grep", "-il", word], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert hits == "", hits
    assert not (ROOT / "Claude outputs" / "ci.yml").exists()


class _Plain(BaseAgent):
    async def execute(self, context: AgentContext) -> AgentResult:
        raise NotImplementedError
