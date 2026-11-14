"""Redaction of secrets and personal data before anything is logged, stored or sent to a model.

Besides fixed key formats it catches ``name=value`` / ``name: value`` pairs whose name marks a
secret (``password``, ``api_key``, ``ANTHROPIC_API_KEY`` and other ``*_KEY`` / ``*_TOKEN`` env names),
phone numbers, and opaque high-entropy tokens (24+ characters, letters and digits, at least
``TOKEN_ENTROPY_BITS`` bits per character). Hex digests of 32, 40 or 64 characters are kept: they are
the platform's own content hashes, not secrets.

Patterns are deliberately broad: a false positive costs a ``[REDACTED:...]`` token in a log line, a
false negative leaks a key. ``redact`` is idempotent. ``scrub_cassette`` removes request metadata
(API keys, request ids, organisation ids) from a recorded HTTP interaction before it is committed.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any

__all__ = ["redact", "redact_obj", "scrub_headers", "scrub_cassette", "SENSITIVE_HEADERS", "shannon_entropy"]

# Names that mark the value after them as a secret: ``password=...``, ``api_key: ...``, ``"token": "..."``,
# and environment-style names such as ``ANTHROPIC_API_KEY=...`` or ``CEAP_AUDIT_HMAC_KEY=...``.
_SECRET_NAME = (
    r"(?:[A-Za-z0-9]+_)*(?:api[_-]?key|apikey|secret(?:[_-]?key)?|password|passwd|pwd|token|"
    r"access[_-]?key|private[_-]?key|client[_-]?secret|hmac[_-]?key|auth|credentials?)"
)
_KEY_VALUE = re.compile(
    rf"(?i)(?P<name>[\"']?(?<![A-Za-z0-9_]){_SECRET_NAME}(?![A-Za-z0-9_])[\"']?)(?P<sep>\s*[:=]\s*)"
    r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^\s,;&'\"}\]]+)"
)
# phone numbers: +44 20 7946 0958, (020) 7946 0958, 020 7946 0958, +1-212-555-0100; 9+ digits in total
_PHONE = re.compile(r"(?<![\w.:/-])(?:\+\d{1,3}|\(0\d{1,4}\)|0\d{1,4})(?:[ .-]?\d{2,5}){2,5}(?![\w:/-]|\.\d)")
# candidate opaque tokens: long runs of base64 / hex / url-safe characters
_TOKEN = re.compile(r"(?<![A-Za-z0-9_+/=-])[A-Za-z0-9_+/=-]{24,}(?![A-Za-z0-9_+/=-])")
_HEX_DIGEST = re.compile(r"\A[0-9a-f]{32}\Z|\A[0-9a-f]{40}\Z|\A[0-9a-f]{64}\Z")
TOKEN_ENTROPY_BITS = 4.0  # bits per character; English words and identifiers sit well below this


def shannon_entropy(s: str) -> float:
    """Shannon entropy of ``s`` in bits per character."""
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _redact_key_value(m: re.Match[str]) -> str:
    value = m.group("value")
    if value.startswith("[REDACTED"):
        return m.group(0)
    quote = value[0] if value[:1] in "\"'" else ""
    return f"{m.group('name')}{m.group('sep')}{quote}[REDACTED:SECRET]{quote}"


def _redact_phone(m: re.Match[str]) -> str:
    text = m.group(0)
    digits = sum(ch.isdigit() for ch in text)
    # dates (2026-11-09), times and short numeric codes are not phone numbers
    if digits < 9 or re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return "[REDACTED:PHONE]"


def _redact_token(m: re.Match[str]) -> str:
    text = m.group(0)
    # Content hashes (sha256 hex digests, git shas) are the platform's own audit identifiers, not secrets.
    if _HEX_DIGEST.match(text) or text.startswith("[REDACTED"):
        return text
    # readable identifiers (PAPER-STG-1a2b3c4d5e-001, snake_case names) are short segments joined by - or _
    if all(len(seg) <= 12 for seg in re.split(r"[-_]", text)):
        return text
    has_digit = any(ch.isdigit() for ch in text)
    has_alpha = any(ch.isalpha() for ch in text)
    if has_digit and has_alpha and shannon_entropy(text) >= TOKEN_ENTROPY_BITS:
        return "[REDACTED:TOKEN]"
    return text


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("API_KEY", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("AWS_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")),
    # Account-like numbers: card-style groups of four, or 8 to 19 contiguous digits only after an
    # account word. A bare long number is usually a quantity, notional or order id in a trading
    # question ("Buy 12000000 shares") and must survive (0.10.0 E7, finding E-09).
    ("ACCOUNT", re.compile(
        r"(?<![\w.:-])\d{4}(?:[ -]\d{4}){2,4}(?![\w.:-])"
        r"|(?i:(?<=account )|(?<=account: )|(?<=account no )|(?<=acct )|(?<=card )|(?<=a/c ))\d{8,19}(?![\w.:-])"
    )),
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
    text = _KEY_VALUE.sub(_redact_key_value, text)
    for kind, pattern in _PATTERNS:
        text = pattern.sub(f"[REDACTED:{kind}]", text)
    text = _PHONE.sub(_redact_phone, text)
    return _TOKEN.sub(_redact_token, text)


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
