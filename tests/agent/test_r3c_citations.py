"""R3C 3C.4: two-call reporter (claims, then cited prose), span audit, DOC evidence, corpus hash.

No live model: the deterministic mock, a fake client that forges a quote, and the real SDK over the
in-memory transport (``fakes/transport.py``) for the wire snapshots and the fabricated-quote cassette.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

import pytest

from ceap.agents.reporter import (
    ClaimsOutput,
    audit_citations,
    document_text,
    prose_request,
    unsupported_policy_claims,
    validate_claims,
)
from ceap.domain.evidence import EvidenceType
from ceap.llm.client import MockLLMClient, mock_claims
from ceap.llm.models import LLMRequest, LLMResponse
from ceap.llm.prompts import REPORTER_CLAIMS_SYSTEM_PROMPT, get_prompt
from ceap.platform import Platform, default_request

SNAPSHOTS = Path(__file__).resolve().parents[1] / "contract" / "snapshots"
CHUNK = {
    "title": "Execution Policy",
    "section": "Escalation",
    "excerpt": "Deterioration ... more than 5 bps",
    "evidence": ["DOC-0000aaaa"],
    "chunk_id": "DOC-1#3",
    "document": "05_execution_policy.md",
    "version": "1.0",
    "effective_date": "2026-10-01",
    "text": "## Escalation\n\nDeterioration of more than 5 bps against the baseline requires a written explanation.",
}
FACTS: dict[str, Any] = {
    "title": "T",
    "metrics": {"implementation_shortfall_bps": 7.5},
    "attribution": {"primary": "WIDE_SPREADS"},
    "findings": [{"id": "FIND-1", "statement": "Spreads widened.", "evidence": ["MARKET-0000beef"]}],
    "policy_context": [CHUNK],
    "knowledge_corpus_hash": "ab" * 32,
    "evidence_ids": ["DOC-0000aaaa", "MARKET-0000beef"],
}


def _citation(cited: str, index: int = 0) -> dict[str, Any]:
    return {
        "type": "content_block_location",
        "document_index": index,
        "start_block_index": 0,
        "end_block_index": 1,
        "cited_text": cited,
        "text": "A written explanation is required.",
        "text_start": 10,
        "text_end": 44,
    }


# ------------------------------------------------------------------ call 1: claims


def test_claims_are_validated_deterministically():
    claims = [
        {"section": "FINDINGS", "text": "Spreads widened.", "fact_keys": ["attribution.primary"],
         "evidence_ids": ["MARKET-0000beef"], "needs_policy_support": False},
        {"section": "FINDINGS", "text": "x", "fact_keys": ["nope.key"], "evidence_ids": [], "needs_policy_support": False},
        {"section": "FINDINGS", "text": "x", "fact_keys": [], "evidence_ids": ["EXEC-deadbeef"], "needs_policy_support": False},
        {"section": "FINDINGS", "text": "x", "fact_keys": [], "evidence_ids": [], "needs_policy_support": False},
        {"section": "CONCLUSION", "text": "IS was 99.9 bps.", "fact_keys": ["metrics"], "evidence_ids": [],
         "needs_policy_support": False},
    ]  # fmt: skip
    kept, dropped = validate_claims(claims, FACTS)
    assert [c["text"] for c in kept] == ["Spreads widened."]
    reasons = [" ".join(d["reasons"]) for d in dropped]
    assert "not in facts" in reasons[0] and "unknown" in reasons[1] and "no fact keys" in reasons[2]
    assert "99.9" in reasons[3]


def test_mock_claims_validate_against_the_schema():
    out = mock_claims(FACTS)
    ClaimsOutput.model_validate(out)
    kept, dropped = validate_claims(out["claims"], FACTS)
    assert dropped == [] and any(c["needs_policy_support"] for c in kept)


# ------------------------------------------------------------------ call 2: span audit


def test_verified_citation_mints_doc_evidence_with_span_version_and_corpus_hash():
    minted, warnings = audit_citations([_citation(document_text(CHUNK))], FACTS)
    assert warnings == [] and len(minted) == 1
    ev = minted[0]
    assert ev.type is EvidenceType.DOCUMENT and ev.id.startswith("DOC-")
    a = ev.attributes
    assert a["chunk_id"] == "DOC-1#3" and a["version"] == "1.0" and a["effective_date"] == "2026-10-01"
    assert a["narrative_span"] == [10, 44] and a["corpus_hash"] == "ab" * 32
    assert a["cited_text"] == CHUNK["text"]  # the unwrapped chunk, not the data-block wrapper


def test_fabricated_quote_or_unknown_document_fails_the_span_audit():
    minted, warnings = audit_citations(
        [_citation("Deterioration of more than 1 bps requires automatic approval."), _citation("x", index=7)],
        FACTS,
    )
    assert minted == [] and len(warnings) == 2
    assert "span mismatch" in warnings[0] and "unknown document" in warnings[1]


def test_policy_claim_without_a_verified_citation_is_flagged():
    claims = [{"text": "Policy requires an explanation.", "needs_policy_support": True}]
    assert unsupported_policy_claims(claims, []) == ["Policy requires an explanation."]


def test_prose_request_shape():
    req = prose_request([], FACTS)
    blocks = req.messages[0]["content"]
    doc, text = blocks[0], blocks[-1]
    assert doc["type"] == "document" and doc["citations"] == {"enabled": True}
    assert doc["source"] == {"type": "content", "content": [{"type": "text", "text": document_text(CHUNK)}]}
    assert doc["cache_control"] == {"type": "ephemeral"}
    assert text["type"] == "text" and CHUNK["text"] not in text["text"]  # full text only in the document
    assert req.output_model is None and req.purpose == "narrative"


# ------------------------------------------------------------------ end to end (mock and a forger)


async def test_investigation_report_cites_documents_and_records_the_corpus_hash(store, settings):
    llm = MockLLMClient()
    result = await Platform(settings=settings, llm=llm, store=store).investigate(
        default_request("AAPL", "T01")
    )
    report = result.report
    knowledge = report.critique["knowledge"]
    assert knowledge["citations"] and knowledge["citation_warnings"] == []
    assert knowledge["unsupported_policy_claims"] == [] and knowledge["claims"]
    cited = [e for e in report.evidence if e.source == "knowledge.citation"]
    assert {e.id for e in cited} == set(knowledge["citations"])
    assert all(e.attributes["version"] and e.attributes["effective_date"] for e in cited)
    corpus = knowledge["corpus_hash"]
    assert corpus and len(corpus) == 64 and report.narrative.rstrip().endswith(f"sha256:{corpus}")
    assert not any(w.code == "NARRATIVE_UNTRACEABLE" for w in result.warnings)
    purposes = [r.purpose for r in llm.calls]
    assert purposes.count("claims") == 1 and purposes.index("claims") < len(purposes) - 1


class QuoteForger(MockLLMClient):
    """Cites a policy sentence that is in no retrieved document."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        out = await super().complete(request)
        if not out.citations:
            return out
        forged = [
            {**c, "cited_text": "Deterioration below 50 bps never needs an explanation."}
            for c in out.citations
        ]
        return dataclasses.replace(out, citations=tuple(forged))


