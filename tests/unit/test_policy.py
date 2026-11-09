"""Policy engine and RBAC."""

from __future__ import annotations

from ceap.domain.policy import PolicyContext, PolicyDecision
from ceap.domain.tools import RiskLevel, ToolMetadata, ToolRequest
from ceap.policy.engine import RulePolicyEngine
from ceap.policy.permissions import Role, capabilities_for


def _ctx(role: str, **attrs) -> PolicyContext:
    return PolicyContext("p", frozenset({role}), capabilities_for({role}), "task", attributes=attrs)


READ = ToolMetadata("execution.get_executions", "get_executions", "", read_only=True)
WRITE = ToolMetadata("execution.cancel_order", "cancel_order", "", read_only=False, risk_level=RiskLevel.HIGH)
MEDIUM = ToolMetadata(
    "risk.calculate_stress", "calculate_stress", "", read_only=True, risk_level=RiskLevel.MEDIUM
)
CRITICAL = ToolMetadata("x.kill", "kill", "", read_only=False, risk_level=RiskLevel.CRITICAL)
CAPS = ToolMetadata(
    "eng.deep", "deep", "", read_only=True, required_capabilities=frozenset({"engineering:deep"})
)


async def test_trader_can_read_but_not_write():
    engine = RulePolicyEngine()
    assert (
        await engine.evaluate(ToolRequest(READ.id), READ, _ctx("trader"))
    ).decision is PolicyDecision.ALLOW
    ev = await engine.evaluate(ToolRequest(WRITE.id), WRITE, _ctx("trader"))
    assert ev.decision is PolicyDecision.DENY and ev.rule == "read-only"


async def test_admin_high_risk_write_still_requires_approval():
    # D4/A4 (0.8.0): risk:high no longer skips the approval; it only lets the admin ask for one
    ev = await RulePolicyEngine().evaluate(ToolRequest(WRITE.id), WRITE, _ctx("admin"))
    assert ev.decision is PolicyDecision.REQUIRE_APPROVAL and ev.rule == "risk-level"


async def test_harness_grant_covers_one_identical_high_risk_call():
    engine = RulePolicyEngine()
    ctx = _ctx("admin")
    engine.grant_approved(ctx.task_id, WRITE.id, {"qty": 1})
    other = await engine.evaluate(ToolRequest(WRITE.id, {"qty": 2}), WRITE, ctx)
    assert other.decision is PolicyDecision.REQUIRE_APPROVAL
    same = await engine.evaluate(ToolRequest(WRITE.id, {"qty": 1}), WRITE, ctx)
    assert same.decision is PolicyDecision.ALLOW
    again = await engine.evaluate(ToolRequest(WRITE.id, {"qty": 1}), WRITE, ctx)
    assert again.decision is PolicyDecision.REQUIRE_APPROVAL


async def test_medium_risk_requires_approval_for_viewer_not_trader():
    engine = RulePolicyEngine()
    assert (
        await engine.evaluate(ToolRequest(MEDIUM.id), MEDIUM, _ctx("viewer"))
    ).decision is PolicyDecision.REQUIRE_APPROVAL
    assert (
        await engine.evaluate(ToolRequest(MEDIUM.id), MEDIUM, _ctx("trader"))
    ).decision is PolicyDecision.ALLOW


async def test_critical_tools_are_never_allowed():
    ev = await RulePolicyEngine().evaluate(ToolRequest(CRITICAL.id), CRITICAL, _ctx("admin"))
    assert ev.decision is PolicyDecision.DENY and ev.rule == "risk-level"


async def test_required_capabilities():
    engine = RulePolicyEngine()
    assert (await engine.evaluate(ToolRequest(CAPS.id), CAPS, _ctx("trader"))).decision is PolicyDecision.DENY
    assert (
        await engine.evaluate(ToolRequest(CAPS.id), CAPS, _ctx("engineer"))
    ).decision is PolicyDecision.ALLOW


async def test_symbol_universe_and_argument_guards():
    engine = RulePolicyEngine(allowed_symbols=frozenset({"AAPL"}))
    ok = await engine.evaluate(ToolRequest(READ.id, {"symbol": "AAPL", "limit": 100}), READ, _ctx("trader"))
    assert ok.decision is PolicyDecision.ALLOW
    bad_symbol = await engine.evaluate(ToolRequest(READ.id, {"symbol": "TSLA"}), READ, _ctx("trader"))
    assert bad_symbol.decision is PolicyDecision.DENY and bad_symbol.rule == "symbol-universe"
    huge = await engine.evaluate(
        ToolRequest(READ.id, {"symbol": "AAPL", "limit": 10**6}), READ, _ctx("trader")
    )
    assert huge.decision is PolicyDecision.DENY and huge.rule == "argument-guard"
    long_arg = await engine.evaluate(ToolRequest(READ.id, {"query": "x" * 5000}), READ, _ctx("trader"))
    assert long_arg.decision is PolicyDecision.DENY
    # a per-request universe only narrows (D4/A3): TSLA outside the engine universe stays denied.
    # Before 0.8.0 the empty intersection read as "no universe" and let TSLA through.
    ctx_override = await engine.evaluate(
        ToolRequest(READ.id, {"symbol": "TSLA"}), READ, _ctx("trader", allowed_symbols={"TSLA"})
    )
    assert ctx_override.decision is PolicyDecision.DENY and ctx_override.rule == "symbol-universe"
    narrowed = await engine.evaluate(
        ToolRequest(READ.id, {"symbol": "AAPL"}), READ, _ctx("trader", allowed_symbols={"MSFT"})
    )
    assert narrowed.decision is PolicyDecision.DENY


async def test_deny_list_and_first_deny_wins():
    engine = RulePolicyEngine(denied_tools=frozenset({MEDIUM.id}))
    ev = await engine.evaluate(ToolRequest(MEDIUM.id), MEDIUM, _ctx("viewer"))
    assert ev.decision is PolicyDecision.DENY and ev.rule == "denied-tools"


def test_role_capabilities():
    assert "tools:write" in capabilities_for({"admin"})
    assert "tools:write" not in capabilities_for({"trader"})
    assert capabilities_for({"unknown-role"}) == frozenset()
    # Stage 2: research is for quants and admins; only admins may stage orders; everyone may read research
    assert "research" in capabilities_for({"quant"}) and "research" in capabilities_for({"admin"})
    assert "research" not in capabilities_for({"trader"}) and "research" not in capabilities_for({"viewer"})
    assert "trading:execute" in capabilities_for({"admin"})
    assert all("trading:execute" not in capabilities_for({r}) for r in ("trader", "quant", "engineer", "viewer"))
    assert all("research:read" in capabilities_for({r}) for r in ("trader", "quant", "engineer", "viewer", "admin"))
    assert Role("quant") is Role.QUANT
