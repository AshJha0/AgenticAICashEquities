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
