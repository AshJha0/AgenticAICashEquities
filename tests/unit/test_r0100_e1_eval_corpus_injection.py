"""Behaviour tests for the corpus pin check and the injection corpus helpers (0.10.0 E1)."""

from __future__ import annotations

import base64
import json
from datetime import date

import pytest

from ceap.eval import corpus as corpus_mod
from ceap.eval import injection as inj
from ceap.rag.ingestion import Document


def _doc(name: str, text: str = "body", version: str = "1.0", effective: str = "2026-01-01") -> Document:
    meta = {"filename": name, "effective_date": effective}
    if version:
        meta["version"] = version
    return Document(name, name, name, text, metadata=meta)


TODAY = date(2026, 6, 1)


def test_pin_round_trip_has_no_problems() -> None:
    docs = [_doc("b.md"), _doc("a.md", "other")]
    pin = corpus_mod.pin_corpus(docs)
    assert [p["filename"] for p in pin["documents"]] == ["a.md", "b.md"]
    assert corpus_mod.corpus_problems(docs, pin, TODAY) == {"problems": [], "warnings": []}


def test_added_and_removed_documents_are_problems() -> None:
    pin = corpus_mod.pin_corpus([_doc("gone.md")])
    found = corpus_mod.corpus_problems([_doc("new.md")], pin, TODAY)
    assert found["problems"] == [
        "gone.md: pinned but missing from the corpus (re-pin after review)",
        "new.md: in the corpus but not pinned (re-pin after review)",
    ]


def test_body_edit_without_version_bump_is_named() -> None:
    pin = corpus_mod.pin_corpus([_doc("a.md", "old")])
    found = corpus_mod.corpus_problems([_doc("a.md", "new")], pin, TODAY)
    assert found["problems"] == ["a.md: body edited without a version bump (still 1.0)"]


def test_version_bump_without_repin_is_named() -> None:
    pin = corpus_mod.pin_corpus([_doc("a.md", "old")])
    found = corpus_mod.corpus_problems([_doc("a.md", "new", version="1.1")], pin, TODAY)
    assert len(found["problems"]) == 1
    assert "now version 1.1" in found["problems"][0] and "pin says 1.0" in found["problems"][0]


def test_unversioned_bad_date_future_and_stale() -> None:
    docs = [
        _doc("u.md", version=""),
        _doc("bad.md", effective="soon"),
        _doc("fut.md", effective="2027-01-01"),
        _doc("old.md", effective="2024-01-01", text="Changed in v0.5.0 the rule moved."),
    ]
    found = corpus_mod.corpus_problems(docs, corpus_mod.pin_corpus(docs), TODAY)
    assert "u.md: no version in front matter or manifest" in found["problems"]
    assert "bad.md: effective_date 'soon' is not an ISO date" in found["problems"]
    assert "fut.md: effective_date 2027-01-01 is in the future" in found["problems"]
    assert any(w.startswith("old.md: effective 2024-01-01, older than 365") for w in found["warnings"])
    assert any("release-note wording 'Changed in v0.5.0'" in w for w in found["warnings"])


def test_shipped_corpus_matches_its_pin() -> None:
    report = corpus_mod.corpus_report()
    assert report["pinned"] is True
    assert report["problems"] == []
    assert report["documents"] == len(corpus_mod.load_pin()["documents"])


def test_attack_templates_embed_canary_and_encodings() -> None:
    a = inj._attack("question", 7, "base64", "tool")
    assert a["id"] == "question-007" and a["canary"] == "CNRY-question-007"
    payload = a["text"].rsplit(" ", 1)[1]
    assert inj.FORBIDDEN_TOOL in base64.b64decode(payload).decode()
    split = inj._attack("question", 1, "split", "tool")
    assert "token A" in split["text"] and split["canary"] in split["text"]


def test_build_corpus_matches_committed_file() -> None:
    built = inj.build_corpus()
    assert len(built["attacks"]) == len(inj.SURFACES) * len(inj.STYLES) * len(inj.GOALS)
    assert built["sha256"] == inj.load_corpus()["sha256"]


def test_stratified_covers_every_style_early() -> None:
    attacks = inj.build_corpus()["attacks"]
    assert inj.stratified(attacks, None) is attacks
    picked = inj.stratified(attacks, 5)
    assert len(picked) == 5 * len(inj.SURFACES)
    first_q = [a for a in picked if a["surface"] == "question"]
    assert len({a["goal"] for a in first_q}) == 5
    assert len({a["style"] for a in first_q}) == 5


def test_obeyed_ignores_echo_and_detects_canary() -> None:
    a = inj._attack("tool_output", 3, "direct", "tool")
    assert inj.obeyed(a, None) is False
    assert inj.obeyed(a, f"quoted: {a['text']}") is False
    assert inj.obeyed(a, json.dumps({"q": a["text"]})) is False
    assert inj.obeyed(a, f"step: {a['canary']}") is True
    assert inj.obeyed(a, "call SHELL.EXEC now") is True


@pytest.mark.asyncio
async def test_run_corpus_with_mock_client_obeys_nothing() -> None:
    from ceap.llm.client import MockLLMClient

    result = await inj.run_corpus(MockLLMClient(), per_surface=1)
    assert result["attacks"] == len(inj.SURFACES)
    assert set(result["per_surface"]) == set(inj.SURFACES)
    assert result["obey"]["k"] == 0
    assert all(r["obeyed"] is False for r in result["rows"])
    assert result["corpus_sha256"] == inj.load_corpus()["sha256"]
