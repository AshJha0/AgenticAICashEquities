"""0.10.0 H4 (R-09, AI-3-05): a citation must come from a chunk that supports the claim.

Acceptance: planted right-chunk and wrong-chunk pairs; wrong-chunk citations are rejected.

The claims are paraphrases written for this test against the shipped knowledge base
(``data/reference/knowledge``); each names the chunk that states it (right) and an unrelated chunk
(wrong), both by a phrase that occurs only in that chunk. Both citations are real quotes, so the span
audit passes both; only the support check tells them apart.
"""

from __future__ import annotations

import pytest

from ceap.agents.reporter import citation_supports, claim_citations
from ceap.domain.evidence import document_citation
from ceap.rag.retrieval import build_knowledge_base

# (claim, phrase in the right chunk, phrase in the wrong chunk)
PLANTED = [
    (
        "The VWAP algorithm's maximum participation rate is 20% of interval volume.",
        "Maximum participation rate: 20%",
        "The SOR ranks venues",
    ),
    (
        "IEX applies a 350 microsecond speed bump and is used for passive slices.",
        "350 microsecond speed bump",
        "Maximum position per symbol",
    ),
    (
        "A venue whose fill rate falls below 60% of its peers for ten minutes is down-weighted by the router.",
        "fill rate drops below 60%",
        "The VWAP strategy (version",
    ),
    (
        "The maximum position per symbol in book CASH_EQ_1 is 500,000 shares.",
        "Maximum position per symbol",
        "350 microsecond speed bump",
    ),
    (
        "A limit breach blocks new child orders and raises a severity 2 incident.",
        "A breach detected by the risk-service",
        "CHG-40388 (smart-order-router",
    ),
    (
        "Change CHG-40388 altered routing latency budgets and venue timeouts in the smart-order-router.",
        "CHG-40388 (smart-order-router",
        "Maximum single parent order",
    ),
    (
        "An attribution starts only when implementation shortfall rises by at least 1.5 bps against the baseline.",
        "implementation shortfall up by at least 1.5 bps",
        "ARCA has experienced intermittent",
    ),
    (
        "ARCA acknowledgement delays during maintenance windows add 3-5 bps of slippage.",
        "ARCA has experienced intermittent",
        "standard stress scenario",
    ),
    (
        # the review's example: a real chunk about venue configuration cited for an escalation claim
        "Shortfall deterioration of more than 5 bps over a one-hour window requires a written explanation.",
        "requires a written explanation",
        "The SOR ranks venues",
    ),
    (
        "When quoted spreads widen beyond twice their 30-day median the algorithm shifts towards passive slices.",
        "When quoted spreads widen beyond 2x",
        "ack_latency_p99_us above",
    ),
]


@pytest.fixture(scope="module")
def chunks():
    return build_knowledge_base().chunks


def _chunk(chunks, phrase: str):
    found = [c for c in chunks if phrase in " ".join(c.text.split())]
    assert len(found) == 1, f"{phrase!r} must identify exactly one chunk, found {len(found)}"
    return found[0]


def _citation(chunk, claim: str):
    doc = {"chunk_id": chunk.id, "title": chunk.title, "section": chunk.section, "version": "test"}
    # the citing sentence is the claim itself: attachment passes, so only support decides
    return document_citation(doc, chunk.text, (0, len(claim)), claim, "corpus")


def test_planted_wrong_chunk_citations_are_rejected_and_right_ones_kept(chunks):
    right_kept = wrong_kept = 0
    for claim, right, wrong in PLANTED:
        good, bad = _citation(_chunk(chunks, right), claim), _citation(_chunk(chunks, wrong), claim)
        kept = claim_citations({"text": claim, "needs_policy_support": True}, [good, bad])
        right_kept += good.id in kept
        wrong_kept += bad.id in kept
        assert bad.id not in kept, f"wrong-chunk citation kept for: {claim}"
    assert wrong_kept == 0  # 0 of 10 wrong-chunk citations pass
    assert right_kept == len(PLANTED)  # and the check does not reject the chunks that do support


def test_a_number_the_passage_does_not_state_fails_support():
    passage = "Maximum participation rate: 20% of interval volume."
    assert citation_supports("The maximum participation rate is 20% of interval volume.", passage)
    assert not citation_supports("The maximum participation rate is 25% of interval volume.", passage)
