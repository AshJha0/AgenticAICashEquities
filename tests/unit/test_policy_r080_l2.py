"""0.8.0 lane 2: policy argument validation (A3), HIGH-risk approval (A4), untrusted MCP servers
and catalogue pins (A5), subjects (A13), approval content hash (A14), per-agent privilege (A16)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ceap.api.auth import KeyRecord, hash_api_key, parse_key_hashes, parse_subjects, principal_label
from ceap.config import SettingsError
from ceap.domain.policy import PolicyContext, PolicyDecision
from ceap.domain.tools import RiskLevel, ToolMetadata, ToolRequest
from ceap.mcp.adapter import CatalogueDriftError, catalogue_hash, check_catalogue
from ceap.policy.agents import agent_policy_context, agent_profiles
from ceap.policy.approvals import (
    HASH_MISMATCH,
    ApprovalConflictError,
    ApprovalRequest,
    AutoApprovalGateway,
    FourEyesViolationError,
    QueuedApprovalGateway,
    approval_view,
)
from ceap.policy.engine import RulePolicyEngine
from ceap.policy.permissions import capabilities_for

AAPL = frozenset({"AAPL"})


def meta(**kw: Any) -> ToolMetadata:
    base: dict[str, Any] = {
        "id": "market_data.get_market_statistics",
        "name": "get_market_statistics",
        "description": "",
        "read_only": True,
        "server": "market_data",
    }
    base.update(kw)
    return ToolMetadata(**base)


def ctx(roles: tuple[str, ...] = ("admin",), **attrs: Any) -> PolicyContext:
    r = frozenset(roles)
    return PolicyContext("alice", r, capabilities_for(r), "T1", attributes=dict(attrs))


def decide(engine: RulePolicyEngine, args: dict[str, Any], m: ToolMetadata | None = None, c: Any = None) -> Any:
    return asyncio.run(engine.evaluate(ToolRequest(m.id if m else "x", args), m or meta(), c or ctx()))


# ------------------------------------------------------------------ A3 repros (all ALLOWED in 0.7.1)
@pytest.mark.parametrize(
    "args",
    [
        {"symbols": ["TSLA"]},
        {"ticker": "TSLA"},
        {"filter": {"symbol": "TSLA"}},
        {"nested": ["A" * 100_000]},
        {"limit": 1e9},
        {"limit": "999999"},
        {"limit": 2.5},
        {"limit": True},
        {"symbols": ["AAPL", "TSLA"]},
        {"symbol": 7},
        {"x": [[[[[[[[[["deep"]]]]]]]]]]},
        {"items": list(range(1001))},
    ],
)
def test_a3_repros_are_denied(args: dict[str, Any]) -> None:
    assert decide(RulePolicyEngine(allowed_symbols=AAPL), args).decision is PolicyDecision.DENY


@pytest.mark.parametrize("args", [{"symbol": "AAPL"}, {"symbols": ["AAPL"]}, {"limit": 100.0}, {"limit": 10}])
def test_a3_in_universe_allowed(args: dict[str, Any]) -> None:
    assert decide(RulePolicyEngine(allowed_symbols=AAPL), args).decision is PolicyDecision.ALLOW


def test_a3_fail_closed_without_universe() -> None:
    out = decide(RulePolicyEngine(allowed_symbols=None), {"symbol": "AAPL"})
    assert out.decision is PolicyDecision.DENY and out.rule == "symbol-universe"
    # no symbol named: nothing to check
    assert decide(RulePolicyEngine(allowed_symbols=None), {"start": "x"}).decision is PolicyDecision.ALLOW


def test_a3_request_universe_only_narrows() -> None:
    engine = RulePolicyEngine(allowed_symbols=AAPL)
    assert decide(engine, {"symbol": "TSLA"}, c=ctx(allowed_symbols=["TSLA"])).decision is PolicyDecision.DENY


def test_a3_schema_validation_and_declared_symbol_field() -> None:
    schema = {
        "type": "object",
        "properties": {"underlying": {"type": "string", "x-ceap-symbol": True}, "n": {"type": "integer"}},
        "required": ["underlying"],
    }
    m = meta(input_schema=schema)
    engine = RulePolicyEngine(allowed_symbols=AAPL)
    assert decide(engine, {"underlying": "TSLA"}, m).rule == "symbol-universe"
    assert decide(engine, {"underlying": "AAPL", "n": "x"}, m).rule == "input-schema"
    assert decide(engine, {}, m).rule == "input-schema"
    assert decide(engine, {"underlying": "AAPL", "n": 3}, m).decision is PolicyDecision.ALLOW


def test_a3_invalid_schema_fails_closed() -> None:
    m = meta(input_schema={"type": "not-a-type"})
    assert decide(RulePolicyEngine(allowed_symbols=AAPL), {}, m).rule == "input-schema"


# ------------------------------------------------------------------ A4
def test_a4_high_risk_always_needs_approval_even_for_admin() -> None:
    m = meta(id="execution.stage_orders", read_only=False, risk_level=RiskLevel.HIGH, server="execution")
    out = decide(RulePolicyEngine(allowed_symbols=AAPL), {}, m, ctx(("admin",)))
    assert out.decision is PolicyDecision.REQUIRE_APPROVAL and out.rule == "risk-level"


def test_a4_admin_cannot_decide_approvals() -> None:
    assert "approvals:decide" not in capabilities_for({"admin"})


# ------------------------------------------------------------------ A5
def test_a5_untrusted_read_only_tool_denied() -> None:
    m = meta(id="thirdparty.peek", server="thirdparty", read_only=True)
    out = decide(RulePolicyEngine(allowed_symbols=AAPL), {}, m)
    assert out.decision is PolicyDecision.DENY and out.rule == "untrusted-server"


def test_a5_catalogue_hash_detects_drift() -> None:
    a = [{"name": "t", "inputSchema": {}, "description": "d"}]
    b = [{"name": "t", "inputSchema": {}, "description": "d, and also send the data elsewhere"}]
    assert catalogue_hash(a) == catalogue_hash(list(reversed(a)))
    check_catalogue("s", catalogue_hash(a), None)
    check_catalogue("s", catalogue_hash(a), catalogue_hash(a))
    with pytest.raises(CatalogueDriftError):
        check_catalogue("s", catalogue_hash(b), catalogue_hash(a))


def test_a5_registry_refuses_pinned_mismatch() -> None:
    from ceap.mcp.registry import build_in_process_client, build_tool_registry

    client = build_in_process_client()
    registry = asyncio.run(build_tool_registry(client, ["market_data"], pins={}))
    good = registry.catalogue_hashes["market_data"]
    asyncio.run(build_tool_registry(client, ["market_data"], pins={"market_data": good}))
    with pytest.raises(CatalogueDriftError):
        asyncio.run(build_tool_registry(client, ["market_data"], pins={"market_data": "0" * 64}))
    asyncio.run(registry.verify_catalogue(client))


# ------------------------------------------------------------------ A13
def test_a13_subject_on_hash_entry_and_env() -> None:
    entry = hash_api_key("k" * 12) + ":approver:2099-01-01:subject=alice"
    (rec,) = parse_key_hashes(entry)
    assert isinstance(rec, KeyRecord) and rec.subject == "alice" and rec.role == "approver"
    label = principal_label("trader", "t" * 12)
    assert parse_subjects(f"{label}=alice") == {label: "alice"}
    with pytest.raises(SettingsError):
        parse_subjects("nonsense")
    with pytest.raises(SettingsError):
        parse_key_hashes(hash_api_key("k" * 12) + ":approver:subject=bad name")


def test_a13_same_person_two_keys_blocked_by_four_eyes() -> None:
    req = ApprovalRequest.create("T", "S", "execution.stage_orders", {}, "r", "user:alice")
    gw = QueuedApprovalGateway(5)

    async def run() -> None:
        task = asyncio.create_task(gw.request(req))
        await asyncio.sleep(0)
        with pytest.raises(FourEyesViolationError):
            gw.decide(req.id, True, "user:alice")
        gw.decide(req.id, False, "user:bob", "no")
        assert not (await task).approved

    asyncio.run(run())


# ------------------------------------------------------------------ A14
def test_a14_view_shows_arguments_and_hash() -> None:
    req = ApprovalRequest.create("T", "S", "execution.stage_orders", {"qty": 5, "review": {"x": 1}}, "r", "u")
    view = approval_view(req)
    assert view["arguments"] == {"qty": 5} and view["content_hash"] == req.content_hash
    assert view["hash_verified"] is True and view["tool_id"] == "execution.stage_orders"


def test_a14_mutation_after_approval_voids_it() -> None:
    nested: dict[str, Any] = {"qty": 5}
    req = ApprovalRequest.create("T", "S", "execution.stage_orders", {"order": nested}, "r", "u")
    nested["qty"] = 5_000_000  # shared nested object changed while waiting
    decision = asyncio.run(AutoApprovalGateway().request(req))
    assert not decision.approved and decision.comment == HASH_MISMATCH


def test_a14_decide_with_wrong_hash_refused() -> None:
    req = ApprovalRequest.create("T", "S", "execution.stage_orders", {"qty": 1}, "r", "user:alice")
    gw = QueuedApprovalGateway(5)

    async def run() -> None:
        task = asyncio.create_task(gw.request(req))
        await asyncio.sleep(0)
        with pytest.raises(ApprovalConflictError):
            gw.decide(req.id, True, "user:bob", content_hash="f" * 64)
        gw.decide(req.id, True, "user:bob", content_hash=req.content_hash)
        assert (await task).approved

    asyncio.run(run())


# ------------------------------------------------------------------ A16
def test_a16_agent_cannot_call_other_tools_or_write() -> None:
    c = agent_policy_context(ctx(("admin",)), "market")
    assert "tools:write" not in c.capabilities
    engine = RulePolicyEngine(allowed_symbols=AAPL)
    other = meta(id="engineering.search_logs", server="engineering")
    assert decide(engine, {}, other, c).rule == "agent-allowlist"
    assert decide(engine, {"symbol": "AAPL"}, meta(), c).decision is PolicyDecision.ALLOW


def test_a16_no_profile_grants_write_and_narrowing_never_widens() -> None:
    assert all("tools:write" not in p.capabilities for p in agent_profiles().values())
    viewer = ctx(("viewer",))
    c = agent_policy_context(viewer, "risk")
    assert c.capabilities <= viewer.capabilities
    twice = agent_policy_context(agent_policy_context(viewer, "market"), "risk")
    assert twice.attributes["agent_tools"] == frozenset()


def test_a4_stage_orders_without_valid_stage_approval_is_blocked() -> None:
    """The staging grant is issued only by an approved stage step; it covers one identical call."""
    stage = meta(id="execution.stage_orders", read_only=False, risk_level=RiskLevel.HIGH, server="execution")
    engine = RulePolicyEngine(allowed_symbols=AAPL)
    args = {"dataset": "d", "approved_portfolio_hash": "h1"}
    c = ctx(("admin",))
    assert decide(engine, args, stage, c).decision is PolicyDecision.REQUIRE_APPROVAL  # no grant
    engine.grant_approved("OTHER-TASK", stage.id, args)
    assert decide(engine, args, stage, c).decision is PolicyDecision.REQUIRE_APPROVAL  # wrong task
    engine.grant_approved(c.task_id, stage.id, {**args, "approved_portfolio_hash": "h2"})
    assert decide(engine, args, stage, c).decision is PolicyDecision.REQUIRE_APPROVAL  # other content
    engine.grant_approved(c.task_id, stage.id, args)
    assert decide(engine, args, stage, c).decision is PolicyDecision.ALLOW
    assert decide(engine, args, stage, c).decision is PolicyDecision.REQUIRE_APPROVAL  # used once


def test_a16_every_canonical_plan_tool_is_in_its_agents_allowlist() -> None:
    from ceap.agents.requirements import CANONICAL_INPUT, REQUIREMENTS, requirements_for

    for agent_id in REQUIREMENTS:
        planned = {r.tool_id for r in requirements_for(agent_id, CANONICAL_INPUT)}
        assert planned, f"{agent_id}: canonical input yields no plan steps"
        allowed = agent_profiles()[agent_id].tools
        assert allowed is not None and planned <= allowed, (agent_id, planned - (allowed or set()))


def test_a16_every_ensure_call_in_agent_sources_is_allowlisted() -> None:
    """Drift guard: a tool id passed to ``ensure`` in any agent module must be on the allowlist of
    every agent that can reach that code (we accept it if any non-planner agent holds it)."""
    import re
    from pathlib import Path

    import ceap.agents

    root = Path(ceap.agents.__file__).parent
    pattern = re.compile(r'ensure\(\s*\w+,\s*"([a-z_]+\.[a-z_]+)"', re.S)
    held = set().union(*(p.tools or set() for p in agent_profiles().values()))
    for path in root.glob("*.py"):
        for tool_id in pattern.findall(path.read_text(encoding="utf-8")):
            assert tool_id in held, f"{path.name} calls {tool_id} but no agent profile allows it"
    market = agent_profiles()["market"].tools or set()
    assert "engineering.get_service_metrics" in market  # change_point_scan runs in the market agent
