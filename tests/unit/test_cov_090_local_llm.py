"""Coverage for the 0.9.0 local-model adapter and its cassettes (``ceap.llm.ollama``).

No Ollama: every live call goes through ``httpx.MockTransport``; ``model_digest`` is pointed at a
monkeypatched ``httpx.get``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from ceap.eval import judge
from ceap.llm import ollama
from ceap.llm.models import LLMRequest
from ceap.llm.ollama import (
    PRIMARY_LOCAL_MODEL,
    LocalCassette,
    LocalCassetteClient,
    LocalProvenance,
    OllamaLLMClient,
    _text,
    _validate,
    chat_body,
    load_local_cassette,
    local_request_sha256,
    model_digest,
    write_local_cassette,
)


def _transport(content: str, calls: list[int]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            200, json={"message": {"content": content}, "prompt_eval_count": 4, "eval_count": 2}
        )

    return httpx.MockTransport(handler)


def _plain(text: str = "hello") -> LLMRequest:
    return LLMRequest(system_prompt="sys", messages=[{"role": "user", "content": text}], purpose="general")


def test_text_flattens_every_block_kind():
    blocks = [
        {"type": "text", "text": "a"},
        "raw",  # not a dict: str()
        {"type": "document", "title": "T", "source": {"content": [1, 2]}},  # non-string data: JSON
        {"type": "image", "x": 1},  # unknown block: dumped as JSON
    ]
    assert _text(blocks) == (
        'a\n\nraw\n\n<document title=\'T\'>\n[1, 2]\n</document>\n\n{"type": "image", "x": 1}'
    )
    assert _text({"type": "text", "text": "solo"}) == "solo"  # a single block, not a list
    assert _text("plain") == "plain"


def test_chat_body_without_output_model_has_no_format_and_no_json_suffix():
    body = chat_body(_plain(), PRIMARY_LOCAL_MODEL, seed=2, temperature=0.5)
    assert "format" not in body
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["options"] == {"seed": 2, "temperature": 0.5, "num_ctx": 8192}
    # the purpose is part of the match key: same body, different purpose, different hash
    assert local_request_sha256(body, "a") != local_request_sha256(body, "b")


def test_validate_paths():
    assert _validate(_plain(), "anything") == (None, ())
    req = judge.judge_request({"symbol": "AAPL"}, "n")
    parsed, errors = _validate(req, "not json")
    assert parsed is None and errors[0].startswith("not JSON")
    # valid JSON, wrong shape: field-located schema errors
    parsed, errors = _validate(req, json.dumps({"faithful": "maybe"}))
    assert parsed is None and any(e.startswith("faithful:") for e in errors)


async def test_plain_reply_has_no_parsed_and_end_turn():
    calls: list[int] = []
    client = OllamaLLMClient(transport=_transport("free text", calls))
    r = await client.complete(_plain())
    assert (r.content, r.parsed, r.parse_errors, r.stop_reason) == ("free text", None, (), "end_turn")
    assert client.name == f"ollama:{PRIMARY_LOCAL_MODEL}"
    assert [p for p, _ in client.latencies] == ["general"]


async def test_checkpoint_written_each_call_and_resumed_without_a_second_post(tmp_path):
    path = tmp_path / "cas" / "run.json"
    calls: list[int] = []
    cas = LocalCassette(provenance=LocalProvenance(model=PRIMARY_LOCAL_MODEL, seed=5, temperature=0.0))
    first = OllamaLLMClient(seed=5, transport=_transport("one", calls), recorder=cas, checkpoint=path)
    await first.complete(_plain())
    assert calls == [1] and path.exists()
    assert load_local_cassette(path).interactions[0].response["content"] == "one"

    # restart with the same (model, seed, temperature): the recorded answer comes from the file
    cas2 = LocalCassette(provenance=LocalProvenance(model=PRIMARY_LOCAL_MODEL, seed=5, temperature=0.0))
    second = OllamaLLMClient(seed=5, transport=_transport("two", calls), recorder=cas2, checkpoint=path)
    assert len(cas2.interactions) == 1 and len(second.latencies) == 1
    r = await second.complete(_plain())
    assert r.content == "one" and calls == [1]

    # a different seed does not reuse the old recordings
    cas3 = LocalCassette(provenance=LocalProvenance(model=PRIMARY_LOCAL_MODEL, seed=6, temperature=0.0))
    third = OllamaLLMClient(seed=6, transport=_transport("three", calls), recorder=cas3, checkpoint=path)
    assert cas3.interactions == []
    assert (await third.complete(_plain())).content == "three" and calls == [1, 1]


async def test_cassette_replay_consumes_duplicates_in_order_then_repeats_the_last(tmp_path):
    body = chat_body(_plain(), PRIMARY_LOCAL_MODEL, 0, 0.0)
    sha = local_request_sha256(body, "general")
    resp = {"input_tokens": 1, "output_tokens": 1, "model": PRIMARY_LOCAL_MODEL, "parse_errors": []}
    cas = LocalCassette(
        provenance=LocalProvenance(model=PRIMARY_LOCAL_MODEL, seed=0, temperature=0.0),
        interactions=[
            {
                "purpose": "general",
                "request_sha256": sha,
                "response": {**resp, "content": "x"},
                "latency_ms": 3.0,
            },
            {
                "purpose": "general",
                "request_sha256": sha,
                "response": {**resp, "content": "y"},
                "latency_ms": 4.0,
            },
        ],
    )
    path = tmp_path / "c.json"
    write_local_cassette(path, cas)
    client = LocalCassetteClient(load_local_cassette(path))
    assert client.name == f"cassette:{PRIMARY_LOCAL_MODEL}"
    got = [(await client.complete(_plain())).content for _ in range(3)]
    assert got == ["x", "y", "y"]
    assert client.latencies == [("general", 3.0), ("general", 4.0), ("general", 4.0)]


def test_model_digest_found_missing_and_unreachable(monkeypatch):
    class R:
        def json(self) -> dict:
            return {
                "models": [{"name": "other", "digest": "d0"}, {"name": PRIMARY_LOCAL_MODEL, "digest": "d1"}]
            }

    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    assert model_digest(PRIMARY_LOCAL_MODEL) == "d1"
    assert model_digest("absent:1b") == ""

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", boom)
    assert model_digest(PRIMARY_LOCAL_MODEL) == ""


@pytest.mark.parametrize(
    ("value", "on"), [("1", True), (" TRUE ", True), ("yes", True), ("0", False), ("", False)]
)
def test_live_gate_values(monkeypatch, value, on):
    monkeypatch.setenv(ollama.LOCAL_MODEL_ENV, value)
    assert ollama.live_enabled() is on
