"""0.8.0 AI evaluation harness: per-agent evals (B2), metric sensitivity (B3), corpus governance (C3).

Offline: the mock client and the degraded doubles in ``ceap.eval.clients``. The sensitivity test is
the point of B3: a metric that does not move when its agent's model is made worse cannot detect a
bad model.
"""

from __future__ import annotations

import inspect
from datetime import date

import pytest

from ceap.agents.critic import CriticAgent
from ceap.agents.reporter import ReportAgent, audit_numbers, validate_claims
from ceap.eval.agents import (
    CRITIC_LEAD,
    SENSITIVITY,
    critic_cases,
    critic_eval,
    load_gold_plans,
    reporter_cases,
    reporter_eval,
    sensitivity,
)
from ceap.eval.clients import (
    INJECTED_CLAIMS,
    UNTRACEABLE_NUMBER_TEXT,
    CapturingClient,
    RubberStampCritic,
    evidence_class,
)
from ceap.eval.corpus import corpus_problems, corpus_report, load_pin, pin_corpus
from ceap.eval.stats import rate, wilson
from ceap.llm.client import MockLLMClient
from ceap.llm.router import LLMRouter
from ceap.rag.ingestion import Document

# ------------------------------------------------------------------ stats


def test_wilson_interval_matches_known_values():
    assert wilson(10, 10) == (0.7225, 1.0)  # the plan's "about 0.72" for 10/10
    lo, hi = wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4) and hi == pytest.approx(0.7634, abs=1e-4)
    assert wilson(0, 0) == (0.0, 1.0)
    with pytest.raises(ValueError):
        wilson(3, 2)
    assert rate(3, 4) == {"k": 3, "n": 4, "rate": 0.75, "ci95": list(wilson(3, 4))}


# ------------------------------------------------------------------ evidence class


def test_evidence_class_reads_the_innermost_client_and_never_assumes():
    mock = MockLLMClient()
    assert evidence_class(mock) == "mock"
    assert evidence_class(CapturingClient(RubberStampCritic(mock))) == "mock"
    assert evidence_class(LLMRouter(mock)) == "mock"

    class Local(MockLLMClient):
        provenance = "local_model"

    assert evidence_class(CapturingClient(Local())) == "local_model"

    class Unknown(MockLLMClient):
        provenance = "vibes"

    assert evidence_class(Unknown()) == "unknown"


# ------------------------------------------------------------------ cases are well formed


def test_gold_plans_are_scoped_and_hand_built():
    gold = load_gold_plans()
    allowed = set(gold["allowed"])
    assert len(gold["cases"]) == 10 and len({c["id"] for c in gold["cases"]}) == 10
    for case in gold["cases"]:
        assert set(case["required"]) <= allowed, case["id"]
    assert not any(t.startswith(("alpha.", "backtest.", "research_data.")) for t in allowed)


def test_critic_cases_plant_each_fault_kind_three_times():
    batches = critic_cases()
    kinds = [f["planted"] for b in batches for f in b["findings"] if f["planted"]]
    assert len(batches) == 5 and len(kinds) == 15
    assert {k: kinds.count(k) for k in set(kinds)} == {
        "uncited": 3,
        "unresolved": 3,
        "contradicted": 3,
        "numeric_mismatch": 3,
        "direction_mismatch": 3,
    }


def test_critic_request_uses_the_agents_own_lead_text():
    # drift guard: the eval builds the critic's request; the agent must still send the same lead
    assert CRITIC_LEAD.strip() in inspect.getsource(CriticAgent._llm_critique)


def test_reporter_eval_requests_match_the_agent():
    source = inspect.getsource(ReportAgent._claims)
    assert "REPORTER_CLAIMS_SYSTEM_PROMPT" in source and "report_request_content(facts)" in source


def test_every_injected_claim_is_unfaithful_by_the_guardrail():
    for case in reporter_cases():
        facts = case["facts"]
        assert audit_numbers(UNTRACEABLE_NUMBER_TEXT, facts), "the planted number must not trace to facts"
        for kind, claim in INJECTED_CLAIMS:
            kept, dropped = validate_claims([claim], facts)
            assert not kept and dropped, (case["id"], kind)


