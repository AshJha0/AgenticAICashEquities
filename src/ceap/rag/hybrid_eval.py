"""C2 comparison of retrieval variants against BM25, and the near-miss abstain rule (0.9.0, L3).

Rules fixed before measuring: ``docs/ai-agents/l3-prereg-0.9.0.md``. Results:
``docs/ai-agents/rag-hybrid-0.9.0.md``. Runs from the committed model caches (``data/rag/``), so no
model or ``fastembed`` is needed. ``python -m ceap.rag.hybrid_eval`` prints the JSON report.

Plain idea: rank every positive query with each variant, count how often an expected section is in
the top 3, and compare with BM25 on the same queries (paired). Then pick, on a dev split only, the
signal and threshold that best separates answerable queries from near-miss negatives, and report
that rule once on the held-out split.

Formal statement: per query ``i`` and variant ``v``, ``h_v(i) = 1`` when ``rank_of <= 3``. The
paired bootstrap resamples queries with replacement (10 000 draws, seed 0) and reports the 2.5 and
97.5 percentiles of ``mean(h_v - h_bm25)``. Abstain objective on dev: answered-and-correct rate at
3 on positives minus leak rate on negatives, ties to the higher threshold.
"""

from __future__ import annotations

import json
import re
from typing import Any

import numpy as np

from ceap.eval.stats import rate
from ceap.rag.dense import VARIANTS, Embedder, HybridRetriever, Reranker
from ceap.rag.evaluation import load_eval, rank_of
from ceap.rag.ingestion import load_documents
from ceap.rag.retrieval import DEFAULT_KNOWLEDGE_DIR, KnowledgeBase, RetrievalHit

BOOTSTRAP_DRAWS = 10_000
K = 3


def split_of(q: dict[str, Any]) -> str:
    """``dev`` or ``heldout`` per the pre-registered rule."""
    if q["split"] in ("train", "negative"):
        return "dev"
    if q["split"] == "heldout":
        return "heldout"
    n = int(re.sub(r"\D", "", q["id"]) or 0)
    return "dev" if n % 2 else "heldout"


