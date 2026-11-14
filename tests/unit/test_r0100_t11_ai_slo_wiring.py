"""T11 (R-14, R-34): the AI SLO monitor is set in ``Platform`` and fed by the planner; each routed
model call is one GenAI client span under its agent span; ``ollama`` is a runtime provider."""

from __future__ import annotations

import itertools
from dataclasses import replace
from pathlib import Path

import pytest

from ceap.config import Settings
from ceap.data.scenarios import all_scenarios
from ceap.domain.common import ID_SOURCE
from ceap.harness.memory import CaseMemory
from ceap.llm.client import MockLLMClient
from ceap.llm.models import LLMRequest, LLMResponse
from ceap.llm.ollama import (
    PRIMARY_LOCAL_MODEL,
    LocalCassette,
    OllamaLLMClient,
    load_local_cassette,
    write_local_cassette,
)
from ceap.llm.router import LLMRouter, build_router, task_cost
from ceap.observability.ai_slo import PLANNER_PURPOSE
from ceap.platform import Platform, default_request

CASSETTE = (
    Path(__file__).resolve().parents[1] / "cassettes" / "local_model" / "qwen2.5_7b-instruct_eval_s1.json"
)


class _SeededMock(MockLLMClient):
    temperature = 0.2
    seed = 7


class _BadPlanner(MockLLMClient):
    """Answers every planning request with text that is not a plan."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        response = await super().complete(request)
        if request.purpose == "planning":
            return replace(response, content="no plan today", parsed=None, tool_calls=[])
        return response


def _platform(tmp_path, llm, **kw) -> Platform:
    settings = Settings(
        llm_provider=kw.pop("provider", "mock"), auto_approve=True, db_path=str(tmp_path / "c.db")
    )
    return Platform(settings=settings, llm=llm, case_memory=CaseMemory(enabled=False), **kw)


def _fixed_ids() -> None:
    """Deterministic ids, so the recorded run and the replayed run send identical requests."""
    counter = itertools.count()
    ID_SOURCE.set(lambda: f"{next(counter):08x}")


def _first_golden_request():
    spec = all_scenarios()[0]
    return default_request(spec.symbol, spec.id)


async def test_genai_span_per_routed_call_under_agent_span(tmp_path):
    p = _platform(tmp_path, LLMRouter(_SeededMock()))
    result = await p.investigate(_first_golden_request())
    spans = result.trace["spans"]
    by_id = {s["id"]: s for s in spans}
    chats = [s for s in spans if s["name"].startswith("gen_ai.chat:")]
    assert chats, "no GenAI client span recorded"
    assert len(chats) == p.llm.usage["calls"]  # exactly one span per routed call
    for s in chats:
        a = s["attributes"]
        assert a["gen_ai.request.model"] and a["gen_ai.response.model"] == "mock-deterministic-v1"
        assert isinstance(a["gen_ai.usage.input_tokens"], int) and isinstance(
            a["gen_ai.usage.output_tokens"], int
        )
        assert a["gen_ai.response.finish_reasons"]
        assert a["gen_ai.request.temperature"] == 0.2 and a["gen_ai.request.seed"] == 7
        assert s["parent_id"] in by_id  # a child, never a root span
    planning = [s for s in chats if s["attributes"]["ceap.ai.purpose"] == "planning"]
    assert planning and by_id[planning[0]["parent_id"]]["name"].startswith(("agent:", "harness.plan"))


async def test_degraded_plan_moves_the_planner_indicator(tmp_path):
    good = _platform(tmp_path / "g", LLMRouter(MockLLMClient()))
    await good.investigate(_first_golden_request())
    assert good.llm.slo is good.slo  # the platform set its monitor on the router
    ok = good.slo.snapshot()[PLANNER_PURPOSE]
    assert ok["plan_valid"]["value"] == 1.0 and ok["repair"]["value"] == 0.0

    bad = _platform(tmp_path / "b", LLMRouter(_BadPlanner()))
    await bad.investigate(_first_golden_request())
    row = bad.slo.snapshot()[PLANNER_PURPOSE]
    assert row["plan_valid"]["value"] == 0.0
    assert row["repair"]["value"] == 1.0 and row["fallback"]["value"] == 1.0


class _ReplayingOllama(OllamaLLMClient):
    """Records a cassette for the current code: each reply is the pinned qwen model's own recorded
    answer for that purpose (from the committed evaluation cassette), so no model runs here."""

    def __init__(self, replies: dict[str, dict], **kw) -> None:
        super().__init__(PRIMARY_LOCAL_MODEL, seed=1, temperature=0.7, verify=False, **kw)
        self.replies, self._purpose = replies, ""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self._purpose = request.purpose
        return await super().complete(request)

    async def _post(self, body: dict) -> dict:
        r = self.replies[self._purpose]
        return {
            "message": {"content": r["content"]},
            "prompt_eval_count": r["input_tokens"],
            "eval_count": r["output_tokens"],
            "done_reason": "stop",
        }


async def _record_cassette(tmp_path) -> Path:
    recorded = load_local_cassette(CASSETTE)
    replies: dict[str, dict] = {}
    for i in recorded.interactions:  # the last recording of each purpose (the golden-set cases)
        if not i.response.get("parse_errors"):
            replies[i.purpose] = i.response
    cas = LocalCassette(provenance=recorded.provenance.model_copy(update={"digest": ""}), interactions=[])
    p = _platform(tmp_path / "rec", LLMRouter(_ReplayingOllama(replies, recorder=cas)))
    _fixed_ids()
    await p.investigate(_first_golden_request())
    path = tmp_path / "s01_cassette.json"
    write_local_cassette(path, cas)
    return path


async def test_ollama_provider_runs_an_investigation_from_a_cassette(tmp_path, monkeypatch):
    monkeypatch.setenv("CEAP_OLLAMA_CASSETTE", str(await _record_cassette(tmp_path)))
    monkeypatch.delenv("CEAP_LOCAL_MODEL", raising=False)
    settings = Settings(llm_provider="ollama", auto_approve=True, db_path=str(tmp_path / "c.db"))
    router = build_router(settings)
    assert router.primary.name.startswith("cassette:qwen") and router.fallback is None
    p = Platform(settings=settings, llm=router, case_memory=CaseMemory(enabled=False))
    _fixed_ids()
    result = await p.investigate(_first_golden_request())
    assert router.primary.misses == 0  # every call of the investigation was answered by the cassette
    assert router.usage["calls"] > 0 and router.usage["fallbacks"] == 0
    cost = task_cost(result.task_id)
    assert cost.calls > 0 and cost.usd == 0.0 and cost.cpu_seconds > 0.0  # CPU-seconds, not dollars
    assert p.slo.p95_ms["planning"] == 1_800_000.0  # the local-model latency objective
    assert p.slo.snapshot()[PLANNER_PURPOSE]["plan_valid"]["n"] == 1


def test_ollama_provider_without_cassette_or_live_flag_is_refused(monkeypatch):
    from ceap.config import SettingsError

    monkeypatch.delenv("CEAP_OLLAMA_CASSETTE", raising=False)
    monkeypatch.delenv("CEAP_LOCAL_MODEL", raising=False)
    with pytest.raises(SettingsError, match="CEAP_OLLAMA_CASSETTE"):
        build_router(Settings(llm_provider="ollama"))
