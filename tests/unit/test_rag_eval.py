"""3C.1/3C.2: BM25 retrieval eval with a held-out split, negatives and a CI floor.

The query set was committed before tuning (data/reference/knowledge/eval.json). Floors, with
tolerance: the held-out split has 10 queries, so one miss moves recall at 3 by 0.10. The floor of
0.80 tolerates two held-out misses against a measured 1.00; MRR floor 0.70 against 1.000. Train
floor 0.90 (measured 0.95). Every negative must return nothing.
"""

from __future__ import annotations

import pytest

from ceap.rag.evaluation import (
    calibrate_min_support,
    compare_chunkings,
    load_eval,
    negatives_leaking,
    score_split,
)
from ceap.rag.ingestion import DEFAULT_CHUNKING
from ceap.rag.retrieval import DEFAULT_MIN_SUPPORT, build_knowledge_base

HELDOUT_RECALL3_FLOOR = 0.80
HELDOUT_MRR_FLOOR = 0.70
TRAIN_RECALL3_FLOOR = 0.90


@pytest.fixture(scope="module")
def kb():
    return build_knowledge_base()


@pytest.fixture(scope="module")
def queries():
    return load_eval()


def test_eval_set_shape(queries):
    splits = [q["split"] for q in queries]
    assert splits.count("train") == 20 and splits.count("heldout") == 10 and splits.count("negative") == 10
    # C1 (0.8.0): 119 extended queries, never tuned on
    assert splits.count("extended") == 94 and splits.count("extended_negative") == 25
    assert len({q["id"] for q in queries}) == len(queries) == 159
    cats = [q.get("category") for q in queries if q["split"].startswith("extended")]
    assert {c: cats.count(c) for c in set(cats)} == {
        "paraphrase": 44,
        "typo": 30,
        "multi_hop": 20,
        "near_miss_negative": 25,
    }
    assert all(
        len(q["expected"]) >= 2 and q["match"] == "all" for q in queries if q.get("category") == "multi_hop"
    )
    assert all(q["expected"] for q in queries if not q["split"].endswith("negative"))


def test_expected_sections_exist_in_the_corpus(kb, queries):
    present = {(c.document, s) for c in kb.chunks for s in c.sections}
    for q in queries:
        for e in q["expected"]:
            assert (e["document"], e["section"]) in present, (q["id"], e)


def test_heldout_meets_the_committed_floor(kb, queries):
    r = score_split(kb, queries, "heldout", min_support=kb.min_support)
    assert r.n == 10
    assert r.recall_at_k >= HELDOUT_RECALL3_FLOOR, r
    assert r.mrr >= HELDOUT_MRR_FLOOR, r


def test_train_meets_its_floor(kb, queries):
    assert score_split(kb, queries, "train", min_support=kb.min_support).recall_at_k >= TRAIN_RECALL3_FLOOR


def test_negatives_return_nothing(kb, queries):
    assert negatives_leaking(kb, queries, kb.min_support) == []
    for q in queries:
        if q["split"] == "negative":
            assert kb.search(q["query"], k=4) == []


def test_default_gate_matches_train_calibration(kb, queries):
    assert calibrate_min_support(kb, queries) == DEFAULT_MIN_SUPPORT


def test_default_chunking_is_the_eval_winner():
    table = compare_chunkings()
    assert set(table) == {"section", "sentence", "fixed"}

    def key(s: str) -> tuple[float, float, float]:
        t = table[s]["train"]  # chosen on train only
        return (t.recall_at_k, t.recall_at_1, t.mrr)

    best = max(key(s) for s in table)
    # Ties within 0.01 MRR go to the simplest strategy (fewest, whole-section chunks).
    winners = [s for s in table if key(s)[:2] == best[:2] and best[2] - key(s)[2] <= 0.01]
    assert DEFAULT_CHUNKING in winners


def test_retrieval_is_deterministic(kb, queries):
    other = build_knowledge_base()
    for q in queries[:10]:
        a = [h.to_dict() for h in kb.search(q["query"], k=5)]
        b = [h.to_dict() for h in other.search(q["query"], k=5)]
        assert a == b


def test_extended_report_has_wilson_intervals_and_honest_numbers(kb, queries):
    from ceap.rag.evaluation import extended_report

    r = extended_report(kb, queries)
    assert r["queries"] == 159 and r["all_positive"]["n"] == 124
    for block in r["by_category"].values():
        lo, hi = block["recall_at_3"]["ci95"]
        assert 0.0 <= lo <= block["recall_at_3"]["rate"] <= hi <= 1.0
    # the original held-out split is unchanged by the extension
    assert r["by_category"]["heldout"]["recall_at_3"]["rate"] >= HELDOUT_RECALL3_FLOOR
    # measured on BM25 at the committed gate (0.8.0): recorded, not tuned. Typos and multi-hop are
    # BM25's known weak spots; C2 (hybrid retrieval) is measured against these numbers.
    assert (
        r["by_category"]["typo"]["recall_at_3"]["rate"]
        < r["by_category"]["paraphrase"]["recall_at_3"]["rate"]
    )
    assert r["negatives"]["negative"]["leaking"] == []


def test_multi_hop_needs_every_expected_section(kb):
    from ceap.rag.evaluation import rank_of

    q = {
        "query": "latency incident first response and the known router change to consider",
        "match": "all",
        "expected": [
            {"document": "07_incident_runbook.md", "section": "First response"},
            {"document": "07_incident_runbook.md", "section": "Known change"},
        ],
    }
    hits = kb.search(q["query"], k=10, min_support=0.0)
    one = {**q, "match": None, "expected": q["expected"][:1]}
    assert rank_of(hits, one) is not None
    both = rank_of(hits, q)
    assert both is None or both >= rank_of(hits, one)
