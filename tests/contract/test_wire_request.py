"""3A.2 / 3A.6: the request the real SDK puts on the wire, captured offline.

The real ``AsyncAnthropic`` sends through an in-memory transport (``fakes/transport.py``); the
JSON body is asserted rule by rule and compared to a committed, reviewed snapshot. Prompt caching
is verified here by request *shape* only, never by a live hit rate (D15).
Regenerate the snapshot deliberately with ``CEAP_UPDATE_SNAPSHOTS=1``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("anthropic")

from ceap.llm.client import AnthropicLLMClient  # noqa: E402
from ceap.llm.models import LLMRequest  # noqa: E402
from ceap.llm.pins import load_pins  # noqa: E402
from ceap.llm.prompts import get_prompt, mask_nonces  # noqa: E402
from ceap.llm.schemas import CritiqueOutput, lint_schema, plan_model  # noqa: E402
from fakes.transport import message_json, sdk_client  # noqa: E402

SNAPSHOT = Path(__file__).parent / "snapshots" / "wire_request_planning.json"
SAMPLING = ("temperature", "top_p", "top_k")
CATALOGUE = {
    "execution.get_execution_metrics": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "dataset": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        },
        "required": ["symbol", "start", "end"],
    },
    "knowledge.search_documents": {
        "type": "object",
        "properties": {"query": {"type": "string"}, "k": {"type": "integer", "default": 4}},
        "required": ["query"],
    },
}
PLAN_JSON = {
    "rationale": "r",
    "steps": [
        {"type": "AGENT_CALL", "description": "c", "agent_id": "critic"},
        {"type": "VALIDATION", "description": "v"},
        {"type": "FINALISE", "description": "f"},
    ],
}


def _planning_request() -> LLMRequest:
    prompt = get_prompt("planner")
    return LLMRequest(
        system_prompt=prompt.text,
        messages=[{"role": "user", "content": "Plan: AAPL 2026-09-18 14:00-15:00 vs baseline."}],
        purpose="planning",
        metadata={"prompt": prompt.ref()},
        output_model=plan_model(CATALOGUE, ["market", "critic"]),
        tools=[
            {
                "name": "lookup",
                "description": "read-only lookup",
                "input_schema": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            }
        ],
    )


async def _send(request: LLMRequest, reply_text: str) -> tuple[dict[str, Any], Any]:
    sdk, transport = sdk_client(lambda body: message_json([{"type": "text", "text": reply_text}]))
    out = await AnthropicLLMClient(client=sdk, retry_base_delay_s=0).complete(request)
    assert len(transport.requests) == 1
    return transport.requests[0], out


def _walk_objects(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for v in node.values():
            found += _walk_objects(v)
    elif isinstance(node, list):
        for v in node:
            found += _walk_objects(v)
    return found


async def test_wire_request_rules_for_structured_planning_call():
    """3A.2: every wire rule, asserted on the body the real SDK serialised."""
    body, out = await _send(_planning_request(), json.dumps(PLAN_JSON))
    pins = load_pins()
    pin = pins.for_purpose("planning")
    assert not any(k in body for k in SAMPLING), "sampling parameters are rejected by current models"
    assert body.get("tool_choice") in (None, {"type": "auto"}), "forced tool_choice returns 400"
    assert body["model"] in pins.allowed and body["model"] == pin.model
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == pin.effort
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema" and lint_schema(fmt["schema"]) == []
    assert all(
        t["strict"] is True and t["input_schema"]["additionalProperties"] is False for t in body["tools"]
    )
    assert all(o.get("additionalProperties") is False for o in _walk_objects(fmt["schema"]))
    # thinking headroom: the pinned max_tokens, never a lowballed value
    assert body["max_tokens"] == pin.max_tokens >= 16000
    # caching: exactly one breakpoint, on the last stable block (system), none in volatile messages
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in json.dumps(body["messages"]) + json.dumps(body["tools"])
    assert out.parsed == PLAN_JSON


async def test_wire_request_snapshot_is_committed_and_unchanged():
    """3A.2: the planning request body equals the reviewed snapshot (SDK or prompt drift fails)."""
    body, _ = await _send(_planning_request(), json.dumps(PLAN_JSON))
    body = json.loads(mask_nonces(json.dumps(body)))  # A8: data-block nonces are random per call
    if os.environ.get("CEAP_UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert SNAPSHOT.exists(), "run once with CEAP_UPDATE_SNAPSHOTS=1, review and commit the snapshot"
    assert json.loads(SNAPSHOT.read_text(encoding="utf-8")) == body


@pytest.mark.parametrize("purpose", ["narrative", "critique", "judge"])
async def test_wire_request_rules_for_every_purpose(purpose):
    """3A.2: unstructured and critique calls keep the same rules; narrative has no output format."""
    pin = load_pins().for_purpose(purpose)
    output_model = CritiqueOutput if purpose == "critique" else None
    reply = json.dumps({"assessments": [], "overall": "ok"}) if output_model else "prose"
    body, out = await _send(
        LLMRequest("sys", [{"role": "user", "content": "x"}], purpose=purpose, output_model=output_model),
        reply,
    )
    assert not any(k in body for k in SAMPLING) and "tool_choice" not in body
    assert body["model"] == pin.model and body["max_tokens"] == pin.max_tokens
    assert ("format" in body["output_config"]) is (output_model is not None)
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}


async def test_wire_schema_invalid_reply_becomes_repairable_not_a_crash():
    """3A.1 through the real SDK: parse() rejects the reply; the adapter returns parse errors."""
    _, out = await _send(_planning_request(), json.dumps({"rationale": "r", "steps": [{"type": "MUTATE"}]}))
    assert out.parsed is None and out.stop_reason == "schema_invalid" and out.parse_errors
