"""Lexical retrieval (Okapi BM25) over chunked, versioned enterprise documents.

Public API (stable; 3C.3 planner wiring and 3C.4 reporter citations build on it):

- ``build_knowledge_base(directory=None, chunking=DEFAULT_CHUNKING) -> KnowledgeBase``
- ``KnowledgeBase.search(query, k=4, min_score=0.0, classifications=None, min_support=None)
  -> list[RetrievalHit]``. Ranked by BM25 score. A hit must also clear the support gate,
  ``support = score * coverage`` (coverage: share of the query's IDF mass the chunk contains,
  ``BM25.coverage``) at least ``min_support`` (default ``DEFAULT_MIN_SUPPORT``). An empty list
  means "no policy found" and the caller must say so rather than cite the nearest miss.
  ``classifications`` restricts hits to documents of those classifications (role filtering, 3C.3).
  ``as_of`` (ISO date, default today in UTC) hides documents whose ``effective_date`` is later, and
  of several versions sharing a ``doc_key`` serves only the latest one in force on that date.
- Documents are admitted by ``ceap.rag.ingestion.admit_documents`` first: a document with
  instruction-like text or marked superseded is never indexed (``KnowledgeBase.rejected``).
- ``KnowledgeBase.corpus_hash``: SHA-256 of the corpus, to record in reports.
- ``RetrievalHit.to_dict()``: ``chunk_id, document_id, document, title, section, version,
  effective_date, classification, score, coverage, support, text``.

This is not semantic search and is not labelled as such (decision D6).
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ceap.rag.bm25 import BM25
from ceap.rag.ingestion import (
    DEFAULT_CHUNKING,
    Chunk,
    ChunkingStrategy,
    Document,
    Rejection,
    admit_documents,
    chunk_document,
    corpus_hash,
    load_documents,
)
from ceap.rag.text import tokenize

DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "data" / "reference" / "knowledge"
# The weakest support of a relevant top-3 hit on the train split, rounded down to 0.1
# (``ceap.rag.evaluation.calibrate_min_support``; table and caveats in docs/ai-agents/ai-agents.md).
DEFAULT_MIN_SUPPORT = 2.5  # 0.6.0: re-calibrated after the corpus grew (was 2.4)
MAX_K = 50
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalHit:
    chunk: Chunk
    score: float
    coverage: float = 1.0

    @property
    def support(self) -> float:
        return self.score * self.coverage

    def to_dict(self) -> dict[str, object]:
        c = self.chunk
        return {
            "chunk_id": c.id,
            "document_id": c.document_id,
            "document": c.document,
            "title": c.title,
            "section": c.section,
            "version": c.version,
            "effective_date": c.effective_date,
            "classification": c.classification,
            "score": round(self.score, 4),
            "coverage": round(self.coverage, 4),
            "support": round(self.support, 4),
            "text": c.text,
        }


class KnowledgeBase:
    def __init__(
        self,
        documents: list[Document],
        chunking: ChunkingStrategy = DEFAULT_CHUNKING,
        min_support: float = DEFAULT_MIN_SUPPORT,
    ) -> None:
        documents, rejected = admit_documents(documents)
        self.rejected: list[Rejection] = rejected
        self.documents = {d.id: d for d in documents}
        self.chunking = chunking
        self.min_support = min_support
        self.chunks: list[Chunk] = [c for d in documents for c in chunk_document(d, strategy=chunking)]
        # Title and section are indexed with the body: headings carry most of the topical signal.
        self._index = BM25(
            [tokenize(f"{c.title} {' '.join(c.sections) or c.section} {c.text}") for c in self.chunks]
        )
        self.corpus_hash = corpus_hash(documents)

    def __len__(self) -> int:
        return len(self.chunks)

    def search(
        self,
        query: str,
        k: int = 4,
        min_score: float = 0.0,
        classifications: Collection[str] | None = None,
        min_support: float | None = None,
        as_of: str | None = None,
    ) -> list[RetrievalHit]:
        """Top ``k`` chunks by BM25 score, highest first, ties by corpus order; deterministic.

        A chunk is returned only if its score is positive and at least ``min_score`` and its
        support is at least ``min_support`` (default: the knowledge base's). Pass
        ``min_support=0.0`` for the raw ranking.

        A query made of ``"; "``-separated segments (``ceap.rag.query.retrieval_query``) is scored
        segment by segment, so one focused segment is not diluted by the others; results are taken
        round-robin across segments (best first), without duplicates, up to ``k``.
        """
        if not self.chunks or not query.strip() or k <= 0:
            return []
        k = min(int(k), MAX_K)
        allowed = self.in_force(as_of)
        segments = [s for s in (part.strip() for part in query.split(";")) if s]
        ranked = [self._search_segment(s, k, min_score, classifications, min_support, allowed) for s in segments]
        if len(ranked) == 1:
            return ranked[0]
        hits: list[RetrievalHit] = []
        seen: set[str] = set()
        for depth in range(k):
            for seg in ranked:
                if depth < len(seg) and seg[depth].chunk.id not in seen and len(hits) < k:
                    seen.add(seg[depth].chunk.id)
                    hits.append(seg[depth])
        return hits

    def _search_segment(
        self,
        query: str,
        k: int,
        min_score: float,
        classifications: Collection[str] | None,
        min_support: float | None,
        allowed: frozenset[str] | None = None,
    ) -> list[RetrievalHit]:
        gate = self.min_support if min_support is None else min_support
        tokens = tokenize(query)
        scores = self._index.scores(tokens)
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
        hits: list[RetrievalHit] = []
        for i in order:
            if scores[i] < min_score or scores[i] <= 0.0 or len(hits) >= k:
                break
            if classifications is not None and self.chunks[i].classification not in classifications:
                continue
            if allowed is not None and self.chunks[i].document_id not in allowed:
                continue
            hit = RetrievalHit(self.chunks[i], scores[i], self._index.coverage(tokens, i))
            if hit.support < gate:
                continue
            hits.append(hit)
        return hits

    def in_force(self, as_of: str | None = None) -> frozenset[str]:
        """Ids of the documents in force on ``as_of`` (ISO date; default today, UTC).

        A document is in force when its ``effective_date`` is on or before ``as_of`` (a document
        without an effective date is treated as always in force) and no other version with the same
        ``doc_key`` is in force with a later effective date (ties broken by version string).
        """
        day = (as_of or datetime.now(UTC).date().isoformat())[:10]
        best: dict[str, Document] = {}
        for d in self.documents.values():
            eff = d.effective_date[:10]
            if eff and eff > day:
                continue
            cur = best.get(d.doc_key)
            if cur is None or (eff, d.version) > (cur.effective_date[:10], cur.version):
                best[d.doc_key] = d
        return frozenset(d.id for d in best.values())

    def get(self, document_id: str) -> Document | None:
        return self.documents.get(document_id)

    def titles(self) -> list[dict[str, str]]:
        return [
            {
                "id": d.id,
                "title": d.title,
                "filename": d.filename,
                "version": d.version,
                "effective_date": d.effective_date,
                "classification": d.classification,
            }
            for d in self.documents.values()
        ]


def build_knowledge_base(
    directory: str | Path | None = None, chunking: ChunkingStrategy = DEFAULT_CHUNKING
) -> KnowledgeBase:
    path = Path(directory) if directory else DEFAULT_KNOWLEDGE_DIR
    docs = load_documents(path) if path.exists() else []
    if not docs:
        log.warning("knowledge base is empty: no *.md documents under %s (set CEAP_KNOWLEDGE_DIR)", path)
    return KnowledgeBase(docs, chunking=chunking)
