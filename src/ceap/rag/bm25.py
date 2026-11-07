"""Okapi BM25 over pre-tokenised documents; stdlib only (no ``rank-bm25`` dependency).

Scores are deterministic: ties are broken by document position.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence


class BM25:
    """Okapi BM25 with the Lucene IDF, ``log(1 + (N - df + 0.5) / (df + 0.5))``, always positive."""

    def __init__(self, corpus: Sequence[Sequence[str]], k1: float = 1.2, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._tf = [Counter(doc) for doc in corpus]
        self._len = [len(doc) for doc in corpus]
        n = len(corpus)
        self._avg = (sum(self._len) / n) if n else 0.0
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        self.idf = {t: math.log(1.0 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        # IDF a term would have if it appeared in no document: the weight of an unknown query term.
        self.max_idf = math.log(1.0 + (n + 0.5) / 0.5) if n else 0.0

    def __len__(self) -> int:
        return len(self._tf)

    def scores(self, query: Sequence[str]) -> list[float]:
        """BM25 score of every document for ``query`` (repeated query terms count once)."""
        terms = [t for t in dict.fromkeys(query) if t in self.idf]
        out = [0.0] * len(self._tf)
        if not terms or not self._avg:
            return out
        for i, tf in enumerate(self._tf):
            norm = self.k1 * (1.0 - self.b + self.b * self._len[i] / self._avg)
            s = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1.0) / (f + norm)
            out[i] = s
        return out

    def coverage(self, query: Sequence[str], index: int) -> float:
        """Share of the query's IDF mass that document ``index`` contains, in [0, 1].

        Unknown terms weigh ``max_idf``, so a query that is mostly about things the corpus never
        mentions ("who won the oscar for best picture") has low coverage even when one common word
        ("best") matches. This is the "nothing relevant" gate used for negative queries.
        """
        terms = list(dict.fromkeys(query))
        total = sum(self.idf.get(t, self.max_idf) for t in terms)
        if not total:
            return 0.0
        tf = self._tf[index]
        return sum(self.idf[t] for t in terms if tf.get(t, 0)) / total
