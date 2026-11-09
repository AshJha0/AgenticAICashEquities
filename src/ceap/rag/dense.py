"""Optional dense retrieval, reciprocal rank fusion and cross-encoder reranking (0.9.0, C2).

Plain idea: BM25 matches words, so it misses a query that says the same thing in other words or
misspells a term. A small sentence-embedding model maps text to vectors whose cosine similarity
tracks meaning; a cross-encoder reads the query and a passage together and scores relevance. Both
are local CPU models (``BAAI/bge-small-en-v1.5`` and ``Xenova/ms-marco-MiniLM-L-6-v2`` through the
optional ``fastembed`` package, free download, no key).

Formal statement:

- dense score of chunk ``c`` for query ``q``: ``cos(E(q), E(c))`` with ``E`` the embedding model;
- reciprocal rank fusion of rankings ``r_1..r_m``: ``RRF(c) = sum_i 1 / (k + rank_i(c))``, ``k = 60``
  (Cormack, Clarke and Buettcher, SIGIR 2009);
- rerank: the top ``depth`` candidates of a ranking are re-ordered by the cross-encoder score.

Worked example: with ``k = 60`` a chunk ranked 1st by BM25 and 3rd by dense gets
``1/61 + 1/63 = 0.0323``; one ranked 2nd by both gets ``2/62 = 0.0323``; ties break by corpus order.

Every model output goes through a content-addressed cache (``sha256(model, role, text)``), stored
as a JSON file under ``data/rag/``. The committed caches hold every chunk and every eval query, so
the eval and CI run without the model or ``fastembed`` installed; a cache miss with no backend
raises :class:`ModelUnavailableError`. BM25 stays the production default (see
``docs/ai-agents/rag-hybrid-0.9.0.md`` for the measured comparison).
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from ceap.rag.retrieval import KnowledgeBase, RetrievalHit, index_text

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
RRF_K = 60
RERANK_DEPTH = 20
FULL_SCAN = 200
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "rag"
VARIANTS = ("bm25", "dense", "rrf", "bm25_rerank", "rrf_rerank")


class ModelUnavailableError(RuntimeError):
    """A text is not in the cache and no local model backend is installed."""


def cache_key(model: str, role: str, text: str) -> str:
    return hashlib.sha256(f"{model}\x00{role}\x00{text}".encode()).hexdigest()[:24]


class _JsonCache:
    def __init__(self, path: Path, model: str) -> None:
        self.path = path
        self.model = model
        self.items: dict[str, Any] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("model") != model:
                raise ValueError(f"{path} holds {data.get('model')}, not {model}")
            self.items = data["items"]
        self.dirty = False

    def save(self) -> None:
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {"model": self.model, "items": dict(sorted(self.items.items()))}
        self.path.write_text(json.dumps(body, indent=0) + "\n", encoding="utf-8")
        self.dirty = False


class Embedder:
    """Cached sentence embeddings (unit vectors, float16 in the cache)."""

    def __init__(self, model: str = EMBED_MODEL, cache_path: Path | None = None) -> None:
        self.model = model
        self.cache = _JsonCache(cache_path or CACHE_DIR / "embeddings.json", model)
        self._backend: Any = None

    def _load(self) -> Any:
        if self._backend is None:
            try:
                from fastembed import TextEmbedding  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - depends on the optional package
                raise ModelUnavailableError(f"{self.model}: not cached and fastembed is not installed") from exc
            self._backend = TextEmbedding(self.model)  # pragma: no cover
        return self._backend  # pragma: no cover

    def embed(self, texts: Sequence[str], role: str) -> np.ndarray:
        """``role`` is ``"query"`` or ``"passage"`` (bge adds an instruction to queries)."""
        keys = [cache_key(self.model, role, t) for t in texts]
        missing = [t for t, k in zip(texts, keys, strict=True) if k not in self.cache.items]
        if missing:
            backend = self._load()  # pragma: no cover - exercised only when building the cache
            fn = backend.query_embed if role == "query" else backend.passage_embed  # pragma: no cover
            for t, v in zip(missing, fn(list(missing), batch_size=4), strict=True):  # pragma: no cover
                v = np.asarray(v, dtype=np.float32)
                v = v / (np.linalg.norm(v) or 1.0)
                self.cache.items[cache_key(self.model, role, t)] = base64.b64encode(
                    v.astype(np.float16).tobytes()
                ).decode()
                self.cache.dirty = True
        rows = [np.frombuffer(base64.b64decode(self.cache.items[k]), dtype=np.float16) for k in keys]
        m = np.vstack(rows).astype(np.float32) if rows else np.zeros((0, 0), np.float32)
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        return m / np.where(norms == 0, 1.0, norms)


class Reranker:
    """Cached cross-encoder relevance scores (logits; higher = more relevant)."""

    def __init__(self, model: str = RERANK_MODEL, cache_path: Path | None = None) -> None:
        self.model = model
        self.cache = _JsonCache(cache_path or CACHE_DIR / "rerank.json", model)
        self._backend: Any = None

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        keys = [cache_key(self.model, query, p) for p in passages]
        missing = [(p, k) for p, k in zip(passages, keys, strict=True) if k not in self.cache.items]
        if missing:
            if self._backend is None:
                try:
                    from fastembed.rerank.cross_encoder import (  # type: ignore[import-not-found]
                        TextCrossEncoder,
                    )
                except ImportError as exc:  # pragma: no cover
                    raise ModelUnavailableError(f"{self.model}: not cached and fastembed is not installed") from exc
                self._backend = TextCrossEncoder(self.model)  # pragma: no cover
            scores = list(self._backend.rerank(query, [p for p, _ in missing], batch_size=4))  # pragma: no cover
            for (_, k), s in zip(missing, scores, strict=True):  # pragma: no cover
                self.cache.items[k] = round(float(s), 4)
                self.cache.dirty = True
        return [float(self.cache.items[k]) for k in keys]


def rrf(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> list[int]:
    """Fuse rankings of item indices by reciprocal rank; ties by smallest index."""
    score: dict[int, float] = {}
    for ranking in rankings:
        for r, i in enumerate(ranking, 1):
            score[i] = score.get(i, 0.0) + 1.0 / (k + r)
    return sorted(score, key=lambda i: (-score[i], i))


class HybridRetriever:
    """Ranks the chunks of a :class:`KnowledgeBase` with one of :data:`VARIANTS`.

    ``rank(query)`` returns ``(chunk index, score)`` pairs, best first, with no abstain gate;
    ``search`` applies the BM25 support gate and, optionally, a minimum cross-encoder score on the
    top hit (``min_rerank``), and returns :class:`RetrievalHit` like ``KnowledgeBase.search``.
    """

    def __init__(
        self,
        kb: KnowledgeBase,
        variant: str = "rrf",
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        depth: int = RERANK_DEPTH,
    ) -> None:
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant {variant!r}; one of {VARIANTS}")
        self.kb = kb
        self.variant = variant
        self.depth = depth
        self.texts = [index_text(c) for c in kb.chunks]
        needs_dense = variant in ("dense", "rrf", "rrf_rerank")
        self.embedder = (embedder or Embedder()) if needs_dense else None
        self.reranker = (reranker or Reranker()) if variant.endswith("rerank") else reranker
        self._matrix = self.embedder.embed(self.texts, "passage") if self.embedder is not None else None

    def bm25_order(self, query: str) -> list[int]:
        scores = self.kb.bm25_scores(query)
        return [i for i in sorted(range(len(scores)), key=lambda i: (-scores[i], i)) if scores[i] > 0]

    def dense_order(self, query: str) -> list[int]:
        assert self.embedder is not None and self._matrix is not None
        q = self.embedder.embed([query], "query")[0]
        sims = self._matrix @ q
        return sorted(range(len(sims)), key=lambda i: (-float(sims[i]), i))

    def rank(self, query: str) -> list[tuple[int, float]]:
        v = self.variant
        if v == "bm25" or v == "bm25_rerank":
            order = self.bm25_order(query)
        elif v == "dense":
            order = self.dense_order(query)
        else:
            order = rrf([self.bm25_order(query), self.dense_order(query)])
        if not v.endswith("rerank"):
            return [(i, 1.0 / (r + 1)) for r, i in enumerate(order)]
        assert self.reranker is not None
        head = order[: self.depth]
        scores = self.reranker.score(query, [self.texts[i] for i in head])
        reranked = sorted(zip(head, scores, strict=True), key=lambda p: (-p[1], p[0]))
        return reranked + [(i, float("-inf")) for i in order[self.depth :]]

    def top_rerank_score(self, query: str) -> float:
        """Best cross-encoder score over the candidates: every chunk while the corpus has at most
        ``FULL_SCAN`` chunks (65 today), else the top ``depth`` of the variant's ranking."""
        if self.reranker is None:
            self.reranker = Reranker()
        if len(self.texts) <= FULL_SCAN:
            cands = list(range(len(self.texts)))
        else:  # pragma: no cover - the committed corpus is small
            cands = [i for i, _ in self.rank(query)[: self.depth]]
        return max(self.reranker.score(query, [self.texts[i] for i in cands]), default=float("-inf"))

    def search(
        self, query: str, k: int = 4, min_support: float | None = None, min_rerank: float | None = None
    ) -> list[RetrievalHit]:
        """Top ``k`` hits, or ``[]`` when the query abstains.

        Abstain rule: with ``min_rerank`` set, abstain when the top cross-encoder score is below it;
        otherwise abstain when no chunk reaches the BM25 support gate (``min_support``, default the
        knowledge base's). The gate decides only whether to answer; the ranking is the variant's.
        """
        if not query.strip() or k <= 0:
            return []
        sc = self.kb.scores_and_coverage(query)
        if min_rerank is not None:
            if self.top_rerank_score(query) < min_rerank:
                return []
        else:
            gate = self.kb.min_support if min_support is None else min_support
            if gate > 0 and max((s * c for s, c in sc if s > 0), default=0.0) < gate:
                return []
        return [RetrievalHit(self.kb.chunks[i], *sc[i]) for i, _ in self.rank(query)[:k]]


def save_caches(*objs: Embedder | Reranker) -> None:
    for o in objs:
        o.cache.save()

