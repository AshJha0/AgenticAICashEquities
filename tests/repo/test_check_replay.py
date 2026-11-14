"""T3 (R-01): the CI replay gate fails on an edited result, an unused or a missing interaction."""

from __future__ import annotations

import importlib.util
import json

from repo_root import ROOT

from ceap.llm.client import LLMRequest
from ceap.llm.ollama import (
    LocalCassette,
    LocalInteraction,
    LocalProvenance,
    chat_body,
    local_request_sha256,
    write_local_cassette,
)

spec = importlib.util.spec_from_file_location("check_replay", ROOT / "scripts" / "check_replay.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

PROV = LocalProvenance(model="m:1", seed=1, temperature=0.7)


def _req(q: str) -> LLMRequest:
    return LLMRequest(system_prompt="s", messages=[{"role": "user", "content": q}], purpose="narrative")


def _hit(q: str, text: str, commit: str = "abc") -> LocalInteraction:
    sha = local_request_sha256(chat_body(_req(q), PROV.model, PROV.seed, PROV.temperature), "narrative")
    resp = {"content": text, "parsed": None, "parse_errors": [], "input_tokens": 1, "output_tokens": 1,
            "model": PROV.model, "stop_reason": "stop"}
    return LocalInteraction(purpose="narrative", request_sha256=sha, response=resp, latency_ms=5.0, commit=commit)


async def _scorer(client):
    k = 0
    for q in ("a", "b"):
        k += (await client.complete(_req(q))).content == "yes"
    return {"metric": {"k": k, "n": 2, "rate": k / 2}, "ok": k == 2}


def _setup(tmp_path, interactions, result=None):
    res, cas = tmp_path / "res", tmp_path / "cas"
    res.mkdir()
    write_local_cassette(cas / "m_1_eval_s1.json", LocalCassette(provenance=PROV, interactions=interactions))
    doc = result or {"metric": {"k": 2, "n": 2, "rate": 1.0}, "ok": True, "note": "text is not checked"}
    (res / "m_1_s1.json").write_text(json.dumps(doc), encoding="utf-8")
    return lambda: gate.main(res, cas, _scorer)


def test_t3_clean_replay_passes(tmp_path):
    assert _setup(tmp_path, [_hit("a", "yes"), _hit("b", "yes")])() == 0


def test_t3_deliberately_edited_result_fails(tmp_path, capsys):
    run = _setup(tmp_path, [_hit("a", "yes"), _hit("b", "yes")],
                 {"metric": {"k": 2, "n": 2, "rate": 0.95}, "ok": True})
    assert run() == 1
    assert "/metric/rate: published 0.95, replay 1.0" in capsys.readouterr().out


def test_t3_unused_interaction_fails(tmp_path, capsys):
    assert _setup(tmp_path, [_hit("a", "yes"), _hit("b", "yes"), _hit("c", "yes")])() == 1
    assert "1 recorded interaction(s) never replayed" in capsys.readouterr().out


def test_t3_missing_interaction_fails(tmp_path, capsys):
    assert _setup(tmp_path, [_hit("a", "yes")])() == 1
    assert "replay raised CassetteStaleError" in capsys.readouterr().out


def test_t3_pre_manifest_cassette_is_named_not_gated(tmp_path, capsys):
    run = _setup(tmp_path, [_hit("a", "yes", ""), _hit("b", "yes", "")], {"metric": {"rate": 0.1}})
    assert run() == 0
    assert "not gated (pre-0.10.0, awaiting re-record)" in capsys.readouterr().out


def test_t3_repository_replay_gate_passes():
    assert gate.main() == 0
