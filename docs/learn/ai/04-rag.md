# Lesson 4: RAG

Learning goals: explain lexical (BM25) and dense retrieval and what a reranker adds; compute and read
recall at k and MRR; explain why a leak on a near-miss negative matters and what abstention is.

## Plain idea

Retrieval-augmented generation hands the model a few documents relevant to the question, so it can
cite policy and runbooks instead of guessing. The retriever matters more than the model: if the
right section is not in the top few, the model cannot use it; if a wrong but similar section is
returned for a question the corpus cannot answer, the model may answer anyway. A retriever should
therefore also be able to abstain.

## Formal statement

- BM25 scores a document by term overlap, weighted by rarity and normalised by length. Dense
  retrieval compares embedding vectors. Reciprocal rank fusion (RRF) merges ranked lists. A
  cross-encoder reranker re-scores the top candidates with the query and document read together.
- Recall at k: the share of answerable queries with a correct section in the top k. MRR: the mean of
  1/rank of the first correct hit.
- Leak rate: the share of negative queries (no answer in the corpus) for which the retriever returns
  something instead of abstaining. A near-miss negative is on-domain and shares wording with a real
  section.

## Worked example from this repo

From [docs/ai-agents/rag-hybrid-0.9.0.md](../../ai-agents/rag-hybrid-0.9.0.md) (124 positive queries,
ranking only, no abstain gate):

| Variant | R@3 (k/124) | Wilson 95 % | MRR |
|---|---|---|---|
| bm25 | 108 | 0.80-0.92 | 0.796 |
| dense | 95 | 0.68-0.83 | 0.674 |
| rrf | 112 | 0.84-0.94 | 0.782 |
| bm25_rerank | 114 | 0.86-0.96 | 0.833 |
| rrf_rerank | 116 | 0.88-0.97 | 0.848 |

Most of the gain is on typos (BM25 23/30, rrf_rerank 29/30). Yet BM25 stays the default: the
pre-registered rule also required no model download at query time, and the reranker needs one.

Abstention: a threshold chosen on the dev split leaked on near-miss negatives 3/13 on dev but 6/12 on
held-out, against 1/12 for the 0.8.0 gate, so it was not adopted. The ranking-only 108/124 above is
not the gated figure: `retrieval.all_positive.recall_at_3` in
[docs/evaluation/latest.json](../../evaluation/latest.json) is 89/124 with the support gate
(`min_support` 2.5), which also drops weak correct hits.

In CEAP:
- `src/ceap/rag/bm25.py`, `retrieval.py`, `query.py`: lexical retrieval and the support gate.
- `src/ceap/rag/dense.py`: the opt-in `HybridRetriever`.
- `src/ceap/rag/hybrid_eval.py`, `src/ceap/rag/evaluation.py`: the measurements.

## Offline exercise

Recompute the hybrid table from committed caches (about 10 s on the test laptop; no model download
unless you add `--build`):

```bash
python -m ceap.rag.hybrid_eval
git diff --stat docs/ai-agents/rag-hybrid-0.9.0.json
git checkout -- docs/ai-agents/rag-hybrid-0.9.0.json
```

The command rewrites the committed JSON; compare the recall figures with the table above, then
restore the file. Then run the query tests: `python -m pytest -q -p no:randomly --no-cov tests/unit/test_rag_query.py`.

## Pitfalls

- Choosing a threshold and reporting it on the same split.
- Comparing a gated recall with an ungated one.
- Measuring only positives: a retriever that always answers has perfect recall and no abstention.

## Self-check

<details><summary>1. Why did BM25 stay the default when rrf_rerank had higher R@3?</summary>
The rule also required no model download at query time; the reranker needs about 100 MB of models.</details>

<details><summary>2. What is MRR if the first correct hit is at rank 2 for every query?</summary>
0.5.</details>

<details><summary>3. Why was the dev-chosen abstention rule rejected?</summary>
On held-out it leaked 6/12 near-misses against 1/12 for the existing gate.</details>

## Further reading

- [LEARN.md](../../../LEARN.md), section 12.
- [docs/ai-agents/l3-prereg-0.9.0.md](../../ai-agents/l3-prereg-0.9.0.md), the rules fixed before measuring.
- Same concept, another desk: [AgenticTrader AI learning path](https://github.com/AshJha0/agentic-trader/blob/main/docs/learn/ai_learning_path.md).
