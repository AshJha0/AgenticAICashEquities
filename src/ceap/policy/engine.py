"""Rule-based policy engine.

Rules are evaluated in order; the first DENY wins, then REQUIRE_APPROVAL,
otherwise ALLOW. Every evaluation records the rule that fired so decisions
are explainable in the audit trail.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ceap.domain.policy import PolicyContext, PolicyDecision, PolicyEngine, PolicyEvaluation
from ceap.domain.tools import RiskLevel, ToolMetadata, ToolRequest

Rule = Callable[[ToolRequest, ToolMetadata, PolicyContext], PolicyEvaluation | None]

MAX_PAGE_LIMIT = 5000


@dataclass
class RulePolicyEngine(PolicyEngine):
    allowed_symbols: frozenset[str] | None = None
    denied_tools: frozenset[str] = frozenset()
    max_page_limit: int = MAX_PAGE_LIMIT

    def __post_init__(self) -> None:
        self._rules: list[Rule] = [
            self._rule_denied_tools,
            self._rule_read_only,
            self._rule_capabilities,
            self._rule_argument_guards,
            self._rule_symbol_universe,
            self._rule_risk_level,
        ]

    async def evaluate(
        self, request: ToolRequest, metadata: ToolMetadata, context: PolicyContext
    ) -> PolicyEvaluation:
        pending_approval: PolicyEvaluation | None = None
        for rule in self._rules:
            outcome = rule(request, metadata, context)
            if outcome is None:
                continue
            if outcome.decision is PolicyDecision.DENY:
                return outcome
            if outcome.decision is PolicyDecision.REQUIRE_APPROVAL and pending_approval is None:
                pending_approval = outcome
        if pending_approval:
            return pending_approval
        return PolicyEvaluation(PolicyDecision.ALLOW, "all rules passed", "default-allow", metadata.id)

    # ------------------------------------------------------------------ rules
    def _rule_denied_tools(
        self, req: ToolRequest, meta: ToolMetadata, ctx: PolicyContext
    ) -> PolicyEvaluation | None:
        if meta.id in self.denied_tools:
            return PolicyEvaluation(
                PolicyDecision.DENY, f"tool {meta.id} is on the deny list", "denied-tools", meta.id
            )
        return None

    def _rule_read_only(
        self, req: ToolRequest, meta: ToolMetadata, ctx: PolicyContext
    ) -> PolicyEvaluation | None:
        if not meta.read_only and "tools:write" not in ctx.capabilities:
            return PolicyEvaluation(
                PolicyDecision.DENY, "principal lacks tools:write for a mutating tool", "read-only", meta.id
            )
        if meta.read_only and "tools:read" not in ctx.capabilities:
            return PolicyEvaluation(PolicyDecision.DENY, "principal lacks tools:read", "read-only", meta.id)
        return None

    def _rule_capabilities(
        self, req: ToolRequest, meta: ToolMetadata, ctx: PolicyContext
    ) -> PolicyEvaluation | None:
        missing = meta.required_capabilities - ctx.capabilities
        if missing:
            return PolicyEvaluation(
                PolicyDecision.DENY,
                f"missing capabilities: {sorted(missing)}",
                "required-capabilities",
                meta.id,
            )
        return None

    def _rule_argument_guards(
        self, req: ToolRequest, meta: ToolMetadata, ctx: PolicyContext
    ) -> PolicyEvaluation | None:
        limit = req.arguments.get("limit")
        if isinstance(limit, int) and limit > self.max_page_limit:
            return PolicyEvaluation(
                PolicyDecision.DENY,
                f"limit {limit} exceeds max {self.max_page_limit}",
                "argument-guard",
                meta.id,
            )
        for key, value in req.arguments.items():
            if isinstance(value, str) and len(value) > 2000:
                return PolicyEvaluation(
                    PolicyDecision.DENY, f"argument {key} too long", "argument-guard", meta.id
                )
        return None

    def _rule_symbol_universe(
        self, req: ToolRequest, meta: ToolMetadata, ctx: PolicyContext
    ) -> PolicyEvaluation | None:
        universe: Any = ctx.attributes.get("allowed_symbols", self.allowed_symbols)
        symbol = req.arguments.get("symbol")
        if universe and symbol and symbol not in universe:
            return PolicyEvaluation(
                PolicyDecision.DENY, f"symbol {symbol} outside permitted universe", "symbol-universe", meta.id
            )
        return None

    def _rule_risk_level(
        self, req: ToolRequest, meta: ToolMetadata, ctx: PolicyContext
    ) -> PolicyEvaluation | None:
        if meta.risk_level is RiskLevel.CRITICAL:
            return PolicyEvaluation(
                PolicyDecision.DENY, "critical-risk tools are not invocable by agents", "risk-level", meta.id
            )
        if meta.risk_level is RiskLevel.HIGH and "risk:high" not in ctx.capabilities:
            return PolicyEvaluation(
                PolicyDecision.REQUIRE_APPROVAL,
                "high-risk tool requires human approval",
                "risk-level",
                meta.id,
            )
        if meta.risk_level is RiskLevel.MEDIUM and "risk:medium" not in ctx.capabilities:
            return PolicyEvaluation(
                PolicyDecision.REQUIRE_APPROVAL,
                "medium-risk tool requires approval for this principal",
                "risk-level",
                meta.id,
            )
        return None
