"""Planner agent: natural-language task -> typed, validated Plan.

The LLM proposes the plan as JSON; this agent converts it into typed
``PlanStep`` objects and *sanitises* it:

* unknown tools, agents and step types are dropped and recorded;
* arguments the schema does not declare are dropped and recorded;
* required arguments must be present;
* arguments that scope data access (``symbol``, ``dataset``) are pinned to
  the task - the model cannot point the investigation at other data;
* the reply is a strict structured output (``llm/schemas.py``): schema errors and semantic errors
  (unknown agent, a window that is neither the task window nor its baseline, a symbol outside the
  task or its universe, a duplicated governance step, too many steps) are sent back once as the
  repair target (rung 2); a second failure is ``DEGRADED`` and the plan is sanitised as below;
* unusable model output falls back to the canonical plan.

The harness validates the result again (including a policy pre-check of
every tool call and hard caps on plan size) before executing anything.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from typing import Any

from ceap.agents.base import BaseAgent, degradation_records, llm_disabled
from ceap.analytics.hypotheses import (
    BASELINE_CHOICES,
    DEFAULT_BASELINE,
    HYPOTHESES,
    baseline_window,
    rank_proposal,
)
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.common import new_id
from ceap.domain.plans import Plan, PlanStep, StepType
from ceap.domain.tools import ToolRequest
from ceap.harness.cancellation import TaskCancelled
from ceap.llm.client import LLMClient, canonical_plan
from ceap.llm.models import (
    RUNG_DETERMINISTIC,
    RUNG_KILL_SWITCH,
    RUNG_REPAIR,
    Degradation,
    LLMRequest,
    LLMResponse,
)
from ceap.llm.prompts import DATA_BLOCK_NOTE, data_block, get_prompt
from ceap.llm.schemas import (
    MAX_PLAN_STEPS,
    allowed_windows,
    parse_json_object,
    plan_model,
    plan_semantic_errors,
    validate_model,
)
from ceap.rag.query import allowed_classifications, retrieval_query

log = logging.getLogger(__name__)

INVESTIGATION_AGENTS = ("market", "execution", "quant", "risk", "engineering", "critic")
RESEARCH_AGENTS = ("research", "alpha", "backtest", "portfolio_risk", "critic")
ALLOWED_AGENTS = INVESTIGATION_AGENTS + tuple(a for a in RESEARCH_AGENTS if a not in INVESTIGATION_AGENTS)
RESEARCH_KIND = "research"
MAX_STEPS_FROM_MODEL = MAX_PLAN_STEPS  # leaves room for the governance steps the harness appends
PINNED_ARGUMENTS = ("symbol", "dataset", "signal")
_RESEARCH_FACT_KEYS = (
    "dataset",
    "signal",
    "start",
    "end",
    "in_sample_end",
    "rebalance_days",
    "long_short",
    "gross_notional",
    "universe",
)

REPAIR_INSTRUCTION = (
    "Your previous plan cannot be used. Problems found:\n{problems}\n"
    "Reply again with a corrected plan that uses only the tool catalogue, the agents and the PARAMETERS given above."
)
MAX_REPAIR_PROBLEMS = 20


def _usable(raw: dict[str, Any] | None) -> bool:
    return raw is not None and isinstance(raw.get("steps"), list) and bool(raw["steps"])


def _reply_object(response: LLMResponse) -> tuple[dict[str, Any] | None, list[str]]:
    """The structured reply: the adapter's parsed output, else a strict parse of the whole text."""
    if response.parsed is not None:
        return response.parsed, []
    if response.parse_errors:
        return None, list(response.parse_errors)
    if response.stop_reason in ("refusal", "max_tokens"):
        return None, [f"reply ended with stop_reason={response.stop_reason}"]
    return parse_json_object(response.content)


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
}


