"""3C.1/3C.5: chunk metadata, corpus versioning and growth, chunking strategies, role filter."""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

import pytest

from ceap.analytics.attribution import Thresholds
from ceap.rag.ingestion import (
    CHUNKING_STRATEGIES,
    Document,
    chunk_document,
    corpus_hash,
    load_documents,
    parse_front_matter,
)
from ceap.rag.retrieval import DEFAULT_KNOWLEDGE_DIR, KnowledgeBase, build_knowledge_base


@pytest.fixture(scope="module")
def kb():
    return build_knowledge_base()


def test_every_document_is_versioned_and_dated(kb):
    for d in kb.documents.values():
        assert d.version != "unversioned", d.filename
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.effective_date), d.filename
        assert not d.text.startswith("---")


def test_quant_doc_and_threshold_tables_are_in_the_corpus(kb):
    names = {d.filename for d in kb.documents.values()}
    assert {"quant.md", "09_attribution_thresholds.md", "02_tca_methodology.md"} <= names


def test_threshold_question_retrieves_thresholds_section_with_version(kb):
    top = kb.search("why this deterioration threshold", k=3)[0].to_dict()
    assert top["document"] == "09_attribution_thresholds.md"
    assert top["section"] == "Why these deterioration thresholds"
    assert top["version"] == "1.0" and top["effective_date"] == "2026-11-07"


def test_hit_dict_carries_metadata(kb):
    d = kb.search("maximum participation rate VWAP", k=1)[0].to_dict()
    keys = ("chunk_id", "document_id", "document", "title", "section", "version", "effective_date")
    assert all(k in d for k in (*keys, "classification", "score", "coverage", "support", "text"))


def test_threshold_table_matches_code():
    text = (DEFAULT_KNOWLEDGE_DIR / "09_attribution_thresholds.md").read_text(encoding="utf-8")
    cells = {float(m) for m in re.findall(r"\|\s*(?:below\s+)?([0-9.]+)(?:x| bps)?\s*\|\s*$", text, re.M)}
    t = Thresholds()
    assert {float(getattr(t, f.name)) for f in fields(t)} == cells


def test_corpus_hash_changes_with_version_and_is_order_free():
    a = Document("A", "a", "a.md", "x", metadata={"filename": "a.md", "version": "1"})
    b = Document("B", "b", "b.md", "y", metadata={"filename": "b.md", "version": "1"})
    a2 = Document("A", "a", "a.md", "x", metadata={"filename": "a.md", "version": "2"})
    assert corpus_hash([a, b]) == corpus_hash([b, a]) != corpus_hash([a2, b])
    assert len(build_knowledge_base().corpus_hash) == 64
    assert KnowledgeBase([]).corpus_hash == corpus_hash([])


def test_front_matter_parsing():
    meta, body = parse_front_matter("---\nversion: 3\neffective_date: 2026-01-02\n---\n\n# T\nbody")
    assert meta == {"version": "3", "effective_date": "2026-01-02"} and body.startswith("# T")
    assert parse_front_matter("# T") == ({}, "# T")


def test_manifest_missing_source_is_skipped(tmp_path: Path):
    (tmp_path / "a.md").write_text("# A\n\ntext", encoding="utf-8")
    (tmp_path / "manifest.json").write_text('{"external": [{"path": "nope.md", "version": "1"}]}')
    assert [d.filename for d in load_documents(tmp_path)] == ["a.md"]


@pytest.mark.parametrize("strategy", CHUNKING_STRATEGIES)
def test_each_strategy_chunks_and_labels_sections(strategy):
    doc = Document("D", "T", "p", "# T\n\n## A\n\n" + "Alpha beta. " * 60 + "\n\n## B\n\nGamma delta.")
    chunks = chunk_document(doc, strategy=strategy)
    assert chunks and all(c.text for c in chunks)
    assert {s for c in chunks for s in c.sections} == {"A", "B"}
    assert [c.order for c in chunks] == list(range(len(chunks)))


def test_unknown_strategy_rejected():
    with pytest.raises(ValueError):
        chunk_document(Document("D", "T", "p", "x"), strategy="bogus")  # type: ignore[arg-type]


def test_classification_filter(kb):
    q = "maximum position per symbol"
    assert kb.search(q, k=3)[0].chunk.classification == "restricted"
    assert all(
        h.chunk.classification != "restricted" for h in kb.search(q, k=3, classifications={"internal"})
    )
