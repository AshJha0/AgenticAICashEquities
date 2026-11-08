"""Tokenisation shared by the BM25 index and the retrieval eval.

Lexical only, on purpose (decision D6): lower-case word tokens, a small stop list and a plural-``s``
fold. Deterministic and dependency-free.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")
_STOP = frozenset(
    "the a an and or of to in on for with by is are was were be been this that it as at from our we you "
    "your their its not no if than then into over under between during about what which when how why who "
    "does do did can must should there these those my me i".split()
)


def _fold(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lower-case, drop stop words and one-letter tokens, fold plurals; order preserved."""
    return [_fold(t) for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]