class PlannerAgent(BaseAgent):
    agent_id = "planner"
    agent_type = "planner"

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def execute(self, context: AgentContext) -> AgentResult:
        task = context.state["task"]
        kind = str(context.task_input.get("kind") or "investigation")
        research = kind == RESEARCH_KIND
        # the model only ever sees read-only tools: mutating tools are scheduled by the harness alone
        read_only_tools = [m for m in context.state.get("tool_catalogue", []) if m.read_only]
        catalogue = [
            {
                "tool_id": m.id,
                "description": m.description,
                "read_only": m.read_only,
                "input_schema": m.input_schema,
            }
            for m in read_only_tools
        ]
        facts = _facts(context.task_input, kind)
        agents = RESEARCH_AGENTS if research else INVESTIGATION_AGENTS
        # R3B 3B.7: up to three prior human-decided cases, injected by the harness, read-only
        cases = [c for c in (context.state.get("cases") or ()) if isinstance(c, dict)][:3]
        user_message = _user_message(research, task.description, facts, catalogue, agents, cases)
        prompt = get_prompt("research_planner" if research else "planner")
        tool_schemas = {m.id: (m.input_schema or {}) for m in read_only_tools}
        output_model = plan_model(
            tool_schemas,
            agents,
            baselines=() if research else tuple(facts.get("baseline_windows") or ()),
            hypotheses=() if research else HYPOTHESES,
        )
        request = LLMRequest(
            system_prompt=prompt.text,
            messages=[{"role": "user", "content": user_message}],
            purpose="planning",  # max_tokens and effort come from model_pins.toml (16000 with thinking)
            # structured parameters travel out-of-band from the prose
            metadata={"parameters": facts, "task_id": context.task_id, "prompt": prompt.ref()},
            output_model=output_model,
        )

        def problems(response: LLMResponse) -> tuple[dict[str, Any] | None, list[str]]:
            raw, errors = _reply_object(response)
            if raw is None:
                return None, errors
            errors = validate_model(output_model, raw) + plan_semantic_errors(
                raw, facts, tool_schemas, agents
            )
            return raw, errors

        degradations: list[Degradation] = []
        raw: dict[str, Any] | None = None
        response: LLMResponse | None = None
        repairs = 0
        if llm_disabled(self.llm):
            degradations.append(
                Degradation(RUNG_KILL_SWITCH, "LLM_DISABLED", "kill switch on: canonical plan used")
            )
        else:
            try:
                response = await self.call_llm(context, self.llm, request)
                degradations.extend(response.degradations)
                raw, errors = problems(response)
                if errors:
                    # rung 2: exactly one repair, with the schema and semantic errors as its target
                    repairs = 1
                    listed = "\n".join(f"- {e}" for e in errors[:MAX_REPAIR_PROBLEMS])
                    degradations.append(
                        Degradation(
                            RUNG_REPAIR,
                            "LLM_REPAIR_RETRY",
                            f"planner output invalid ({len(errors)} problem(s), first: {errors[0][:160]}); one repair",
                        )
                    )
                    repair = LLMRequest(
                        **{
                            **request.__dict__,
                            "messages": [
                                *request.messages,
                                {
                                    "role": "assistant",
                                    "content": response.content[:4000] or "(no usable reply)",
                                },
                                {"role": "user", "content": REPAIR_INSTRUCTION.format(problems=listed)},
                            ],
                        }
                    )
                    response = await self.call_llm(context, self.llm, repair)
                    degradations.extend(response.degradations)
                    raw, errors = problems(response)
                    if errors:
                        degradations.append(
                            Degradation(
                                RUNG_REPAIR,
                                "LLM_PLAN_INVALID_AFTER_REPAIR",
                                f"repaired plan still invalid ({len(errors)} problem(s)); sanitised before use",
                            )
                        )
            except (TaskCancelled, asyncio.CancelledError):
                raise
            except Exception as exc:  # noqa: BLE001 - rung 3 below records the failure
                log.warning("planner: model call failed (%s); using canonical plan", exc)
                degradations.append(
                    Degradation(
                        RUNG_DETERMINISTIC, "LLM_CALL_FAILED", f"{type(exc).__name__}: {str(exc)[:200]}"
                    )
                )
        source = f"llm:{response.model}" if response is not None else "canonical-fallback"
        if not _usable(raw):
            if not any(d.rung >= RUNG_DETERMINISTIC for d in degradations):
                degradations.append(
                    Degradation(
                        RUNG_DETERMINISTIC, "LLM_CANONICAL_PLAN", "unusable model output: canonical plan used"
                    )
                )
            log.warning("planner: unusable model output; using canonical plan")
            raw = canonical_plan(facts)
            source = "canonical-fallback"
        assert raw is not None

        schemas = tool_schemas
        steps, rejected = self._to_steps(raw["steps"][:MAX_STEPS_FROM_MODEL], schemas, facts, agents)
        if len(raw["steps"]) > MAX_STEPS_FROM_MODEL:
            rejected.append(
                {
                    "step": f"{len(raw['steps']) - MAX_STEPS_FROM_MODEL} further steps",
                    "reason": f"plan truncated to {MAX_STEPS_FROM_MODEL} steps",
                }
            )
        if not any(s.type is StepType.TOOL_CALL for s in steps):
            raw = canonical_plan(facts)
            steps, rejected2 = self._to_steps(raw["steps"], schemas, facts, agents)
            rejected.extend(rejected2)
            source = "canonical-fallback"
            degradations.append(
                Degradation(
                    RUNG_DETERMINISTIC,
                    "LLM_CANONICAL_PLAN",
                    "model plan had no valid tool call: canonical plan used",
                )
            )
        # R3C 3C.3: the knowledge query and classifications are built here, never taken from the model
        proposed = raw.get("hypotheses")
        hypotheses, _ = rank_proposal(proposed if isinstance(proposed, list) else [])
        knowledge_query = retrieval_query(task.description, facts, hypotheses)
        readable = ",".join(allowed_classifications(context.policy_context.roles))
        steps = _ground_knowledge_steps(steps, knowledge_query, readable)
        plan = Plan(
            id=new_id("PLAN"),
            task_id=task.id,
            steps=tuple(steps),
            rationale=str(raw.get("rationale", ""))[:2000],
            generated_by=source,
        )
        scope = _scope(raw, facts, steps, cases, research)
        output: dict[str, Any] = {
            "plan": plan,
            "rejected_steps": rejected,
            "llm_model": response.model if response else None,
            "tokens": {"input": response.input_tokens, "output": response.output_tokens} if response else {},
            "degradations": degradation_records(degradations),
            "repairs": repairs,
            "prompt": prompt.ref(),
            "scope": scope,
        }
        return AgentResult(
            agent_id=self.id,
            success=True,
            output=output,
            summary=f"{len(steps)} steps planned ({source}); {len(rejected)} rejected",
        )

    @staticmethod
    def _to_steps(
        raw_steps: list[Any],
        schemas: dict[str, dict[str, Any]],
        facts: dict[str, Any],
        allowed_agents: tuple[str, ...] = ALLOWED_AGENTS,
    ) -> tuple[list[PlanStep], list[dict[str, Any]]]:
        steps: list[PlanStep] = []
        rejected: list[dict[str, Any]] = []
        seq = 0
        for item in raw_steps:
            if not isinstance(item, dict):
                rejected.append({"step": _preview(item), "reason": "not an object"})
                continue
            try:
                stype = StepType(str(item.get("type", "")).upper())
            except ValueError:
                rejected.append({"step": _preview(item), "reason": "unknown step type"})
                continue
            description = str(item.get("description", ""))[:500]
            tool_request = None
            agent_id = None
            if stype is StepType.TOOL_CALL:
                tool_id = str(item.get("tool_id", ""))
                if tool_id not in schemas:
                    rejected.append({"step": _preview(item), "reason": f"unknown tool {tool_id}"})
                    continue
                args = item.get("arguments") or {}
                if not isinstance(args, dict):
                    rejected.append({"step": _preview(item), "reason": "arguments must be an object"})
                    continue
                args, problems = _sanitise_arguments(args, schemas[tool_id], facts)
                if problems:
                    rejected.append(
                        {
                            "step": _preview(item),
                            "reason": "; ".join(problems),
                            "kept": bool(args is not None),
                        }
                    )
                if args is None:
                    continue
                tool_request = ToolRequest(tool_id=tool_id, arguments=args)
            elif stype is StepType.AGENT_CALL:
                agent_id = str(item.get("agent_id", ""))
                if agent_id not in allowed_agents:
                    rejected.append({"step": _preview(item), "reason": f"unknown agent {agent_id}"})
                    continue
            seq += 1
            steps.append(
                PlanStep(
                    id=f"step-{seq:02d}",
                    sequence=seq,
                    type=stype,
                    description=description,
                    tool_request=tool_request,
                    agent_id=agent_id,
                )
            )
        return steps, rejected