def paired_bootstrap(a: np.ndarray, b: np.ndarray, draws: int = BOOTSTRAP_DRAWS, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    d = a.astype(float) - b.astype(float)
    idx = rng.integers(0, len(d), size=(draws, len(d)))
    means = d[idx].mean(axis=1)
    return [round(float(np.percentile(means, 2.5)), 4), round(float(np.percentile(means, 97.5)), 4)]


def _hits(h: HybridRetriever, query: str, k: int = 10) -> list[RetrievalHit]:
    return [RetrievalHit(h.kb.chunks[i], 0.0) for i, _ in h.rank(query)[:k]]


def compare(kb: KnowledgeBase | None = None, queries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    kb = kb or KnowledgeBase(load_documents(DEFAULT_KNOWLEDGE_DIR))
    queries = queries if queries is not None else load_eval()
    pos = [q for q in queries if q["expected"]]
    emb, rr = Embedder(), Reranker()
    ranks: dict[str, list[int | None]] = {}
    for v in VARIANTS:
        h = HybridRetriever(kb, v, embedder=emb, reranker=rr)
        ranks[v] = [rank_of(_hits(h, q["query"]), q) for q in pos]
    base = np.array([r is not None and r <= K for r in ranks["bm25"]])
    cats = sorted({str(q.get("category") or q["split"]) for q in pos})
    out: dict[str, Any] = {"evidence_class": "deterministic", "n_positive": len(pos), "variants": {}}
    for v, r in ranks.items():
        hit = np.array([x is not None and x <= K for x in r])
        out["variants"][v] = {
            "recall_at_3": rate(int(hit.sum()), len(hit)),
            "recall_at_1": rate(sum(x == 1 for x in r), len(r)),
            "mrr": round(sum(1.0 / x for x in r if x) / len(r), 4),
            "delta_recall_at_3_vs_bm25": round(float(hit.mean() - base.mean()), 4),
            "delta_ci95": paired_bootstrap(hit, base),
            "by_category_recall_at_3": {
                c: rate(
                    int(sum(h for h, q in zip(hit, pos, strict=True) if str(q.get("category") or q["split"]) == c)),
                    sum(str(q.get("category") or q["split"]) == c for q in pos),
                )
                for c in cats
            },
        }
    wins = [v for v, row in out["variants"].items() if v != "bm25" and row["delta_ci95"][0] > 0]
    out["winners_by_prereg_rule"] = wins
    return out


def abstain(kb: KnowledgeBase | None = None, queries: list[dict[str, Any]] | None = None, variant: str = "bm25") -> dict[str, Any]:
    """Choose signal and threshold on dev; report on held-out (``variant`` supplies the ranking)."""
    kb = kb or KnowledgeBase(load_documents(DEFAULT_KNOWLEDGE_DIR))
    queries = queries if queries is not None else load_eval()
    rr = Reranker()
    h = HybridRetriever(kb, variant, reranker=rr)
    rows = []
    for q in queries:
        sc = kb.scores_and_coverage(q["query"])
        support = max((s * c for s, c in sc if s > 0), default=0.0)
        rows.append(
            {
                "id": q["id"],
                "split": split_of(q),
                "positive": bool(q["expected"]),
                "near_miss": q.get("category") == "near_miss_negative",
                "correct": bool(q["expected"]) and (rank_of(_hits(h, q["query"]), q) or 99) <= K,
                "signals": {"bm25_support": support, "rerank_top": h.top_rerank_score(q["query"])},
            }
        )

    def evaluate(rs: list[dict[str, Any]], signal: str, t: float) -> dict[str, Any]:
        p = [r for r in rs if r["positive"]]
        n = [r for r in rs if not r["positive"]]
        nm = [r for r in n if r["near_miss"]]
        ans = [r["signals"][signal] >= t for r in rs]
        a = dict(zip([r["id"] for r in rs], ans, strict=True))
        return {
            "answered_correct_at_3": rate(sum(a[r["id"]] and r["correct"] for r in p), len(p)),
            "leak_all_negatives": rate(sum(a[r["id"]] for r in n), len(n)),
            "leak_near_miss": rate(sum(a[r["id"]] for r in nm), len(nm)),
            "leaking": [r["id"] for r in n if a[r["id"]]],
        }

    dev = [r for r in rows if r["split"] == "dev"]
    held = [r for r in rows if r["split"] == "heldout"]
    # objective first; across signals a tie keeps today's BM25 support; within a signal, higher t
    cands = [
        (evaluate(dev, sig, t), sig, t) for sig in ("bm25_support", "rerank_top") for t in {r["signals"][sig] for r in dev}
    ]
    scored = [(e["answered_correct_at_3"]["rate"] - e["leak_all_negatives"]["rate"], sig == "bm25_support", t, sig) for e, sig, t in cands]
    obj, _, t, signal = max(scored)
    best = (obj, t, signal)
    gate = 2.5
    return {
        "evidence_class": "deterministic",
        "ranking_variant": variant,
        "chosen_on_dev": {"signal": signal, "threshold": round(t, 4), "objective": round(best[0], 4)},
        "dev": evaluate(dev, signal, t),
        "heldout": evaluate(held, signal, t),
        "heldout_with_0_8_0_gate": evaluate(held, "bm25_support", gate),
        "dev_with_0_8_0_gate": evaluate(dev, "bm25_support", gate),
        "n": {"dev": len(dev), "heldout": len(held)},
    }


def build_caches() -> None:
    """Fill ``data/rag/`` caches for every chunk and eval query (needs ``pip install fastembed``)."""
    from ceap.rag.dense import save_caches

    kb = KnowledgeBase(load_documents(DEFAULT_KNOWLEDGE_DIR))
    emb, rr = Embedder(), Reranker()
    for v in VARIANTS:
        h = HybridRetriever(kb, v, embedder=emb, reranker=rr)
        for q in load_eval():
            h.rank(q["query"])
            h.top_rerank_score(q["query"])
        save_caches(emb, rr)


def main() -> None:
    """``python -m ceap.rag.hybrid_eval [--build] [--out PATH]``: optionally rebuild the model caches,
    then write the JSON report (default ``docs/ai-agents/rag-hybrid-0.9.0.json``)."""
    import sys
    from pathlib import Path

    if "--build" in sys.argv:
        build_caches()
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else (
        Path(__file__).resolve().parents[3] / "docs" / "ai-agents" / "rag-hybrid-0.9.0.json"
    )
    out.write_text(json.dumps({"compare": compare(), "abstain": abstain()}, indent=1) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