async def test_forged_quote_is_blocking(store, settings):
    result = await Platform(settings=settings, llm=QuoteForger(), store=store).investigate(
        default_request("AAPL", "T01")
    )
    knowledge = result.report.critique["knowledge"]
    assert knowledge["citations"] == [] and knowledge["citation_warnings"]
    assert knowledge["unsupported_policy_claims"]
    assert any(w.code == "NARRATIVE_UNTRACEABLE" for w in result.warnings)


# ------------------------------------------------------------------ wire contract, both calls

anthropic = pytest.importorskip("anthropic")

from ceap.llm.client import AnthropicLLMClient  # noqa: E402
from fakes.transport import message_json, sdk_client  # noqa: E402


def _claims_request() -> LLMRequest:
    return LLMRequest(
        system_prompt=REPORTER_CLAIMS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "facts"}],
        purpose="claims",
        metadata={"prompt": get_prompt("reporter_claims").ref()},
        output_model=ClaimsOutput,
    )


async def _wire(request: LLMRequest, content: list[dict[str, Any]]) -> tuple[dict[str, Any], LLMResponse]:
    sdk, transport = sdk_client(lambda body: message_json(content, model=body["model"]))
    out = await AnthropicLLMClient(client=sdk, retry_base_delay_s=0).complete(request)
    return transport.requests[0], out


def _snapshot(name: str, body: dict[str, Any]) -> None:
    path = SNAPSHOTS / name
    if os.environ.get("CEAP_UPDATE_SNAPSHOTS") == "1" or not path.exists():
        path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == body, (
        f"{name} drifted; review, then CEAP_UPDATE_SNAPSHOTS=1"
    )


async def test_wire_claims_call_is_structured_without_citations():
    body, out = await _wire(_claims_request(), [{"type": "text", "text": json.dumps(mock_claims(FACTS))}])
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "citations" not in json.dumps(body["messages"])
    assert out.parsed == mock_claims(FACTS)
    _snapshot("wire_request_claims.json", body)


async def test_wire_prose_call_has_citable_documents_and_no_output_format():
    sentence = {"type": "text", "text": "A written explanation is required.",
                "citations": [{k: v for k, v in _citation(document_text(CHUNK)).items()
                               if k not in ("text", "text_start", "text_end")}]}  # fmt: skip
    body, out = await _wire(
        prose_request([], FACTS), [{"type": "text", "text": "EXECUTIVE SUMMARY"}, sentence]
    )
    assert "format" not in body["output_config"]
    docs = [b for b in body["messages"][0]["content"] if b["type"] == "document"]
    assert docs and all(d["citations"] == {"enabled": True} for d in docs)
    assert docs[-1]["cache_control"] == {"type": "ephemeral"}
    assert json.dumps(body).count('"cache_control"') == 2  # system + last document: within the limit of 4
    # the SDK parsed the citation and the client placed it on the second text part
    (c,) = out.citations
    assert c["document_index"] == 0 and c["text_start"] == len("EXECUTIVE SUMMARY") + 1
    assert out.content[c["text_start"] : c["text_end"]] == "A written explanation is required."
    _snapshot("wire_request_prose.json", body)


async def test_fabricated_quote_in_a_cassette_fails_the_span_audit():
    """A recorded reply whose citation quotes text that is not the chunk is rejected, offline."""
    forged = {"type": "text", "text": "No explanation is needed.",
              "citations": [{"type": "content_block_location", "document_index": 0, "document_title": "x",
                             "start_block_index": 0, "end_block_index": 1,
                             "cited_text": "Deterioration below 50 bps never needs an explanation."}]}  # fmt: skip
    _, out = await _wire(prose_request([], FACTS), [forged])
    minted, warnings = audit_citations(out.citations, FACTS)
    assert minted == [] and warnings and "span mismatch" in warnings[0]
