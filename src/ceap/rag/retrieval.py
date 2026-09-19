"""Vector retrieval over chunked enterprise documents."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ceap.rag.embeddings import Embedder, HashingEmbedder
from ceap.rag.ingestion import Chunk, Document, chunk_document, load_documents

DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "data" / "reference" / "knowledge"
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalHit:
    chunk: Chunk
    score: float

    def to_dict(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk.id,
            "document_id": self.chunk.document_id,
            "title": self.chunk.title,
            "section": self.chunk.section,
            "score": round(self.score, 4),
            "text": self.chunk.text,
        }


class KnowledgeBase:
    def __init__(self, documents: list[Document], embedder: Embedder | None = None) -> None:
        self.documents = {d.id: d for d in documents}
        self.chunks: list[Chunk] = [c for d in documents for c in chunk_document(d)]
        self.embedder = embedder or HashingEmbedder()
        texts = [f"{c.title}. {c.section}. {c.text}" for c in self.chunks]
        self.embedder.fit(texts)
        self._matrix = (
            self.embedder.embed(texts) if texts else np.zeros((0, self.embedder.dimension), dtype=np.float32)
        )

    def __len__(self) -> int:
        return len(self.chunks)

    def search(self, query: str, k: int = 4, min_score: float = 0.05) -> list[RetrievalHit]:
        if not self.chunks or not query.strip() or k <= 0:
            return []
        k = min(int(k), 50)
        q = self.embedder.embed([query])[0]
        scores = self._matrix @ q
        order = np.argsort(-scores)[:k]
        return [RetrievalHit(self.chunks[i], float(scores[i])) for i in order if scores[i] >= min_score]

    def get(self, document_id: str) -> Document | None:
        return self.documents.get(document_id)

    def titles(self) -> list[dict[str, str]]:
        return [
            {"id": d.id, "title": d.title, "filename": d.metadata.get("filename", "")}
            for d in self.documents.values()
        ]


def build_knowledge_base(directory: str | Path | None = None) -> KnowledgeBase:
    path = Path(directory) if directory else DEFAULT_KNOWLEDGE_DIR
    docs = load_documents(path) if path.exists() else []
    if not docs:
        log.warning("knowledge base is empty: no *.md documents under %s (set CEAP_KNOWLEDGE_DIR)", path)
    return KnowledgeBase(docs)
