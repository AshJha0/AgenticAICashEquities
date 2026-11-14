"""Cassette match keys, provenance and the publishability gate in the unit tier (0.10.0 E1)."""

from __future__ import annotations

import hashlib

import pytest

from ceap.llm import recording as rec
from ceap.llm.prompts import get_prompt


def test_match_key_ignores_last_ulp_noise_nonces_and_digests() -> None:
    a = {
        "model": "m",
        "x": 0.1 + 0.2,
        "text": 'v=0.30000000000000004 <data nonce="0123abcd">',
        "digest": "ab" * 16,
    }
    b = {"model": "m", "x": 0.3, "text": 'v=0.3 <data nonce="ffffffff">', "digest": "cd" * 16}
    assert rec.request_sha256(a) == rec.request_sha256(b)
    assert rec.request_sha256({"x": 0.31}) != rec.request_sha256({"x": 0.3})


def test_round_floats_keeps_short_literals_and_non_finite_values() -> None:
    assert rec._round_floats("cost 1.25 bps") == "cost 1.25 bps"
    assert rec._round_floats("x 1.234567890123") == "x 1.23456789"
    assert rec._round_floats(float("inf")) == float("inf")
    assert rec._round_floats((1.0000000000001, "a")) == [1.0, "a"]
    assert rec._round_floats('{\\"digest\\": \\"0123abcd\\"}') == '{\\"digest\\": \\"<digest>\\"}'


def test_prefix_match_hashes_only_the_cacheable_prefix() -> None:
    a = {"model": "m", "system": "s", "messages": [{"role": "user", "content": "one"}]}
    b = {**a, "messages": [{"role": "user", "content": "two"}]}
    assert rec.request_sha256(a, "prefix") == rec.request_sha256(b, "prefix")
    assert rec.request_sha256(a) != rec.request_sha256(b)


def test_purpose_and_prompt_sha_from_the_system_prompt() -> None:
    text = get_prompt("critic").text
    body = {"system": [{"type": "text", "text": "cached"}, {"type": "text", "text": text}]}
    assert rec.purpose_of(body) == "critic"
    assert rec.purpose_of({"system": "something else"}) == "general"
    assert rec.purpose_of({}) == "general"
    assert rec.prompt_sha_of(body) == hashlib.sha256(text.encode()).hexdigest()


def _interaction(purpose: str = "critic") -> dict:
    body = {"model": "m", "system": get_prompt(purpose).text, "messages": []}
    return {
        "purpose": purpose,
        "request_sha256": rec.request_sha256(body),
        "request": body,
        "response": {"model": "m", "content": [{"type": "text", "text": "ok"}]},
    }


def test_build_write_load_round_trip(tmp_path) -> None:
    cas = rec.build_cassette([_interaction()], provenance="recorded", model="m", sdk="anthropic==1", note="n")
    assert cas.provenance.prompt_sha == {"critic": rec.prompt_sha_of(cas.interactions[0].request)}
    expected = hashlib.sha256(cas.interactions[0].request_sha256.encode()).hexdigest()
    assert cas.provenance.request_sha256 == expected
    path = tmp_path / "c.json"
    rec.write_cassette(path, cas)
    first = path.read_text(encoding="utf-8")
    rec.write_cassette(path, rec.load_cassette(path))
    assert path.read_text(encoding="utf-8") == first  # scrubbing is idempotent
    assert rec.load_cassette(path) == cas


def test_hand_built_cassettes_cannot_publish_model_metrics() -> None:
    hand = rec.build_cassette([_interaction()], provenance="hand_built", model="m", sdk="s", note="fixture")
    with pytest.raises(rec.UnpublishableMetricError, match="fixture"):
        rec.assert_publishable(hand, "planner_validity")
    rec.assert_publishable(
        hand.model_copy(update={"provenance": hand.provenance.model_copy(update={"provenance": "recorded"})}),
        "x",
    )
    assert "interactions" in rec.cassette_schema()["properties"]