# ------------------------------------------------------------------ per-agent evals (B2)


async def test_critic_eval_mock_detects_only_the_structural_faults():
    r = await critic_eval(MockLLMClient())
    assert r["evidence_class"] == "mock" and r["cases_source"] == "hand_built"
    by = {k: v["rate"] for k, v in r["detection_by_kind"].items()}
    # the mock checks evidence presence only; semantic contradictions need a model
    assert by["uncited"] == 1.0 and by["unresolved"] == 1.0
    assert by["contradicted"] == by["numeric_mismatch"] == by["direction_mismatch"] == 0.0
    assert r["false_alarm"]["rate"] == 0.0 and r["critique_valid"]["rate"] == 1.0
    assert r["detection"]["ci95"][0] < r["detection"]["rate"] < r["detection"]["ci95"][1]


async def test_reporter_eval_mock_rates():
    r = await reporter_eval(MockLLMClient())
    assert r["faithfulness"]["rate"] == 1.0 and r["claim_drop"]["rate"] == 0.0
    assert r["injection_caught"] == rate(16, 16)
    assert r["citation_verified"]["rate"] == 1.0 and r["citation_verified"]["n"] >= 4


@pytest.mark.slow
async def test_sensitivity_every_model_metric_moves_under_its_degraded_client():
    r = await sensitivity()
    assert len(r["rows"]) == len(SENSITIVITY) == 8
    assert r["all_moved"], [x for x in r["rows"] if not x["moved"]]
    base = r["baseline"]
    assert base["planner"]["evidence_class"] == "mock"
    assert set(base["rates"]) == {"plan_valid", "repair", "claim_drop", "citation_verified"}
    assert base["planner"]["tool_recall"]["n"] == sum(len(c["required"]) for c in load_gold_plans()["cases"])


# ------------------------------------------------------------------ corpus governance (C3)


def test_corpus_matches_its_pin():
    report = corpus_report(today=date(2026, 11, 9))
    assert report["pinned"] and report["problems"] == [], report["problems"]


def _doc(name: str, version: str, effective: str, text: str) -> Document:
    return Document(
        id=name,
        title=name,
        path=name,
        text=text,
        metadata={"filename": name, "version": version, "effective_date": effective},
    )


def test_corpus_check_catches_edits_without_a_bump_and_stale_or_future_documents():
    docs = [_doc("a.md", "1.0", "2026-10-01", "body"), _doc("b.md", "2.0", "2026-10-01", "text")]
    pin = pin_corpus(docs)
    today = date(2026, 11, 9)
    assert corpus_problems(docs, pin, today) == {"problems": [], "warnings": []}
    edited = [_doc("a.md", "1.0", "2026-10-01", "body changed"), docs[1]]
    assert "without a version bump" in " ".join(corpus_problems(edited, pin, today)["problems"])
    bumped = [_doc("a.md", "1.1", "2026-10-01", "body changed"), docs[1]]
    assert "re-pin" in " ".join(corpus_problems(bumped, pin, today)["problems"])
    assert "not pinned" in " ".join(
        corpus_problems([*docs, _doc("c.md", "1", "2026-10-01", "x")], pin, today)["problems"]
    )
    assert "missing" in " ".join(corpus_problems(docs[:1], pin, today)["problems"])
    future = [_doc("a.md", "1.0", "2027-01-01", "body"), docs[1]]
    assert "future" in " ".join(corpus_problems(future, pin_corpus(future), today)["problems"])
    stale = corpus_problems(docs, pin, date(2028, 1, 1))
    assert stale["problems"] == [] and len(stale["warnings"]) == 2
    notes = [_doc("a.md", "1.0", "2026-10-01", "Since 0.5.0 the gate is stricter."), docs[1]]
    found = corpus_problems(notes, pin_corpus(notes), today)
    assert found["problems"] == [] and "release-note wording" in found["warnings"][0]


def test_corpus_pin_records_every_document():
    pin = load_pin()
    assert len(pin["documents"]) == 10 and all(len(d["body_sha256"]) == 64 for d in pin["documents"])
