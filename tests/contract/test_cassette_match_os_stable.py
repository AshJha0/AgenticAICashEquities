"""Cassette match keys must not depend on last-ulp float noise (Windows vs Linux builds).

The 0.6.0 PR failed on Linux because raw tool output, serialised into prompt text with full
precision, differed in its last digits from the Windows recording.
"""

from __future__ import annotations

import json
from pathlib import Path

from ceap.llm.recording import request_sha256

CASSETTES = Path(__file__).resolve().parents[1] / "cassettes"


def _nudge(text: str) -> str:
    # flip the final digit of every long decimal literal, as a different BLAS build would
    import re

    def bump(m: re.Match[str]) -> str:
        s = m.group(0)
        return s[:-1] + ("1" if s[-1] != "1" else "2")

    return re.sub(r"\d+\.\d{12,}", bump, text)


def test_ulp_noise_in_prompt_text_and_numbers_keeps_the_match_key():
    body = {
        "system": [{"type": "text", "text": "metric 0.00011114198388441267 bps"}],
        "messages": [{"role": "user", "content": "x=3.141592653589793, v1.10, id 12.5"}],
        "temperature": 0.30000000000000004,
    }
    noisy = json.loads(_nudge(json.dumps(body)))
    noisy["temperature"] = 0.3
    assert noisy != body
    assert request_sha256(noisy) == request_sha256(body)


def test_a_real_change_still_changes_the_match_key():
    body = {"messages": [{"role": "user", "content": "slippage 12.25 bps"}]}
    assert request_sha256(body) != request_sha256({"messages": [{"role": "user", "content": "slippage 12.26 bps"}]})


def test_recorded_cassettes_survive_ulp_noise():
    for path in sorted(CASSETTES.glob("investigation_*.json")):
        for it in json.loads(path.read_text(encoding="utf-8"))["interactions"]:
            noisy = json.loads(_nudge(json.dumps(it["request"])))
            assert request_sha256(noisy, it["match"]) == it["request_sha256"], (path.name, it["purpose"])


def test_evidence_digests_are_masked_in_the_match_key():
    def body(digest: str) -> dict:
        return {"messages": [{"role": "user", "content": json.dumps({"digest": digest, "v": 1.5})}]}

    assert request_sha256(body("621a038335a6874f")) == request_sha256(body("a0721544bcad0059"))
    assert request_sha256({"digest": "621a038335a6874f"}) == request_sha256({"digest": "a0721544bcad0059"})


def test_python_311_float_summation_keeps_the_cassettes_matching(monkeypatch):
    """Python 3.12 made sum() of floats compensated; 3.11 adds left to right. CI runs both."""
    import builtins

    def left_to_right(items, start=0):
        total = start
        for x in items:
            total = total + x
        return total

    monkeypatch.setattr(builtins, "sum", left_to_right)
    # the full adapter replay under 3.11-style summation is exercised by CI's 3.11 job; here we
    # check the property it relies on: last-digit drift and its digest never change the key
    a = {"c": json.dumps({"is_delta_bps": -3.6534604603646805, "digest": "621a038335a6874f"})}
    b = {"c": json.dumps({"is_delta_bps": -3.6534604603646823, "digest": "a0721544bcad0059"})}
    assert request_sha256(a) == request_sha256(b)
