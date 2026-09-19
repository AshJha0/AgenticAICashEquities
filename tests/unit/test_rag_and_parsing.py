"""RAG retrieval, LLM helpers and request parsing."""

from __future__ import annotations

from datetime import date

from ceap.api.parsing import parse_question
from ceap.llm.client import MockLLMClient, extract_json
from ceap.llm.models import LLMRequest
from ceap.rag.embeddings import HashingEmbedder, tokenize
from ceap.rag.ingestion import Document, chunk_document
from ceap.rag.retrieval import KnowledgeBase, build_knowledge_base


def test_knowledge_base_loads_documents_and_finds_runbook():
    kb = build_knowledge_base()
    assert len(kb.documents) == 8 and len(kb) > 8
    hits = kb.search("VWAP participation ceiling maximum participation rate", k=3)
    assert hits and "VWAP" in hits[0].chunk.title
    assert kb.search("", k=3) == []
    assert kb.get("nope") is None


def test_incident_runbook_retrieved_for_latency_query():
    kb = build_knowledge_base()
    hits = kb.search("ack latency SLO breached TOO_LATE_TO_ENTER rejects", k=2)
    assert any("Incident" in h.chunk.title for h in hits)


def test_chunking_splits_on_headings_and_size():
    doc = Document(
        "D", "T", "p", "# A\n\n" + ("para one. " * 50) + "\n\n" + ("para two. " * 50) + "\n\n# B\n\nshort"
    )
    chunks = chunk_document(doc, max_chars=300)
    assert len(chunks) >= 3 and chunks[-1].section == "B"


def test_hashing_embedder_is_deterministic_and_normalised():
    e = HashingEmbedder(256)
    e.fit(["alpha beta", "beta gamma"])
    v1, v2 = e.embed(["alpha beta"]), e.embed(["alpha beta"])
    assert (v1 == v2).all() and abs(float((v1**2).sum()) - 1.0) < 1e-5
    assert tokenize("The VWAP-algo is at 14:00") == ["vwap-algo", "14", "00"]


def test_empty_knowledge_base_is_safe():
    kb = KnowledgeBase([])
    assert kb.search("anything") == [] and kb.titles() == []


def test_extract_json_handles_fences_and_trailing_text():
    assert extract_json('```json\n{"a": 1}\n```')["a"] == 1
    assert extract_json('prefix {"a": {"b": 2}} trailing')["a"]["b"] == 2
    assert extract_json("no json here") is None


async def test_mock_llm_purposes():
    llm = MockLLMClient()
    plan = await llm.complete(
        LLMRequest(
            "s",
            [{"role": "user", "content": "question with {} braces"}],
            purpose="planning",
            metadata={"parameters": {"symbol": "MSFT"}},
        )
    )
    assert '"steps"' in plan.content and "MSFT" in plan.content
    other = await llm.complete(LLMRequest("s", [{"role": "user", "content": "hi"}]))
    assert other.content == "OK" and len(llm.calls) == 2


def test_parse_question_symbol_window_and_relative_dates():
    d = date(2026, 9, 18)
    p = parse_question("Analyse our AAPL execution between 14:00 and 15:00", d)
    assert p.symbol == "AAPL" and p.window_start.hour == 14 and p.window_end.hour == 15
    assert p.window_start.tzinfo is not None
    p2 = parse_question("Why did MSFT deteriorate from 2pm to 3:30pm yesterday?", d, today=date(2026, 9, 19))
    assert p2.symbol == "MSFT" and p2.session_date == d and p2.relative_day == "yesterday"
    assert (p2.window_start.hour, p2.window_end.hour, p2.window_end.minute) == (14, 15, 30)
    p3 = parse_question("Look at NVDA on 2026-09-18 between 9 and 10", d)
    assert p3.session_date == d and p3.window_start.hour == 9
    p4 = parse_question("what happened?", d)
    assert p4.symbol is None and p4.window_start is None
