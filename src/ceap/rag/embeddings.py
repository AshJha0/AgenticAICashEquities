"""Embedding interface with a dependency-free default.

``HashingEmbedder`` produces sparse-ish hashed TF-IDF vectors (feature
hashing, sublinear TF, corpus IDF, L2-normalised). It is deterministic,
needs no model download and is good enough for a few dozen runbooks.
Swap in a sentence-transformer or API embedder by implementing
:class:`Embedder`; the retriever does not care.
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Sequence

import numpy as np

_TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")
_STOP = frozenset(
    "the a an and or of to in on for with by is are was were be been this that it as at from our we you your their its not no if than then into over under between during about".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


class Embedder(ABC):
    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @abstractmethod
    def fit(self, corpus: Sequence[str]) -> None: ...

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


class HashingEmbedder(Embedder):
    def __init__(self, dimension: int = 4096) -> None:
        self._dim = dimension
        self._idf: dict[str, float] = {}
        self._default_idf = 1.0

    @property
    def dimension(self) -> int:
        return self._dim

    def _index(self, token: str) -> int:
        return int(hashlib.blake2b(token.encode(), digest_size=4).hexdigest(), 16) % self._dim

    def fit(self, corpus: Sequence[str]) -> None:
        n = max(1, len(corpus))
        df: Counter[str] = Counter()
        for doc in corpus:
            df.update(set(tokenize(doc)))
        self._idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}
        self._default_idf = math.log((1 + n) / 1) + 1.0

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self._dim), dtype=np.float32)
        for i, text in enumerate(texts):
            counts = Counter(tokenize(text))
            for tok, c in counts.items():
                out[i, self._index(tok)] += (1.0 + math.log(c)) * self._idf.get(tok, self._default_idf)
            norm = np.linalg.norm(out[i])
            if norm > 0:
                out[i] /= norm
        return out
