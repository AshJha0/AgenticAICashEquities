"""Retrieval eval: recall at k, MRR, negatives, and the chunking comparison.

The query set (``data/reference/knowledge/eval.json``) was committed before any tuning. Tuning
(chunking choice, ``DEFAULT_MIN_SUPPORT``) uses ``split == "train"`` only; ``heldout`` and
``negative`` are scored, never tuned on. Run ``python -m ceap.rag.evaluation`` for the table.

C1 (0.8.0) adds the ``extended`` and ``extended_negative`` splits, 119 queries written from the
corpus text before scoring: paraphrases, typos, multi-hop questions (``match: all``: found only when
every expected section is in the top k) and near-miss negatives (on-domain questions the corpus does
not answer). :func:`extended_report` scores every category with a 95 % Wilson interval.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ceap.rag.ingestion import CHUNKING_STRATEGIES, ChunkingStrategy, load_documents
from ceap.rag.retrieval import DEFAULT_KNOWLEDGE_DIR, KnowledgeBase, RetrievalHit

DEFAULT_EVAL = DEFAULT_KNOWLEDGE_DIR / "eval.json"


@dataclass(frozen=True)
class SplitResult:
    split: str
    n: int
    recall_at_k: float
    recall_at_1: float
    mrr: float
    misses: tuple[str, ...]


def load_eval(path: str | Path = DEFAULT_EVAL) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = json.loads(Path(path).read_text(encoding="utf-8"))["queries"]
    return queries


def is_relevant(hit: RetrievalHit, expected: list[dict[str, str]]) -> bool:
    """A hit is relevant when its document matches and it covers an expected section."""
    c = hit.chunk
    return any(c.document == e["document"] and e["section"] in (c.sections or (c.section,)) for e in expected)


def rank_of(hits: list[RetrievalHit], query: dict[str, Any]) -> int | None:
    """1-based rank at which ``query`` is answered: the first relevant hit, or for ``match: all`` the
    rank by which every expected section has been covered."""
    expected = query["expected"]
    if query.get("match") != "all":
        return next((i + 1 for i, h in enumerate(hits) if is_relevant(h, expected)), None)
    pending = list(range(len(expected)))
    for i, h in enumerate(hits):
        pending = [j for j in pending if not is_relevant(h, [expected[j]])]
        if not pending:
            return i + 1
    return None


def score_split(
    kb: KnowledgeBase, queries: list[dict[str, Any]], split: str, k: int = 3, min_support: float = 0.0
) -> SplitResult:
    """Recall at ``k`` and at 1 (a relevant chunk ranked that high) and MRR over the top 10."""
    rows = [q for q in queries if q["split"] == split]
    found = first = 0
    rr = 0.0
    misses: list[str] = []
    for q in rows:
        hits = kb.search(q["query"], k=10, min_support=min_support)
        rank = rank_of(hits, q)
        if rank is not None and rank <= k:
            found += 1
            first += rank == 1
        else:
            misses.append(q["id"])
        rr += 1.0 / rank if rank else 0.0
    n = len(rows) or 1
    return SplitResult(split, len(rows), found / n, first / n, rr / n, tuple(misses))


def negatives_leaking(kb: KnowledgeBase, queries: list[dict[str, Any]], min_support: float) -> list[str]:
    """Ids of negative queries that return any hit at or above ``min_support``."""
    return [
        q["id"]
        for q in queries
        if q["split"] == "negative" and kb.search(q["query"], k=1, min_support=min_support)
    ]


def max_support(kb: KnowledgeBase, queries: list[dict[str, Any]], split: str) -> dict[str, float]:
    """Highest support among the top 10 hits of each query of ``split``."""
    return {
        q["id"]: max((h.support for h in kb.search(q["query"], k=10, min_support=0.0)), default=0.0)
        for q in queries
        if q["split"] == split
    }


def calibrate_min_support(kb: KnowledgeBase, queries: list[dict[str, Any]], k: int = 3) -> float:
    """Weakest support of a relevant top-``k`` hit on the train split, rounded down (train only)."""
    supports = []
    for q in queries:
        if q["split"] != "train":
            continue
        rel = [
            h.support for h in kb.search(q["query"], k=k, min_support=0.0) if is_relevant(h, q["expected"])
        ]
        if rel:
            supports.append(max(rel))
    return math.floor(min(supports) * 10) / 10 if supports else 0.0


def compare_chunkings(
    directory: str | Path = DEFAULT_KNOWLEDGE_DIR, eval_path: str | Path = DEFAULT_EVAL
) -> dict[ChunkingStrategy, dict[str, SplitResult]]:
    docs = load_documents(directory)
    queries = load_eval(eval_path)
    out: dict[ChunkingStrategy, dict[str, SplitResult]] = {}
    for strategy in CHUNKING_STRATEGIES:
        kb = KnowledgeBase(docs, chunking=strategy)
        out[strategy] = {s: score_split(kb, queries, s) for s in ("train", "heldout")}
    return out


def extended_report(
    kb: KnowledgeBase | None = None, queries: list[dict[str, Any]] | None = None, k: int = 3
) -> dict[str, Any]:
    """Recall at ``k``, recall at 1 and MRR per category, and the negative leak rate, with Wilson CIs.

    Scored with the production gate (``kb.min_support``), the way the knowledge tool answers. The
    ``all_positive`` row pools every answerable query (train, held-out and extended).
    """
    from ceap.eval.stats import rate

    kb = kb or KnowledgeBase(load_documents(DEFAULT_KNOWLEDGE_DIR))
    queries = queries if queries is not None else load_eval()

    def category(q: dict[str, Any]) -> str:
        return str(q.get("category") or {"train": "train", "heldout": "heldout"}.get(q["split"], q["split"]))

    positives = [q for q in queries if q["expected"]]
    negatives = [q for q in queries if not q["expected"]]
    ranks = {q["id"]: rank_of(kb.search(q["query"], k=10, min_support=kb.min_support), q) for q in positives}

    def block(xs: list[dict[str, Any]]) -> dict[str, Any]:
        r = [ranks[q["id"]] for q in xs]
        return {
            "n": len(xs),
            f"recall_at_{k}": rate(sum(x is not None and x <= k for x in r), len(xs)),
            "recall_at_1": rate(sum(x == 1 for x in r), len(xs)),
            "mrr": round(sum(1.0 / x for x in r if x) / len(xs), 4) if xs else None,
            "misses": [q["id"] for q, x in zip(xs, r, strict=True) if x is None or x > k],
        }

    cats = sorted({category(q) for q in positives})
    leaks = [q["id"] for q in negatives if kb.search(q["query"], k=1, min_support=kb.min_support)]
    neg_cats = sorted({category(q) for q in negatives})
    return {
        "evidence_class": "deterministic",
        "queries": len(queries),
        "min_support": kb.min_support,
        "chunking": kb.chunking,
        "corpus_hash": kb.corpus_hash,
        "all_positive": block(positives),
        "by_category": {c: block([q for q in positives if category(q) == c]) for c in cats},
        "negatives": {
            c: {
                "leak": rate(
                    sum(q["id"] in leaks for q in negatives if category(q) == c),
                    sum(category(q) == c for q in negatives),
                ),
                "leaking": [q["id"] for q in negatives if category(q) == c and q["id"] in leaks],
            }
            for c in neg_cats
        },
    }


def main() -> None:
    print("| Chunking | Train R@3 | Train R@1 | Train MRR | Held-out R@3 | Held-out R@1 | Held-out MRR |")
    print("|---|---|---|---|---|---|---|")
    for strategy, row in compare_chunkings().items():
        t, h = row["train"], row["heldout"]
        print(
            f"| {strategy} | {t.recall_at_k:.2f} | {t.recall_at_1:.2f} | {t.mrr:.3f} "
            f"| {h.recall_at_k:.2f} | {h.recall_at_1:.2f} | {h.mrr:.3f} |"
        )
    kb = KnowledgeBase(load_documents(DEFAULT_KNOWLEDGE_DIR))
    q = load_eval()
    neg = max_support(kb, q, "negative")
    print(f"\ndefault chunking={kb.chunking} min_support={kb.min_support} corpus={kb.corpus_hash[:12]}")
    print(
        f"calibrated on train: {calibrate_min_support(kb, q)}; negative max support={max(neg.values()):.2f}"
    )
    print("negatives leaking:", negatives_leaking(kb, q, kb.min_support))
    for s in ("train", "heldout"):
        r = score_split(kb, q, s, min_support=kb.min_support)
        print(
            f"{s} with gate: R@3={r.recall_at_k:.2f} R@1={r.recall_at_1:.2f} MRR={r.mrr:.3f} misses={list(r.misses)}"
        )


if __name__ == "__main__":
    main()
