"""3A.2: responses in the full wire format, validated by the pinned SDK's own ``Message`` type.

Each response is hand-built JSON in the shape the API returns (thinking, redacted_thinking,
tool_use, citations of both location types, refusal with ``stop_details``, cache usage). It is
validated with ``anthropic.types.Message.model_validate`` *and* sent through the real client and
our adapter, so SDK schema drift fails CI offline.
"""

from __future__ import annotations

import pytest

anthropic = pytest.importorskip("anthropic")

from anthropic.types import (  # noqa: E402
    CitationCharLocation,
    CitationPageLocation,
    Message,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

from ceap.llm.client import AnthropicLLMClient  # noqa: E402
from ceap.llm.models import LLMRequest  # noqa: E402
from fakes.transport import message_json, sdk_client  # noqa: E402

FULL = message_json(
    [
        {"type": "thinking", "thinking": "", "signature": "c2ln"},
        {"type": "redacted_thinking", "data": "ZW5jcnlwdGVk"},
        {
            "type": "text",
            "text": "Spread widened.",
            "citations": [
                {
                    "type": "char_location",
                    "cited_text": "spread",
                    "document_index": 0,
                    "document_title": "Runbook",
                    "start_char_index": 10,
                    "end_char_index": 16,
                    "file_id": None,
                },
                {
                    "type": "page_location",
                    "cited_text": "policy",
                    "document_index": 1,
                    "document_title": "Policy.pdf",
                    "start_page_number": 2,
                    "end_page_number": 3,
                    "file_id": None,
                },
            ],
        },
        {"type": "tool_use", "id": "toolu_01", "name": "lookup", "input": {"q": "AAPL"}, "caller": None},
    ],
    stop_reason="tool_use",
    usage={
        "input_tokens": 50,
        "output_tokens": 70,
        "cache_creation_input_tokens": 1024,
        "cache_read_input_tokens": 4096,
    },
)
REFUSAL = message_json(
    [],
    stop_reason="refusal",
    stop_details={"type": "refusal", "category": "cyber", "explanation": "declined"},
)


def test_wire_response_full_message_validates_against_pinned_sdk_types():
    msg = Message.model_validate(FULL)
    kinds = [type(b) for b in msg.content]
    assert kinds == [ThinkingBlock, RedactedThinkingBlock, TextBlock, ToolUseBlock]
    cites = msg.content[2].citations
    assert isinstance(cites[0], CitationCharLocation) and isinstance(cites[1], CitationPageLocation)
    assert msg.usage.cache_read_input_tokens == 4096 and msg.usage.cache_creation_input_tokens == 1024
    refusal = Message.model_validate(REFUSAL)
    assert refusal.stop_reason == "refusal" and refusal.stop_details.category == "cyber"


async def test_wire_response_mapping_through_real_client():
    sdk, _ = sdk_client(lambda body: FULL)
    out = await AnthropicLLMClient(client=sdk).complete(LLMRequest("s", [{"role": "user", "content": "x"}]))
    assert out.content == "Spread widened."  # thinking and redacted_thinking are never surfaced
    assert out.tool_calls == [{"id": "toolu_01", "name": "lookup", "arguments": {"q": "AAPL"}}]
    assert (out.cache_read_tokens, out.cache_write_tokens) == (4096, 1024)
    assert out.request_id == "req_fake_0001" and out.stop_reason == "tool_use"


async def test_wire_response_refusal_carries_stop_details():
    sdk, _ = sdk_client(lambda body: REFUSAL)
    out = await AnthropicLLMClient(client=sdk).complete(LLMRequest("s", [{"role": "user", "content": "x"}]))
    assert out.stop_reason == "refusal"
    assert out.stop_details == {"type": "refusal", "category": "cyber", "explanation": "declined"}
