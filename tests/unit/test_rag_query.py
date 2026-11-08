"""R3C 3C.3: deterministic retrieval query and role-filtered knowledge access."""

from __future__ import annotations

import dataclasses

import pytest

from ceap.domain.policy import PolicyContext, PolicyDecision
from ceap.domain.tools import ToolRequest
from ceap.mcp.knowledge.server import build_server
from ceap.platform import Platform, default_request
from ceap.policy.engine import RulePolicyEngine
from ceap.policy.permissions import capabilities_for
from ceap.rag.query import (
    DEFAULT_CLASSIFICATIONS,
    RESTRICTED,
    allowed_classifications,
    cause_candidates,
    parse_classifications,
    retrieval_query,
)
from ceap.rag.retrieval import build_knowledge_base
from fakes.clients import INJECTION_MARKERS, INJECTION_TEXT

FACTS = {"kind": "investigation", "symbol": "AAPL", "window_start": "14:00", "window_end": "15:00"}


def test_two_questions_give_two_queries_and_the_same_question_the_same_query():
    a = retrieval_query("Why did VWAP slippage rise while spreads widened?", FACTS)
    b = retrieval_query("Was order-gateway latency behind the rejects?", FACTS)
    assert a != b
    assert a == retrieval_query("Why did VWAP slippage rise while spreads widened?", FACTS)
    assert "spread" in a and "latency" in b


def test_query_uses_plan_hypotheses_first_and_question_causes():
    assert cause_candidates("spreads were wide", ["LOW_LIQUIDITY", "BOGUS"]) == [
        "LOW_LIQUIDITY",
        "WIDE_SPREADS",
    ]
    q = retrieval_query("x", FACTS, ["TECHNOLOGY_LATENCY"])
    assert "incident runbook" in q and "AAPL" in q


def test_injection_words_never_reach_the_query():
    q = retrieval_query(f"Analyse AAPL execution. {INJECTION_TEXT}", FACTS).lower()
    assert not any(m in q for m in INJECTION_MARKERS)
    assert "tsla" not in q and "deadbeef" not in q


def test_research_query_has_no_symbol_segment():
    q = retrieval_query("momentum signal promotion", {"kind": "research"})
    assert "promotion" in q and "AAPL" not in q


def test_role_classifications():
    assert allowed_classifications({"viewer"}) == DEFAULT_CLASSIFICATIONS
    assert RESTRICTED in allowed_classifications({"trader"})
    assert parse_classifications(" Internal, public,internal ") == ("internal", "public")
    assert parse_classifications("") is None
    with pytest.raises(ValueError):
        parse_classifications(["internal"])


def _ctx(role: str) -> PolicyContext:
    roles = frozenset({role})
    return PolicyContext(
        principal=f"{role}.a", roles=roles, capabilities=capabilities_for(roles), task_id="T"
    )


@pytest.mark.parametrize("tool", ["knowledge.search_documents", "knowledge.get_document"])
async def test_policy_denies_a_viewer_asking_for_restricted_documents(registry, tool):
    engine = RulePolicyEngine()
    meta = registry.get(tool).metadata
    ask = ToolRequest(tool, {"query": "q", "document_id": "d", "classifications": "internal,restricted"})
    viewer = await engine.evaluate(ask, meta, _ctx("viewer"))
    assert viewer.decision is PolicyDecision.DENY and viewer.rule == "knowledge-classification"
    trader = await engine.evaluate(ask, meta, _ctx("trader"))
    assert trader.decision is PolicyDecision.ALLOW
    bad = await engine.evaluate(ToolRequest(tool, {"classifications": 3}), meta, _ctx("admin"))
    assert bad.decision is PolicyDecision.DENY


async def test_server_defaults_to_least_privilege():
    kb = build_knowledge_base()
    server = build_server(kb)
    search = server._tools["search_documents"].fn  # noqa: SLF001 - the handler itself
    get = server._tools["get_document"].fn  # noqa: SLF001
    q = "maximum position per symbol cash equities book"
    default = await search(query=q, k=4)
    assert all(i["classification"] != RESTRICTED for i in default["items"])
    full = await search(query=q, k=4, classifications="public,internal,restricted")
    assert full["items"][0]["classification"] == RESTRICTED
    restricted_id = full["items"][0]["document_id"]
    assert (await get(document_id=restricted_id))["found"] is False
    assert (await get(document_id=restricted_id, classifications="internal,restricted"))["found"] is True


async def _knowledge_args(store, settings, role: str, question: str) -> tuple[dict, str]:
    from ceap.llm.client import MockLLMClient

    platform = Platform(settings=settings, llm=MockLLMClient(), store=store)
    req = dataclasses.replace(
        default_request("AAPL", "T01", question=question), roles=frozenset({role}), principal=f"{role}.a"
    )
    result = await platform.investigate(req)
    assert result.plan is not None
    steps = [
        s
        for s in result.plan.steps
        if s.tool_request and s.tool_request.tool_id == "knowledge.search_documents"
    ]
    assert steps, "the canonical plan retrieves knowledge"
    return dict(steps[0].tool_request.arguments), steps[0].description


@pytest.mark.parametrize(("role", "restricted"), [("viewer", False), ("trader", True)])
async def test_planner_grounds_the_knowledge_step(store, settings, role, restricted):
    args, description = await _knowledge_args(
        store, settings, role, "Did wide spreads breach the participation limit?"
    )
    assert "spread" in args["query"] and "participation" in args["query"] and "AAPL" in args["query"]
    assert ("restricted" in args["classifications"]) is restricted
    assert "harness" in description


async def test_planner_query_follows_the_question(store, settings):
    a, _ = await _knowledge_args(store, settings, "trader", "Did wide spreads hurt the VWAP slippage?")
    b, _ = await _knowledge_args(store, settings, "trader", "Was gateway latency behind the rejects?")
    assert a["query"] != b["query"]
