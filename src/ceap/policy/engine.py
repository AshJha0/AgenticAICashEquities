"""Rule-based policy engine.

Rules are evaluated in order; the first DENY wins, then REQUIRE_APPROVAL,
otherwise ALLOW. Every evaluation records the rule that fired so decisions
are explainable in the audit trail.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from ceap.domain.policy import PolicyContext, PolicyDecision, PolicyEngine, PolicyEvaluation
from ceap.domain.tools import RiskLevel, ToolMetadata, ToolRequest
from ceap.policy.agents import AGENT_ATTRIBUTE, AGENT_TOOLS_ATTRIBUTE
from ceap.rag.query import allowed_classifications, parse_classifications

Rule = Callable[[ToolRequest, ToolMetadata, PolicyContext], PolicyEvaluation | None]

MAX_PAGE_LIMIT = 5000
#: argument size guards (A3), applied to every nested value, not only the top level
MAX_STRING_LENGTH = 2000
MAX_COLLECTION_ITEMS = 1000
MAX_ARGUMENT_DEPTH = 8
MAX_ARGUMENT_NODES = 10_000
MAX_KEY_LENGTH = 200
#: argument keys that carry instrument symbols wherever they appear (A3). A tool declares further
#: symbol-bearing fields with ``"x-ceap-symbol": true`` on a property of its input schema.
SYMBOL_KEYS = frozenset(
    {"symbol", "symbols", "ticker", "tickers", "instrument", "instruments", "ric", "rics", "isin", "isins"}
)
SCHEMA_SYMBOL_FLAG = "x-ceap-symbol"
#: knowledge tools whose ``classifications`` argument is checked against the principal's roles (3C.3)
KNOWLEDGE_CLASSIFIED_TOOLS = frozenset({"knowledge.search_documents", "knowledge.get_document"})
#: built-in MCP servers; overridden by CEAP_MCP_TRUSTED_SERVERS (comma-separated)
DEFAULT_TRUSTED_SERVERS = (
    "market_data",
    "execution",
    "risk",
    "engineering",
    "knowledge",
    "research_data",
    "alpha",
    "backtest",
)


def trusted_servers_from_env() -> frozenset[str]:
    raw = os.getenv("CEAP_MCP_TRUSTED_SERVERS")
    if raw is None or raw.strip() == "":
        return frozenset(DEFAULT_TRUSTED_SERVERS)
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


@dataclass
class RulePolicyEngine(PolicyEngine):
    """``allowed_symbols=None`` means no universe is configured: any request naming a symbol is
    denied (fail closed, D4). Pass the permitted universe explicitly."""

    allowed_symbols: frozenset[str] | None = None
    denied_tools: frozenset[str] = frozenset()
    max_page_limit: int = MAX_PAGE_LIMIT
    trusted_servers: frozenset[str] = field(default_factory=trusted_servers_from_env)

    def __post_init__(self) -> None:
        self._rules: list[Rule] = [
            self._rule_denied_tools,
            self._rule_untrusted_server,
            self._rule_agent_allowlist,
            self._rule_read_only,
            self._rule_capabilities,
            self._rule_argument_guards,
            self._rule_input_schema,
            self._rule_symbol_universe,
            self._rule_knowledge_classification,
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

    def _rule_untrusted_server(
        self, req: ToolRequest, meta: ToolMetadata, ctx: PolicyContext
    ) -> PolicyEvaluation | None:
        """Every tool of an MCP server outside the allowlist is denied (A5, T20).

        The server's own ``readOnlyHint`` is not evidence: an untrusted server can declare a
        mutating tool read-only. Trust comes only from ``CEAP_MCP_TRUSTED_SERVERS``.
        """
        if meta.server is None or meta.server in self.trusted_servers:
            return None
        return PolicyEvaluation(
            PolicyDecision.DENY,
            f"server {meta.server} is not in trusted_servers; its tools are denied",
            "untrusted-server",
            meta.id,
        )

    def _rule_agent_allowlist(
        self, req: ToolRequest, meta: ToolMetadata, ctx: PolicyContext
    ) -> PolicyEvaluation | None:
        """A call made by an agent may use only that agent's tools (A16, see ``policy/agents.py``)."""
        allowed = ctx.attributes.get(AGENT_TOOLS_ATTRIBUTE)
        if allowed is None or meta.id in allowed:
            return None
        agent = ctx.attributes.get(AGENT_ATTRIBUTE, "?")
        return PolicyEvaluation(
            PolicyDecision.DENY,
            f"agent {agent} is not allowed to call {meta.id}",
            "agent-allowlist",
            meta.id,
        )

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
        """Size guards over the whole argument tree, then the ``limit`` bound (A3).

        ``limit`` is coerced to a whole number before the bound, so ``1e9`` or ``"999999"`` cannot
        pass the way they did when only ``int`` values were compared. Nested ``limit`` keys count.
        """
        problem = _size_problem(req.arguments)
        if problem:
            return PolicyEvaluation(PolicyDecision.DENY, problem, "argument-guard", meta.id)
        for path, key, value in _walk(req.arguments):
            if key != "limit" or value is None or isinstance(value, (dict, list, tuple)):
                continue
            limit = _as_int(value)
            if limit is None:
                return PolicyEvaluation(
                    PolicyDecision.DENY, f"{path} must be a whole number", "argument-guard", meta.id
                )
            if limit < 0 or limit > self.max_page_limit:
                return PolicyEvaluation(
                    PolicyDecision.DENY,
                    f"{path} {limit} outside 0..{self.max_page_limit}",
                    "argument-guard",
                    meta.id,
                )
        return None

    def _rule_input_schema(
        self, req: ToolRequest, meta: ToolMetadata, ctx: PolicyContext
    ) -> PolicyEvaluation | None:
        """Arguments must validate against the tool's declared JSON schema (A3).

        An empty schema accepts anything. A schema that is itself invalid fails closed.
        """
        if not meta.input_schema:
            return None
        try:
            validator = _validator(meta.input_schema)
            error = jsonschema.exceptions.best_match(validator.iter_errors(req.arguments))
        except jsonschema.exceptions.SchemaError as exc:
            return PolicyEvaluation(
                PolicyDecision.DENY,
                f"tool input schema is invalid: {exc.message[:200]}",
                "input-schema",
                meta.id,
            )
        if error is not None:
            where = "/".join(str(p) for p in error.absolute_path) or "(root)"
            return PolicyEvaluation(
                PolicyDecision.DENY,
                f"arguments fail the input schema at {where}: {error.message[:200]}",
                "input-schema",
                meta.id,
            )
        return None

    def _rule_symbol_universe(
        self, req: ToolRequest, meta: ToolMetadata, ctx: PolicyContext
    ) -> PolicyEvaluation | None:
        """Every symbol-bearing field, at any depth, must be inside the permitted universe (A3).

        Symbol-bearing fields are the keys in :data:`SYMBOL_KEYS` wherever they appear, plus the
        schema properties flagged ``x-ceap-symbol``. A value may be a string or a list of strings.
        With no universe configured, a request that names any symbol is denied (fail closed, D4).
        """
        universe: frozenset[str] | None = self.allowed_symbols
        requested = ctx.attributes.get("allowed_symbols")
        if requested:  # a per-request universe can only narrow the engine's universe, never widen it
            narrowed = frozenset(str(s) for s in requested)
            universe = narrowed & universe if universe is not None else narrowed
        symbol_keys = SYMBOL_KEYS | _schema_symbol_keys(meta.input_schema)
        for path, key, value in _walk(req.arguments):
            if key not in symbol_keys or value is None or isinstance(value, (list, tuple)):
                continue  # list items are visited one by one under the list's key
            if not isinstance(value, str):
                return PolicyEvaluation(
                    PolicyDecision.DENY, f"{path} must be a symbol string", "argument-guard", meta.id
                )
            if universe is None:
                return PolicyEvaluation(
                    PolicyDecision.DENY,
                    f"{path} names a symbol but no symbol universe is configured",
                    "symbol-universe",
                    meta.id,
                )
            if value not in universe:
                return PolicyEvaluation(
                    PolicyDecision.DENY,
                    f"symbol {value[:32]} at {path} outside permitted universe",
                    "symbol-universe",
                    meta.id,
                )
        return None

    def _rule_knowledge_classification(
        self, req: ToolRequest, meta: ToolMetadata, ctx: PolicyContext
    ) -> PolicyEvaluation | None:
        """R3C 3C.3: a knowledge read may only ask for classifications the principal's roles allow.

        Omitting ``classifications`` is safe: the server then reads public and internal only.
        """
        if meta.id not in KNOWLEDGE_CLASSIFIED_TOOLS:
            return None
        try:
            requested = parse_classifications(req.arguments.get("classifications"))
        except ValueError as exc:
            return PolicyEvaluation(PolicyDecision.DENY, str(exc), "argument-guard", meta.id)
        if requested is None:
            return None
        excess = sorted(set(requested) - set(allowed_classifications(ctx.roles)))
        if excess:
            return PolicyEvaluation(
                PolicyDecision.DENY,
                f"roles {sorted(ctx.roles)} may not read {excess} knowledge documents",
                "knowledge-classification",
                meta.id,
            )
        return None

    def _rule_risk_level(
        self, req: ToolRequest, meta: ToolMetadata, ctx: PolicyContext
    ) -> PolicyEvaluation | None:
        """CRITICAL is never invocable; HIGH always needs a human approval (A4).

        No capability removes the approval for a HIGH-risk tool. Who may decide it is settled at
        decision time: a different person holding ``approvals:decide`` (four-eyes), so an admin
        cannot approve their own request.
        """
        if meta.risk_level is RiskLevel.CRITICAL:
            return PolicyEvaluation(
                PolicyDecision.DENY, "critical-risk tools are not invocable by agents", "risk-level", meta.id
            )
        if meta.risk_level is RiskLevel.HIGH:
            return PolicyEvaluation(
                PolicyDecision.REQUIRE_APPROVAL,
                "high-risk tool requires approval by a second person (four-eyes)",
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


# ------------------------------------------------------------------ argument helpers


def _walk(value: Any, path: str = "$", key: str | None = None) -> Iterator[tuple[str, str | None, Any]]:
    """Yield ``(path, key, value)`` for every node; ``key`` is the dict key the value sits under.

    List items inherit the list's key, so ``{"symbols": ["A", "B"]}`` yields each item under
    ``symbols``. Call only after :func:`_size_problem` has bounded the tree's depth.
    """
    yield path, key, value
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, f"{path}.{k}", str(k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]", key)


def _size_problem(arguments: Any) -> str | None:
    """The first size violation in the argument tree, or ``None`` (iterative: no recursion limit)."""
    stack: list[tuple[str, Any, int]] = [("$", arguments, 0)]
    nodes = 0
    while stack:
        path, value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_ARGUMENT_NODES:
            return f"arguments hold more than {MAX_ARGUMENT_NODES} values"
        if depth > MAX_ARGUMENT_DEPTH:
            return f"argument {path} is nested deeper than {MAX_ARGUMENT_DEPTH}"
        if isinstance(value, str):
            if len(value) > MAX_STRING_LENGTH:
                return f"argument {path} too long ({len(value)} > {MAX_STRING_LENGTH} characters)"
        elif isinstance(value, dict):
            if len(value) > MAX_COLLECTION_ITEMS:
                return f"argument {path} has more than {MAX_COLLECTION_ITEMS} entries"
            for k, v in value.items():
                if not isinstance(k, str) or len(k) > MAX_KEY_LENGTH:
                    return f"argument {path} has a non-string or over-long key"
                stack.append((f"{path}.{k}", v, depth + 1))
        elif isinstance(value, (list, tuple)):
            if len(value) > MAX_COLLECTION_ITEMS:
                return f"argument {path} has more than {MAX_COLLECTION_ITEMS} items"
            stack.extend((f"{path}[{i}]", v, depth + 1) for i, v in enumerate(value))
        elif isinstance(value, float) and not math.isfinite(value):
            return f"argument {path} is not a finite number"
    return None


def _as_int(value: Any) -> int | None:
    """``value`` as an int when it is a whole number (int, integral float or numeric string)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


_VALIDATORS: dict[str, Any] = {}
_MAX_CACHED_VALIDATORS = 1024


def _validator(schema: dict[str, Any]) -> Any:
    """A checked validator for ``schema``, cached by its JSON text."""
    import json

    key = json.dumps(schema, sort_keys=True, default=str)
    cached = _VALIDATORS.get(key)
    if cached is None:
        cls = jsonschema.validators.validator_for(schema, default=jsonschema.Draft202012Validator)
        cls.check_schema(schema)
        cached = cls(schema)
        if len(_VALIDATORS) < _MAX_CACHED_VALIDATORS:
            _VALIDATORS[key] = cached
    return cached


def _schema_symbol_keys(schema: Any) -> frozenset[str]:
    """Property names flagged ``x-ceap-symbol`` anywhere in a JSON schema."""
    found: set[str] = set()
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                for name, sub in props.items():
                    if isinstance(sub, dict) and sub.get(SCHEMA_SYMBOL_FLAG) is True:
                        found.add(str(name))
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(node)
    return frozenset(found)