def _sanitise_arguments(
    args: dict[str, Any], schema: dict[str, Any], facts: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[str]]:
    """Return (arguments, problems). ``None`` arguments means the step must be dropped."""
    props: dict[str, Any] = schema.get("properties", {}) or {}
    required: list[str] = list(schema.get("required", []) or [])
    problems: list[str] = []
    clean: dict[str, Any] = {}
    for key, value in args.items():
        if props and key not in props:
            problems.append(f"argument {key} not in schema (dropped)")
            continue
        if value is None:
            continue
        if isinstance(value, (dict, list, tuple)):
            problems.append(f"argument {key} must be a scalar (dropped)")
            continue
        expected = _JSON_TYPES.get(str(props.get(key, {}).get("type", "")))
        if expected and not isinstance(value, expected) or (expected == (int,) and isinstance(value, bool)):
            problems.append(f"argument {key} has wrong type (dropped)")
            continue
        clean[key] = value
    # pin data-scoping arguments to the task: the model may not point at other data
    for key in PINNED_ARGUMENTS:
        pinned = facts.get(key)
        if key in props and pinned is not None and not isinstance(pinned, (list, tuple)):
            if key in clean and str(clean[key]) != str(pinned):
                problems.append(f"argument {key}={clean[key]!r} re-pinned to task value {pinned!r}")
            clean[key] = pinned
    windows = allowed_windows(facts, str(facts.get("kind") or "") == RESEARCH_KIND)
    if (
        windows
        and "start" in clean
        and "end" in clean
        and (str(clean["start"]), str(clean["end"])) not in windows
    ):
        problems.append(f"window {clean['start']}..{clean['end']} is not a task window (step dropped)")
        return None, problems
    universe = facts.get("universe")
    if isinstance(universe, (list, tuple)) and "symbol" in clean:
        if str(clean["symbol"]) not in {str(u) for u in universe}:
            problems.append(f"symbol {clean['symbol']!r} is outside the task universe (step dropped)")
            return None, problems
    missing = [r for r in required if r not in clean]
    if missing:
        problems.append(f"missing required arguments {missing} (step dropped)")
        return None, problems
    return clean, problems


