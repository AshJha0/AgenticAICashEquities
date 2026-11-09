# 0.9.0 lane L3: rules fixed before measuring

Written 2026-11-09 and committed before any hybrid retrieval result, abstain threshold or critic
change was measured. Results go in `docs/ai-agents/rag-hybrid-0.9.0.md` once measured.

## C2 hybrid retrieval

- Query set: `data/reference/knowledge/eval.json` (159 queries, unchanged). Positives: the 124
  queries with expected sections; negatives: 10 original plus 25 near-miss.
- Variants: `bm25` (production today), `dense` (bge-small-en-v1.5, cosine), `rrf` (reciprocal rank
  fusion of bm25 and dense, k = 60), `rrf_rerank` (top 20 of rrf reranked by the
  ms-marco-MiniLM-L-6-v2 cross-encoder), `bm25_rerank` (top 20 of bm25 reranked).
- A-priori metric: recall at 3 over the 124 positives, ranking only (no abstain gate), with a
  paired bootstrap 95 % interval (10 000 resamples, seed 0) of the difference against `bm25`.
- Decision rule: a variant replaces `bm25` as the default only if the lower bound of that interval
  is above zero **and** it can run without a network model download at query time. Otherwise BM25
  stays the default and the hybrid is opt-in. Secondary metrics (R@1, MRR, per category) are
  reported, not decided on.

## (a) abstain rule for near-miss negatives

- Dev split: train (20 positives), the 10 original negatives, and the extended positives and
  near-miss negatives whose numeric id is odd. Held-out: the even-numbered extended queries and
  the held-out split. Rule and threshold are chosen on dev only.
- Candidate signals: BM25 support (today's gate) and the cross-encoder score of the top hit.
- Objective on dev: maximise answered-and-correct rate at 3 on dev positives minus leak rate on
  dev negatives; ties go to the higher threshold.
- Reported on held-out: leak rate on held-out near-miss negatives and gated R@3 on held-out
  positives, both with Wilson intervals, next to the 0.8.0 numbers.

## (b) critic

- Existing planted set: `ceap.eval.agents.critic_cases()` (15 planted, 10 controls).
- New held-out planted set: `data/eval/critic_planted_heldout_v1.json` (15 planted, 10 controls),
  committed before the critic change. Same author as the fix; see its `author_note`.
- Measured on the deterministic critic (no model). A finding counts as detected when the critic
  marks it unsupported or records a contradiction or check flag against it; the single-evidence
  confidence cap alone does not count. False alarm: the same on a control.
