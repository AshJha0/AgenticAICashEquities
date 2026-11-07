"""Strict structured-output schemas for the planner and the critic (PLAN 3A.1).

The model's plan is constrained on the wire by ``output_config.format`` (a JSON schema generated
from these Pydantic models, ``additionalProperties: false`` on every object) and validated again
here, in two stages:

1. **schema validation** - :func:`validate_plan` / :func:`validate_critique` run the Pydantic
   model; any failure is a list of human-readable errors (no regex extraction anywhere);
2. **semantic validation** - :func:`plan_semantic_errors` / :func:`critique_semantic_errors` check
   what a schema cannot say: the window and baseline the task fixed, the task symbol and universe,
   finding ids that exist, confidences in ``[0, 1]``, one governance tail, a bounded step count.

The errors of either stage are the repair target: the agent sends them back once (rung 2), and a
second failure is ``DEGRADED`` (PLAN 1.5). Plan steps are a discriminated union over the tool
catalogue: one ``TOOL_CALL`` model per read-only tool, generated from that tool's MCP
``inputSchema``, so a tool id outside the catalogue cannot even be expressed.

:func:`lint_schema` checks a generated schema against the structured-outputs subset (no
``oneOf``, no numeric or length constraints, ``additionalProperties: false`` on every object,
no recursion) so a catalogue change that the API would reject fails in CI, offline.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

# the harness caps a whole plan at 64 steps and appends up to 11 governance steps itself
# (required agents, critic, validation, approvals, staging, finalise): the model gets 48
MAX_PLAN_STEPS = 48
GOVERNANCE_TYPES = ("VALIDATION", "FINALISE")
WINDOW_KEYS = ("start", "end")
BASELINE_KEYS = ("baseline_start", "baseline_end")

_JSON_TO_PY: dict[str, type] = {"string": str, "integer": int, "number": float, "boolean": bool}
_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "oneOf",
        "not",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
        "if",
        "then",
        "else",
        "patternProperties",
        "dependentSchemas",
    }
)


class StrictModel(BaseModel):
    """Every structured-output object forbids extra keys (``additionalProperties: false``)."""

    model_config = ConfigDict(extra="forbid", strict=False)


# ---------------------------------------------------------------- critic


class CritiqueAssessment(StrictModel):
    finding_id: str
    supported: bool
    adjusted_confidence: float
    comment: str


class CritiqueOutput(StrictModel):
    assessments: list[CritiqueAssessment]
    overall: str


# ---------------------------------------------------------------- planner


class AgentStep(StrictModel):
    type: Literal["AGENT_CALL"]
    description: str
    agent_id: str  # narrowed to a Literal per task by :func:`plan_model`


class ValidationStep(StrictModel):
    type: Literal["VALIDATION"]
    description: str


class FinaliseStep(StrictModel):
    type: Literal["FINALISE"]
    description: str


def _model_name(tool_id: str) -> str:
    return "Tool_" + re.sub(r"[^0-9A-Za-z]", "_", tool_id)


def _field_type(prop: Mapping[str, Any]) -> Any:
    """Python type for one MCP property (scalars, or ``anyOf`` scalar | null)."""
    if "anyOf" in prop:
        members = [m for m in prop["anyOf"] if m.get("type") != "null"]
        base = _field_type(members[0]) if len(members) == 1 else str
        return base | None
    return _JSON_TO_PY.get(str(prop.get("type", "string")), str)


def arguments_model(tool_id: str, input_schema: Mapping[str, Any]) -> type[StrictModel]:
    """A strict Pydantic model for one tool's MCP ``inputSchema``."""
    props: Mapping[str, Any] = input_schema.get("properties") or {}
    required = set(input_schema.get("required") or [])
    fields: dict[str, Any] = {}
    for name in sorted(props):
        t = _field_type(props[name])
        if name in required:
            fields[name] = (t, ...)
        else:
            fields[name] = (t | None, None)
    return create_model(_model_name(tool_id) + "_Args", __base__=StrictModel, **fields)  # type: ignore[call-overload,no-any-return]


def tool_step_model(tool_id: str, input_schema: Mapping[str, Any]) -> type[StrictModel]:
    args = arguments_model(tool_id, input_schema)
    return create_model(  # type: ignore[call-overload,no-any-return]
        _model_name(tool_id),
        __base__=StrictModel,
        type=(Literal["TOOL_CALL"], ...),
        description=(str, ...),
        tool_id=(Literal[tool_id], ...),
        arguments=(args, ...),
    )