def _user_message(
    research: bool,
    description: Any,
    facts: dict[str, Any],
    catalogue: list[dict[str, Any]],
    agents: tuple[str, ...],
    cases: list[dict[str, Any]],
) -> str:
    """Every untrusted surface (the question, tool descriptions, prior cases) travels in a data block."""
    parts = [
        f"Produce {'a research' if research else 'an investigation'} plan for the following request.",
        DATA_BLOCK_NOTE,
        "REQUEST:\n" + data_block("question", "request", str(description)),
        f"PARAMETERS: {json.dumps(facts)}",
        "TOOL CATALOGUE:\n" + data_block("tool_descriptions", "catalogue", catalogue),
        f"AVAILABLE AGENTS: {list(agents)}",
    ]
    if not research:
        parts.append(f"BASELINE CHOICES: {list(facts.get('baseline_windows') or ())}")
        parts.append(f"PRE-REGISTERED HYPOTHESES: {list(HYPOTHESES)}")
    if cases:
        parts.append(
            "PRIOR CASES (read-only context from human-decided cases; cite a case id only if it is relevant):\n"
            + "\n".join(
                data_block("case_memory", c.get("case_id", f"case-{i}"), c) for i, c in enumerate(cases)
            )
        )
    return "\n\n".join(parts)


