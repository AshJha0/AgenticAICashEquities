"""Redaction of secrets and personal data before anything is logged, stored or sent to a model.

Patterns are deliberately broad: a false positive costs a ``[REDACTED:...]`` token in a log line, a
false negative leaks a key. ``redact`` is idempotent. ``scrub_cassette`` removes request metadata
(API keys, request ids, organisation ids) from a recorded HTTP interaction before it is committed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

__all__ = ["redact", "redact_obj", "scrub_headers", "scrub_cassette", "SENSITIVE_HEADERS"]

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("API_KEY", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("AWS_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")),
    # account-like numbers: 8 to 19 contiguous digits, or card-style groups of four
    ("ACCOUNT", re.compile(r"(?<![\w.:-])(?:\d{8,19}|\d{4}(?:[ -]\d{4}){2,4})(?![\w.:-])")),
)

SENSITIVE_HEADERS = frozenset(
    {
        "x-api-key",
        "authorization",
        "anthropic-organization-id",
        "openai-organization",
        "request-id",
        "x-request-id",
        "cf-ray",
        "set-cookie",
        "cookie",
    }
)


def redact(text: str) -> str:
    """Replace secrets and personal identifiers in ``text`` with ``[REDACTED:KIND]``."""
    for kind, pattern in _PATTERNS:
        text = pattern.sub(f"[REDACTED:{kind}]", text)
    return text


def redact_obj(value: Any) -> Any:
    """Apply :func:`redact` to every string inside nested dicts, lists and tuples."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {k: redact_obj(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(redact_obj(v) for v in value)
    return value


def scrub_headers(headers: Mapping[str, Any]) -> dict[str, Any]:
    return {k: ("[SCRUBBED]" if k.lower() in SENSITIVE_HEADERS else v) for k, v in headers.items()}


_ID_KEYS = frozenset({"request_id", "id", "organization_id", "org_id"})


def scrub_cassette(interaction: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of a recorded request/response with keys, ids and personal data removed."""
    out: dict[str, Any] = {}
    for section, body in interaction.items():
        if isinstance(body, Mapping):
            sec = dict(body)
            if isinstance(sec.get("headers"), Mapping):
                sec["headers"] = scrub_headers(sec["headers"])
            for key in list(sec):
                if key in _ID_KEYS and isinstance(sec[key], str):
                    sec[key] = "[SCRUBBED]"
            if "body" in sec:
                sec["body"] = redact_obj(sec["body"])
            out[section] = sec
        else:
            out[section] = redact_obj(body)
    return out