def plan_model(catalogue: Mapping[str, Mapping[str, Any]], agents: Sequence[str]) -> type[StrictModel]:
    """The ``Plan`` output model for one task: steps are a union over ``catalogue`` and ``agents``.

    ``catalogue`` maps read-only tool id -> MCP ``inputSchema``. The union is a plain ``anyOf``
    (the structured-outputs subset has no ``oneOf``); each member pins its ``type`` and
    ``tool_id`` with ``const`` so exactly one member can match a valid step.
    """
    tool_steps = [tool_step_model(tid, catalogue[tid]) for tid in sorted(catalogue)]
    agent_lit = Literal[tuple(agents)] if agents else str  # type: ignore[valid-type]
    agent_step = create_model("AgentStep", __base__=AgentStep, agent_id=(agent_lit, ...))
    members = (*tool_steps, agent_step, ValidationStep, FinaliseStep)
    step_union = Union[members]  # type: ignore[valid-type]  # noqa: UP007 - runtime tuple
    return create_model(  # type: ignore[call-overload,no-any-return]
        "PlanOutput",
        __base__=StrictModel,
        rationale=(str, ...),
        steps=(list[step_union], ...),  # type: ignore[valid-type]
    )


def output_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema sent as ``output_config.format.schema`` for ``model``."""
    return model.model_json_schema()


# ---------------------------------------------------------------- validation


def _errors(exc: ValidationError, limit: int = 12) -> list[str]:
    out = []
    for err in exc.errors()[:limit]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        out.append(f"{loc}: {err.get('msg')}")
    if len(exc.errors()) > limit:
        out.append(f"... {len(exc.errors()) - limit} further schema errors")
    return out


def parse_json_object(text: str | None) -> tuple[dict[str, Any] | None, list[str]]:
    """Strict ``json.loads`` of a whole reply; no fences, no regex, no partial recovery."""
    if not text:
        return None, ["empty reply"]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"reply is not one JSON document ({exc.msg} at {exc.pos})"]
    if not isinstance(value, dict):
        return None, ["reply is JSON but not an object"]
    return value, []


def validate_model(model: type[BaseModel], raw: Any) -> list[str]:
    try:
        model.model_validate(raw)
    except ValidationError as exc:
        return _errors(exc)
    return []


def plan_semantic_errors(
    raw: Mapping[str, Any],
    facts: Mapping[str, Any],
    catalogue: Iterable[str],
    agents: Sequence[str],
    *,
    max_steps: int = MAX_PLAN_STEPS,
) -> list[str]:
    """What the schema cannot say: windows, symbol, universe, agents, governance, size."""
    errors: list[str] = []
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["plan has no steps"]
    if len(steps) > max_steps:
        errors.append(f"plan has {len(steps)} steps; at most {max_steps} are allowed")
    tools = set(catalogue)
    research = str(facts.get("kind") or "") == "research"
    windows = allowed_windows(facts, research)
    symbol = facts.get("symbol")
    universe = {str(u) for u in facts.get("universe") or []}
    gov_counts = {t: 0 for t in GOVERNANCE_TYPES}
    for i, step in enumerate(steps[:max_steps]):
        if not isinstance(step, Mapping):
            errors.append(f"steps.{i}: not an object")
            continue
        stype = str(step.get("type", ""))
        if stype in gov_counts:
            gov_counts[stype] += 1
        if stype == "AGENT_CALL" and str(step.get("agent_id")) not in agents:
            errors.append(f"steps.{i}: unknown agent {step.get('agent_id')!r}; allowed {list(agents)}")
        if stype != "TOOL_CALL":
            continue
        tool_id = str(step.get("tool_id", ""))
        if tool_id not in tools:
            errors.append(f"steps.{i}: tool {tool_id!r} is not in the catalogue")
            continue
        args = step.get("arguments")
        if not isinstance(args, Mapping):
            continue
        start, end = args.get("start"), args.get("end")
        if windows and start is not None and end is not None and (str(start), str(end)) not in windows:
            errors.append(f"steps.{i}: window {start}..{end} is neither the task window nor its baseline")
        if symbol is not None and "symbol" in args and str(args["symbol"]) != str(symbol):
            errors.append(f"steps.{i}: symbol {args['symbol']!r} is not the task symbol {symbol!r}")
        if universe and "symbol" in args and str(args["symbol"]) not in universe:
            errors.append(f"steps.{i}: symbol {args['symbol']!r} is outside the task universe")
        for key in ("dataset", "signal"):
            pinned = facts.get(key)
            if pinned is not None and key in args and str(args[key]) != str(pinned):
                errors.append(f"steps.{i}: {key} {args[key]!r} is not the task {key} {pinned!r}")
    for stype, n in gov_counts.items():
        if n > 1:
            errors.append(f"plan has {n} {stype} steps; the governance tail allows one")
    return errors


def allowed_windows(facts: Mapping[str, Any], research: bool) -> set[tuple[str, str]]:
    pairs: list[tuple[Any, Any]]
    if research:
        pairs = [(facts.get("start"), facts.get("end"))]
    else:
        pairs = [
            (facts.get("window_start"), facts.get("window_end")),
            (facts.get("baseline_start"), facts.get("window_end")),  # deployments look-back
            (facts.get("baseline_start"), facts.get("baseline_end")),
        ]
    return {(str(a), str(b)) for a, b in pairs if a is not None and b is not None}


def critique_semantic_errors(raw: Mapping[str, Any], finding_ids: Iterable[str]) -> list[str]:
    known = set(finding_ids)
    errors: list[str] = []
    for i, a in enumerate(raw.get("assessments") or []):
        if not isinstance(a, Mapping):
            continue
        fid = a.get("finding_id")
        if fid not in known:
            errors.append(f"assessments.{i}: finding_id {fid!r} does not exist")
        conf = a.get("adjusted_confidence")
        if isinstance(conf, (int, float)) and not (0.0 <= float(conf) <= 1.0):
            errors.append(f"assessments.{i}: adjusted_confidence {conf} is outside [0, 1]")
    return errors


# ---------------------------------------------------------------- schema-subset lint


def lint_schema(schema: Mapping[str, Any]) -> list[str]:
    """Problems that would make the API reject ``schema`` as a strict structured-output schema."""
    problems: list[str] = []
    defs = schema.get("$defs") or {}
    _lint(schema, "#", problems)
    for name, sub in defs.items():
        _lint(sub, f"#/$defs/{name}", problems)
        if _refers_to(sub, f"#/$defs/{name}", defs, set()):
            problems.append(f"#/$defs/{name}: recursive schema")
    return problems


def _lint(node: Any, path: str, problems: list[str]) -> None:
    if isinstance(node, list):
        for i, item in enumerate(node):
            _lint(item, f"{path}/{i}", problems)
        return
    if not isinstance(node, Mapping):
        return
    for key in node:
        if key in _UNSUPPORTED_KEYWORDS:
            problems.append(f"{path}: unsupported keyword {key!r}")
    if node.get("type") == "object" or "properties" in node:
        if node.get("additionalProperties") is not False:
            problems.append(f"{path}: object without additionalProperties: false")
    for key, value in node.items():
        if key in ("$defs", "default", "const", "enum", "examples"):
            continue
        if key == "properties" and isinstance(value, Mapping):
            for pname, pnode in value.items():
                _lint(pnode, f"{path}/properties/{pname}", problems)
        elif isinstance(value, (Mapping, list)):
            _lint(value, f"{path}/{key}", problems)


def _refers_to(node: Any, target: str, defs: Mapping[str, Any], seen: set[str]) -> bool:
    if isinstance(node, list):
        return any(_refers_to(n, target, defs, seen) for n in node)
    if not isinstance(node, Mapping):
        return False
    ref = node.get("$ref")
    if isinstance(ref, str):
        if ref == target:
            return True
        name = ref.rsplit("/", 1)[-1]
        if name not in seen and name in defs:
            seen.add(name)
            if _refers_to(defs[name], target, defs, seen):
                return True
    return any(_refers_to(v, target, defs, seen) for k, v in node.items() if k != "$ref")


__all__ = [
    "AgentStep",
    "CritiqueAssessment",
    "CritiqueOutput",
    "FinaliseStep",
    "MAX_PLAN_STEPS",
    "StrictModel",
    "ValidationStep",
    "allowed_windows",
    "arguments_model",
    "critique_semantic_errors",
    "lint_schema",
    "output_schema",
    "parse_json_object",
    "plan_model",
    "plan_semantic_errors",
    "tool_step_model",
    "validate_model",
]
