"""0.9.0 (L3) C2: hybrid retrieval from the committed model caches (no model needed in CI).

Pins the measured comparison (docs/ai-agents/rag-hybrid-0.9.0.md) so a corpus or cache change is
noticed; the a-priori decision rule is in docs/ai-agents/l3-prereg-0.9.0.md.
"""

from __future__ import annotations

import pytest

from ceap.rag.dense import HybridRetriever, rrf
from ceap.rag.hybrid_eval import compare
from ceap.rag.retrieval import build_knowledge_base


def test_rrf_worked_example():
    # rank 1 + rank 3 vs rank 2 + rank 2: 1/61 + 1/63 > 2/62, so item 0 first
    assert rrf([[0, 1, 2], [2, 1, 0]])[0] in (0, 2)
    assert rrf([[5, 6], [6, 5]]) == [5, 6]


@pytest.mark.slow
def test_comparison_from_cache():
    r = compare()
    v = r["variants"]
    assert v["bm25"]["recall_at_3"]["k"] == 108
    assert v["rrf_rerank"]["recall_at_3"]["k"] == 116
    assert set(r["winners_by_prereg_rule"]) == {"bm25_rerank", "rrf_rerank"}


def test_bm25_stays_default():
    kb = build_knowledge_base()
    h = HybridRetriever(kb, "bm25")
    q = "kill switch procedure"
    assert [x.chunk.id for x in h.search(q, k=3)] == [x.chunk.id for x in kb.search(q, k=3)][: len(h.search(q, k=3))]
