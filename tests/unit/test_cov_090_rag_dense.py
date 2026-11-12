"""Coverage for the 0.9.0 dense/rerank caches and the L3 abstain rule (``ceap.rag.dense``,
``ceap.rag.hybrid_eval``). Runs from the committed caches; no model, no ``fastembed``."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np
import pytest

from ceap.rag import hybrid_eval
from ceap.rag.dense import Embedder, HybridRetriever, Reranker, _JsonCache, cache_key, save_caches
from ceap.rag.evaluation import load_eval
from ceap.rag.retrieval import build_knowledge_base

REPORT = Path(__file__).resolve().parents[2] / "docs" / "ai-agents" / "rag-hybrid-0.9.0.json"


def _vec(xs: list[float]) -> str:
    return base64.b64encode(np.asarray(xs, dtype=np.float16).tobytes()).decode()


def test_json_cache_refuses_another_models_file_and_saves_only_when_dirty(tmp_path):
    p = tmp_path / "sub" / "c.json"
    c = _JsonCache(p, "m1")
    c.save()
    assert not p.exists()  # nothing changed, nothing written
    c.items["k"] = 1.5
    c.dirty = True
    c.save()
    assert json.loads(p.read_text(encoding="utf-8")) == {"model": "m1", "items": {"k": 1.5}}
    assert c.dirty is False
    assert _JsonCache(p, "m1").items == {"k": 1.5}
    with pytest.raises(ValueError, match="holds m1, not m2"):
        _JsonCache(p, "m2")


def test_embedder_reads_cache_and_renormalises(tmp_path):
    e = Embedder("m", cache_path=tmp_path / "e.json")
    e.cache.items[cache_key("m", "query", "a")] = _vec([3.0, 4.0])  # norm 5: (0.6, 0.8)
    e.cache.items[cache_key("m", "query", "z")] = _vec([0.0, 0.0])  # zero row stays zero, no NaN
    m = e.embed(["a", "z"], "query")
    assert m.shape == (2, 2)
    np.testing.assert_allclose(m[0], [0.6, 0.8], atol=1e-3)
    assert m[1].tolist() == [0.0, 0.0]
    # the role is part of the key: the same text as a passage is a different entry
    assert cache_key("m", "query", "a") != cache_key("m", "passage", "a")


def test_reranker_reads_cache_and_save_caches_writes_dirty_only(tmp_path):
    r = Reranker("rm", cache_path=tmp_path / "r.json")
    r.cache.items[cache_key("rm", "q", "p1")] = 2.25
    r.cache.items[cache_key("rm", "q", "p2")] = -1.0
    assert r.score("q", ["p2", "p1"]) == [-1.0, 2.25]
    r.cache.dirty = True
    save_caches(r)
    assert (tmp_path / "r.json").exists()


def test_unknown_variant_and_empty_queries():
    kb = build_knowledge_base()
    with pytest.raises(ValueError, match="unknown variant"):
        HybridRetriever(kb, "bm42")
    h = HybridRetriever(kb, "bm25")
    assert h.search("   ") == [] and h.search("kill switch", k=0) == []


def test_min_rerank_gate_abstains_or_answers():
    kb = build_knowledge_base()
    h = HybridRetriever(kb, "bm25")
    q = next(x["query"] for x in load_eval() if x["expected"])  # in the committed rerank cache
    top = h.top_rerank_score(q)
    assert len(h.search(q, k=2, min_rerank=top)) == 2  # at the bar: answers
    assert h.search(q, k=2, min_rerank=top + 0.01) == []  # just above: abstains


def test_split_of_prereg_rule():
    assert hybrid_eval.split_of({"split": "train", "id": "q2"}) == "dev"
    assert hybrid_eval.split_of({"split": "negative", "id": "q2"}) == "dev"
    assert hybrid_eval.split_of({"split": "heldout", "id": "q1"}) == "heldout"
    # other splits: odd numeric id -> dev, even -> heldout; no digits counts as 0 (heldout)
    assert hybrid_eval.split_of({"split": "eval", "id": "q-07"}) == "dev"
    assert hybrid_eval.split_of({"split": "eval", "id": "q-10"}) == "heldout"
    assert hybrid_eval.split_of({"split": "eval", "id": "abc"}) == "heldout"


def test_paired_bootstrap_constant_difference_has_a_point_interval():
    a = np.array([1, 1, 1, 1])
    b = np.array([0, 0, 0, 0])
    assert hybrid_eval.paired_bootstrap(a, b, draws=50) == [1.0, 1.0]
    lo, hi = hybrid_eval.paired_bootstrap(np.array([1, 0, 1, 0]), np.array([0, 0, 0, 0]), draws=500)
    assert 0.0 <= lo <= 0.5 <= hi <= 1.0


def test_abstain_reproduces_the_published_report():
    """The rule chosen on dev and its held-out numbers are the ones in the committed report."""
    got = hybrid_eval.abstain()
    published = json.loads(REPORT.read_text(encoding="utf-8"))["abstain"]
    assert got == published
    assert got["chosen_on_dev"]["signal"] == "rerank_top"
    assert got["n"] == {"dev": 90, "heldout": 69}
