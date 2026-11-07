"""The real ``AsyncAnthropic`` over an in-memory ``httpx2.MockTransport`` (PLAN 3A.2).

Nothing leaves the process: the transport records every request body and answers with a canned
``Message`` JSON. Wire-contract tests assert on the exact bytes the SDK would send and parse the
exact JSON the API would return, so an SDK upgrade that changes either fails CI, offline.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2

Responder = Callable[[dict[str, Any]], dict[str, Any] | httpx2.Response]


class RecordingTransport(httpx2.MockTransport):
    def __init__(self, responder: Responder) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self._responder = responder
        super().__init__(self._handle)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append(body)
        self.headers.append({k.lower(): v for k, v in request.headers.items()})
        out = self._responder(body)
        if isinstance(out, httpx2.Response):
            return out
        return httpx2.Response(200, json=out, headers={"request-id": "req_fake_0001"})


def sdk_client(responder: Responder) -> tuple[anthropic.AsyncAnthropic, RecordingTransport]:
    """An ``AsyncAnthropic`` with a dummy key whose HTTP goes to ``responder``; no retries."""
    transport = RecordingTransport(responder)
    client = anthropic.AsyncAnthropic(
        api_key="sk-test-not-a-real-key",
        base_url="https://api.invalid",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=transport),
    )
    return client, transport


def message_json(
    content: list[dict[str, Any]],
    *,
    model: str = "claude-opus-5-5",
    stop_reason: str | None = "end_turn",
    stop_details: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A ``Message`` in the full wire format."""
    return {
        "id": "msg_fake_0001",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "stop_details": stop_details,
        "usage": usage
        or {
            "input_tokens": 1200,
            "output_tokens": 340,
            "cache_creation_input_tokens": 1024,
            "cache_read_input_tokens": 0,
        },
    }
