"""Canonical bytes for hashing.

Every hash the platform records (prompt hashes, audit-chain links, content hashes on approvals)
must be the same on Windows and Linux and across Python versions. Hashing ``str.encode()`` of a
file read on Windows gives a different digest from the same file on Linux when git converted line
endings, and ``json.dumps`` of a dict depends on insertion order. This module fixes both:

* text: CRLF and lone CR become LF, encoded as UTF-8;
* JSON: sorted keys, compact separators, ``ensure_ascii=False``, NaN and infinity refused;
* datetimes: timezone-aware values in UTC, ISO 8601 with microseconds and a ``Z`` suffix.
  Naive datetimes are refused because their meaning depends on the host clock.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

__all__ = ["canonical_text", "canonical_json", "canonical_datetime", "sha256_hex", "content_hash"]


def canonical_text(text: str | bytes) -> bytes:
    """Return UTF-8 bytes with every line ending normalised to LF."""
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def canonical_datetime(value: datetime) -> str:
    """ISO 8601 in UTC with microseconds and ``Z``; naive datetimes are rejected."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetime cannot be hashed canonically; attach a timezone")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalise(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float {value!r} cannot be hashed canonically")
        return value
    if isinstance(value, datetime):
        return canonical_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _normalise(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _normalise(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"canonical JSON keys must be str, got {type(k).__name__}")
            out[k] = _normalise(v)
        return out
    if isinstance(value, list | tuple):
        return [_normalise(v) for v in value]
    if isinstance(value, set | frozenset):
        return sorted(_normalise(v) for v in value)
    raise TypeError(f"type {type(value).__name__} has no canonical JSON form")


def canonical_json(value: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, compact, UTF-8, no NaN."""
    text = json.dumps(
        _normalise(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(value: Any) -> str:
    """SHA-256 of the canonical form: text is line-ending normalised, anything else is JSON."""
    if isinstance(value, str | bytes):
        return sha256_hex(canonical_text(value))
    return sha256_hex(canonical_json(value))