def _scope(
    raw: dict[str, Any],
    facts: dict[str, Any],
    steps: list[PlanStep],
    cases: list[dict[str, Any]],
    research: bool,
) -> dict[str, Any]:
    """R3B 3B.1 / 3B.2: what the plan chose, within enumerated choices. Invalid choices are rejected."""
    chosen_agents = sorted({s.agent_id for s in steps if s.type is StepType.AGENT_CALL and s.agent_id})
    optional_tools = sorted({s.tool_request.tool_id for s in steps if s.tool_request is not None})
    case_ids = [str(c.get("case_id")) for c in cases if c.get("case_id")]
    if research:
        return {"agents": chosen_agents, "tools": optional_tools, "context_cases": case_ids}
    rejected: list[str] = []
    baseline = raw.get("baseline")
    windows = facts.get("baseline_windows") or {}
    if baseline is None:
        baseline = DEFAULT_BASELINE
    elif str(baseline) not in windows:
        rejected.append(
            f"baseline {str(baseline)[:80]!r} is not one of {sorted(windows)}; {DEFAULT_BASELINE} used"
        )
        baseline = DEFAULT_BASELINE
    proposed = raw.get("hypotheses")
    ranked, unknown = rank_proposal(proposed if isinstance(proposed, list) else [])
    rejected.extend(f"hypothesis {u!r} is not pre-registered" for u in unknown)
    return {
        "agents": chosen_agents,
        "tools": optional_tools,
        "baseline": str(baseline),
        "baseline_window": list(windows.get(str(baseline)) or []),
        "hypotheses": ranked,
        "rejected": rejected,
        "context_cases": case_ids,
    }


def _baseline_windows(task_input: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for choice in BASELINE_CHOICES:
        try:
            out[choice] = list(baseline_window(choice, task_input))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _facts(task_input: dict[str, Any], kind: str) -> dict[str, Any]:
    """The structured parameters the planner may use; they travel out-of-band from the prose."""
    if kind == RESEARCH_KIND:
        facts: dict[str, Any] = {"kind": kind}
        for key in _RESEARCH_FACT_KEYS:
            value = task_input.get(key)
            facts[key] = list(value) if isinstance(value, (list, tuple)) else value
        facts["universe_size"] = len(facts.get("universe") or [])
        return facts
    return {
        "kind": kind,
        "symbol": task_input.get("symbol"),
        "window_start": task_input.get("window_start"),
        "window_end": task_input.get("window_end"),
        "baseline_start": task_input.get("baseline_start"),
        "baseline_end": task_input.get("baseline_end"),
        "dataset": task_input.get("dataset"),
        "baseline_windows": _baseline_windows(task_input),
    }


KNOWLEDGE_SEARCH = "knowledge.search_documents"


def _ground_knowledge_steps(steps: list[PlanStep], query: str, readable: str) -> list[PlanStep]:
    """Replace the model's knowledge query with the deterministic one and pin the readable classes.

    ``k`` is kept if the model chose one (bounded by the tool's schema already); the description
    is rewritten so the audit trail shows the query that actually ran.
    """
    out: list[PlanStep] = []
    for step in steps:
        req = step.tool_request
        if req is not None and req.tool_id == KNOWLEDGE_SEARCH:
            args = {"query": query, "k": req.arguments.get("k", 4), "classifications": readable}
            step = replace(
                step,
                tool_request=ToolRequest(KNOWLEDGE_SEARCH, args, req.correlation_id),
                description="Retrieve runbook and policy knowledge for this question (query built by the harness)",
            )
        out.append(step)
    return out


def _preview(item: Any) -> Any:
    text = json.dumps(item, default=str) if not isinstance(item, str) else item
    return text[:300]
