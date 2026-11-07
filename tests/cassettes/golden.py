"""Hand-built cassettes for the ten scenario templates (PLAN 3A.5, 3A.8).

``provenance: hand_built``. The replies are authored by :func:`golden_responder`, which writes the
full wire format (an empty ``thinking`` block with a signature, a ``redacted_thinking`` block, then
the text) from the golden references: the reference plan for the task parameters, and a critique
that keeps the confidence of every finding whose evidence resolves and cuts the rest to 0.1.
The 3E.1 golden set does not exist yet; when it lands, these replies are re-authored from it.
None of this measures a model: :func:`ceap.llm.recording.assert_publishable` refuses it.

Regenerate deliberately (and review the diff) with::

    CEAP_UPDATE_CASSETTES=1 python -m pytest tests/contract/test_adapter_differential.py
"""

from __future__ import annotations

import itertools
import json
import uuid
from pathlib import Path
from typing import Any

from ceap.llm.client import canonical_plan
from ceap.llm.recording import purpose_of

CASSETTE_DIR = Path(__file__).parent
TEMPLATES = tuple(f"T{i:02d}" for i in range(1, 11))
NOTE = (
    "hand-built from the reference plan and critique rule; 3E.1 golden set pending; NOT a model measurement"
)


def cassette_path(template: str) -> Path:
    return CASSETTE_DIR / f"investigation_{template}.json"


def deterministic_uuid4(seed: int = 0) -> Any:
    """A ``uuid.uuid4`` replacement yielding a fixed sequence (ids repeat run to run)."""
    counter = itertools.count(seed)
    return lambda: uuid.UUID(int=(next(counter) * 0x9E3779B97F4A7C15) % (1 << 128))


def _payload_after(text: str, marker: str) -> dict[str, Any]:
    start = text.find(marker)
    if start < 0:
        return {}
    brace = text.find("{", start)
    value, _ = json.JSONDecoder().raw_decode(text, brace)
    return value if isinstance(value, dict) else {}


def _message(text: str, model: str) -> dict[str, Any]:
    return {
        "id": "msg_hand_built",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [
            {"type": "thinking", "thinking": "", "signature": "c2lnbmF0dXJlLWhhbmQtYnVpbHQ="},
            {"type": "redacted_thinking", "data": "cmVkYWN0ZWQtaGFuZC1idWlsdA=="},
            {"type": "text", "text": text},
        ],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "stop_details": None,
        "usage": {
            "input_tokens": 2100,
            "output_tokens": 900,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 1800,
        },
    }


def golden_responder(body: dict[str, Any]) -> dict[str, Any]:
    purpose = purpose_of(body)
    user = body["messages"][0]["content"]
    model = body["model"]
    if purpose in ("planner", "research_planner"):
        return _message(json.dumps(canonical_plan(_payload_after(user, "PARAMETERS:"))), model)
    if purpose == "critic":
        findings = json.loads(user).get("findings", [])
        assessments = []
        for f in findings:
            supported = bool(f.get("supporting_evidence")) and f.get("evidence_resolved", True)
            assessments.append(
                {
                    "finding_id": f["id"],
                    "supported": supported,
                    "adjusted_confidence": f.get("confidence", 0.5) if supported else 0.1,
                    "comment": "hand-built: evidence resolves"
                    if supported
                    else "hand-built: evidence missing",
                }
            )
        return _message(json.dumps({"assessments": assessments, "overall": "Hand-built critique."}), model)
    return _message("EXECUTIVE SUMMARY\n\nHand-built narrative (adapter equivalence only).", model)
