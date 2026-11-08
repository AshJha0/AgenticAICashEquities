"""R3A item tests (3A.1, 3A.3, 3A.5, 3A.6, 3A.7). Offline: fake clients and in-memory transports only."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from ceap.agents.critic import CriticAgent
from ceap.agents.planner import PlannerAgent
from ceap.domain.agents import AgentContext
from ceap.domain.findings import Finding
from ceap.llm.client import MockLLMClient
from ceap.llm.models import LLMRequest
from ceap.llm.pins import load_pins
from ceap.llm.prompts import PROMPT_IDS, canonical_bytes, get_prompt, parse_prompt, registry
from ceap.llm.router import LLMBudgetExceededError, LLMRouter, task_scope
from ceap.llm.schemas import (
    CritiqueOutput,
    lint_schema,
    parse_json_object,
    plan_model,
)
from ceap.platform import default_request
from fakes.clients import (
    ALL_CLIENT_FACTORIES,
    HostilePlanClient,
    IdFabricatorClient,
    RaisingClient,
    SchemaFuzzClient,
    every_sdk_error,
)

ROOT = Path(__file__).resolve().parents[2]
INVESTIGATION_AGENTS = ["market", "execution", "quant", "risk", "engineering", "critic"]
RESEARCH_AGENTS = ["research", "alpha", "backtest", "portfolio_risk", "critic"]


async def _planner_ctx(platform, task_id_suffix: str = ""):
    req = default_request("AAPL", "T01")
    task = platform.build_task(req)
    base = await platform.harness()
    ctx = AgentContext(
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
    return ctx, base


def _codes(output) -> list[str]:
    return [d["code"] for d in output.get("degradations", [])]


# ---------------------------------------------------------------- 3A.1 structured outputs


async def test_3a1_schema_subset_lint_passes_for_every_schema(platform):
    _, base = await _planner_ctx(platform)
    catalogue = {m.id: m.input_schema for m in base.tools.list() if m.read_only}
    for agents in (INVESTIGATION_AGENTS, RESEARCH_AGENTS):
        assert lint_schema(plan_model(catalogue, agents).model_json_schema()) == []
    assert lint_schema(CritiqueOutput.model_json_schema()) == []
    # the lint is not vacuous
    bad = {"type": "object", "properties": {"x": {"type": "integer", "minimum": 0}, "y": {"oneOf": []}}}
    problems = " ".join(lint_schema(bad))
    assert "additionalProperties" in problems and "minimum" in problems and "oneOf" in problems


async def test_3a1_semantic_invalid_plan_triggers_exactly_one_repair_then_degraded(platform):
    ctx, _ = await _planner_ctx(platform)
    client = HostilePlanClient("universe")  # schema-valid, semantically wrong: symbol ZZZZ
    result = await PlannerAgent(client).execute(ctx)
    assert client.calls_for("planning") == 2  # the call and exactly one repair
    repair_msg = client.calls[1].messages[-1]["content"]
    assert "ZZZZ" in repair_msg and "task symbol" in repair_msg  # semantic errors are the repair target
    codes = _codes(result.output)
    assert codes.count("LLM_REPAIR_RETRY") == 1 and "LLM_PLAN_INVALID_AFTER_REPAIR" in codes
    assert result.output["repairs"] == 1
    # the sanitiser still pins every argument to the task
    assert all(
        s.tool_request.arguments.get("symbol", "AAPL") == "AAPL"
        for s in result.output["plan"].steps
        if s.tool_request
    )


async def test_3a1_valid_structured_plan_needs_no_repair(platform):
    ctx, _ = await _planner_ctx(platform)
    client = MockLLMClient()
    result = await PlannerAgent(client).execute(ctx)
    assert result.output["repairs"] == 0 and not result.output["degradations"]
    assert client.calls[0].output_model is not None  # the schema travelled with the request


async def test_3a1_critic_semantic_invalid_triggers_one_repair_then_deterministic(platform):
    ctx, _ = await _planner_ctx(platform)
    client = IdFabricatorClient()
    finding = Finding("FIND-1", "s", ("EV-1",), (), 0.6, produced_by="quant")
    _, _, degradations = await CriticAgent(client)._llm_critique(ctx, [finding], _Memory(), [])
    assert client.calls_for("critique") == 2
    assert [d.code for d in degradations] == ["LLM_REPAIR_RETRY", "LLM_CRITIQUE_UNUSABLE"]


class _Memory:
    evidence: dict = {}

    def unresolved_evidence(self, f):
        return []


def test_3a1_no_regex_json_path_remains():
    src = (ROOT / "src" / "ceap").rglob("*.py")
    offenders = [p for p in src if "extract_json" in p.read_text(encoding="utf-8")]
    assert offenders == []
    assert not re.search(
        r"re\.(search|match|findall)\(.*\{", (ROOT / "src/ceap/llm/client.py").read_text("utf-8")
    )
    # strict parsing: a fenced or prefixed reply is not silently recovered
    assert parse_json_object('```json\n{"a": 1}\n```')[0] is None
    assert parse_json_object('prefix {"a": 1}')[0] is None
    assert parse_json_object('{"a": 1}') == ({"a": 1}, [])


# ---------------------------------------------------------------- 3A.3 adversarial clients


def test_3a3_nine_adversarial_clients_exist_and_are_used():
    names = {type(f()).__name__ for f in ALL_CLIENT_FACTORIES.values()}
    assert names == {
        "SchemaFuzzClient",
        "TruncatingClient",
        "RefusalClient",
        "HangingClient",
        "RaisingClient",
        "ObedientInjectionClient",
        "NumberFabricatorClient",
        "IdFabricatorClient",
        "HostilePlanClient",
    }


@pytest.mark.parametrize("exc", list(every_sdk_error()), ids=lambda e: type(e).__name__)
async def test_3a3_every_sdk_error_degrades_the_planner(platform, exc):
    ctx, _ = await _planner_ctx(platform)
    result = await PlannerAgent(RaisingClient(exc)).execute(ctx)
    assert result.success and result.output["plan"].generated_by == "canonical-fallback"
    assert "LLM_CALL_FAILED" in _codes(result.output)


async def test_3a3_schema_fuzz_never_yields_an_unvalidated_plan(platform):
    ctx, base = await _planner_ctx(platform)
    tools = {m.id for m in base.tools.list() if m.read_only}
    for seed in range(15):
        result = await PlannerAgent(SchemaFuzzClient(seed)).execute(ctx)
        steps = result.output["plan"].steps
        assert all(s.tool_request.tool_id in tools for s in steps if s.tool_request)
        assert result.output["repairs"] <= 1


# ---------------------------------------------------------------- 3A.6 cost and budget


async def test_3a6_two_concurrent_tasks_report_separate_costs():
    router = LLMRouter(MockLLMClient(model="claude-sonnet-5-5"), pins=load_pins())

    async def task(task_id: str, n: int) -> None:
        with task_scope(task_id):
            for _ in range(n):
                await router.complete(
                    LLMRequest("s", [{"role": "user", "content": "x" * 4000}], purpose="narrative")
                )
                await asyncio.sleep(0)

    await asyncio.gather(task("TASK-A", 1), task("TASK-B", 3))
    a, b = router.cost_for("TASK-A"), router.cost_for("TASK-B")
    assert (a.calls, b.calls) == (1, 3)
    assert 0 < a.usd < b.usd and abs(b.usd - 3 * a.usd) < 1e-12
    assert all(r.task_id == "TASK-B" and r.usd_is_estimate for r in b.records)


async def test_3a6_tiny_budget_aborts_before_the_call_and_degrades(platform):
    primary = MockLLMClient(model="claude-opus-5-5")
    router = LLMRouter(primary, pins=load_pins(), task_budget_usd=0.01)
    with pytest.raises(LLMBudgetExceededError, match="local estimate"):
        await router.complete(LLMRequest("s", [{"role": "user", "content": "x"}], purpose="planning"))
    assert len(primary.calls) == 0  # aborted before any call
    ctx, _ = await _planner_ctx(platform)
    result = await PlannerAgent(router).execute(ctx)
    assert len(primary.calls) == 0 and "LLM_CALL_FAILED" in _codes(result.output)  # DEGRADED, canonical plan


async def test_3a6_call_record_has_redacted_body_and_prompt_provenance():
    router = LLMRouter(MockLLMClient(model="claude-sonnet-5-5"), pins=load_pins())
    prompt = get_prompt("report")
    await router.complete(
        LLMRequest(
            prompt.text,
            [{"role": "user", "content": "mail ops@example.com"}],
            purpose="narrative",
            metadata={"task_id": "T-1", "prompt": prompt.ref()},
        )
    )
    rec = router.cost_for("T-1").records[0]
    assert "ops@example.com" not in rec.redacted_request and "[REDACTED:EMAIL]" in rec.redacted_request
    assert (rec.prompt_id, rec.prompt_version, rec.prompt_sha) == ("report", prompt.version, prompt.sha256)


# ---------------------------------------------------------------- 3A.7 prompt registry

HASHES = ROOT / "tests" / "cassettes" / "prompt_hashes.json"


def test_3a7_prompt_hash_snapshot_and_changelog():
    """Changing a prompt changes its hash: CI fails until the snapshot and CHANGELOG are updated."""
    current = {pid: {"version": p.version, "sha256": p.sha256} for pid, p in registry().items()}
    assert json.loads(HASHES.read_text(encoding="utf-8")) == current, (
        "a prompt changed: bump its version header, update tests/cassettes/prompt_hashes.json "
        "and add '<id>@<version>' to CHANGELOG.md"
    )
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    missing = [
        f"{pid}@{p['version']}" for pid, p in current.items() if f"{pid}@{p['version']}" not in changelog
    ]
    assert missing == []


def test_3a7_canonical_bytes_and_any_edit_changes_the_hash():
    p = get_prompt("planner")
    raw = f"<!-- prompt: planner | version: {p.version} -->\n" + p.text
    assert (
        parse_prompt(raw.replace("\n", "\r\n") + "  \n").sha256 == p.sha256
    )  # CRLF / trailing space invariant
    assert parse_prompt(raw + "One more rule.\n").sha256 != p.sha256
    assert canonical_bytes("a \r\nb") == b"a\nb\n"
    assert set(PROMPT_IDS) == {f.stem for f in (ROOT / "src/ceap/llm/prompts").glob("*.md")}


async def test_3a7_prompt_version_and_hash_travel_with_the_plan(platform):
    ctx, _ = await _planner_ctx(platform)
    client = MockLLMClient()
    result = await PlannerAgent(client).execute(ctx)
    ref = get_prompt("planner").ref()
    assert result.output["prompt"] == ref and client.calls[0].metadata["prompt"] == ref
