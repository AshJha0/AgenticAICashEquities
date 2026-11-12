"""Unit tests for the replay building blocks in ``ceap.harness.replay``.

The full record-and-replay round trip is a slow test (tests/unit/cli/test_db_replay_cli.py); these pin
the pieces it relies on: the deterministic clock and its injection, the recording and replaying MCP,
model and approval wrappers (including the divergence errors), request round trips, the difference
finder and the bundle version check.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ceap.domain.common import new_id, utc_now
from ceap.harness import replay as rp
from ceap.llm.client import LLMClient
from ceap.llm.models import LLMRequest, LLMResponse
from ceap.mcp.client import MCPClient, MCPError
from ceap.platform import default_request, default_research_request
from ceap.policy.approvals import ApprovalDecision, ApprovalGateway, ApprovalRequest


def test_clock_steps_and_injection_restores() -> None:
    clock = rp.DeterministicClock("2026-01-01T00:00:00+00:00", step_ms=10)
    real_perf = time.perf_counter
    with rp.injected_clock(clock):
        assert utc_now() == datetime(2026, 1, 1, 0, 0, 0, 10_000, tzinfo=UTC)
        assert utc_now() == datetime(2026, 1, 1, 0, 0, 0, 20_000, tzinfo=UTC)
        assert new_id() == "00000001" and new_id() == "00000002"
        assert time.perf_counter() == pytest.approx(0.01)
        assert time.perf_counter() == pytest.approx(0.02)
    assert time.perf_counter is real_perf
    assert utc_now().year >= 2026 and new_id() != "00000003"


class _Inner(MCPClient):
    def __init__(self) -> None:
        self.closed = False

    def servers(self) -> list[str]:
        return ["market"]

    async def discover_tools(self, server: str) -> list[dict[str, Any]]:
        return [{"name": "get_quotes", "when": datetime(2026, 1, 1, tzinfo=UTC)}]

    async def invoke(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        if tool == "bad":
            raise KeyError("nope")
        return {"tool": tool, "n": arguments.get("n")}

    async def close(self) -> None:
        self.closed = True


async def test_recording_mcp_then_replay_mcp() -> None:
    inner = _Inner()
    rec = rp.RecordingMCPClient(inner)
    assert rec.servers() == ["market"]
    tools = await rec.discover_tools("market")
    assert rec.discovery["market"][0]["when"].startswith("2026-01-01")  # stored as JSON text
    assert tools[0]["name"] == "get_quotes"
    assert await rec.invoke("market", "q", {"n": 1}) == {"tool": "q", "n": 1}
    assert await rec.invoke("market", "q", {"n": 2}) == {"tool": "q", "n": 2}
    with pytest.raises(KeyError):
        await rec.invoke("market", "bad", {})
    await rec.close()
    assert inner.closed
    assert rec.calls[-1]["error"].startswith("KeyError")

    play = rp.ReplayMCPClient(rec.discovery, rec.calls + [rec.calls[0] | {"output": {"again": True}}])
    assert play.servers() == ["market"]
    assert (await play.discover_tools("market"))[0]["name"] == "get_quotes"
    # two recordings under one key: consumed in order, then the last is reused
    assert await play.invoke("market", "q", {"n": 1}) == {"tool": "q", "n": 1}
    assert await play.invoke("market", "q", {"n": 1}) == {"again": True}
    assert await play.invoke("market", "q", {"n": 1}) == {"again": True}
    with pytest.raises(MCPError, match="KeyError"):
        await play.invoke("market", "bad", {})
    with pytest.raises(rp.ReplayDivergedError, match="market.q"):
        await play.invoke("market", "q", {"n": 99})


class _LLM(LLMClient):
    usage = 42

    @property
    def name(self) -> str:
        return "inner"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(content=f"answer to {request.purpose}", tool_calls=[], input_tokens=1, output_tokens=2,
                           model="m")


async def test_recording_llm_then_replay_llm() -> None:
    rec = rp.RecordingLLM(_LLM())
    assert rec.name == "inner" and rec.usage == 42  # attributes fall through to the inner client
    await rec.complete(LLMRequest(system_prompt="s", messages=[{"role": "user", "content": "a"}], purpose="planning"))
    await rec.complete(LLMRequest(system_prompt="s", messages=[], purpose="narrative"))
    ex = rec.exchanges
    assert [e["purpose"] for e in ex] == ["planning", "narrative"]
    assert all(len(e["prompt_hash"]) == 64 for e in ex)

    play = rp.ReplayLLM(ex)
    assert play.name == "replay"
    got = await play.complete(LLMRequest(system_prompt="other", messages=[], purpose="planning"))
    assert got.content == "answer to planning" and got.degradations == ()
    with pytest.raises(rp.ReplayDivergedError, match="planning"):
        await play.complete(LLMRequest(system_prompt="s", messages=[], purpose="planning"))
    with pytest.raises(rp.ReplayDivergedError, match="critique"):
        await play.complete(LLMRequest(system_prompt="s", messages=[], purpose="critique"))


def test_response_from_rebuilds_degradations_and_ignores_unknown_keys() -> None:
    resp = rp._response_from(
        {"content": "c", "tool_calls": [], "input_tokens": 0, "output_tokens": 0, "model": "m",
         "degradations": [{"rung": 1, "code": "x", "message": "y"}], "not_a_field": 1}
    )
    assert resp.degradations[0].code == "x"


class _Gate(ApprovalGateway):
    async def request(self, approval: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(approval.id, True, "alice", "fine")


def _approval(step: str) -> ApprovalRequest:
    return ApprovalRequest(id=f"a-{step}", task_id="t", step_id=step, tool_id="x", arguments={}, reason="r",
                           requested_by="cli")


async def test_recording_and_replay_approvals() -> None:
    rec = rp.RecordingApprovals(_Gate())
    assert (await rec.request(_approval("s1"))).approved
    assert rec.decisions == [{"step_id": "s1", "approved": True, "decided_by": "alice", "comment": "fine"}]
    play = rp.ReplayApprovals(rec.decisions)
    d = await play.request(_approval("s1"))
    assert (d.request_id, d.approved, d.decided_by, d.comment) == ("a-s1", True, "alice", "fine")
    with pytest.raises(rp.ReplayDivergedError, match="s1"):
        await play.request(_approval("s1"))


@pytest.mark.parametrize("make", [lambda: default_request(dataset="T01"), lambda: default_research_request("R01")])
def test_request_spec_round_trip(make: Any) -> None:
    req = make()
    spec = rp._request_spec(req)
    assert spec["kind"] == ("research" if "R" in req.dataset[:1] else "investigation")
    assert rp._request_from(spec) == req


@pytest.mark.parametrize(
    ("a", "b", "where"),
    [
        ({"x": 1}, {"x": 1}, None),
        ({"x": 1}, {"x": "1"}, "$.x"),
        ({"x": 1}, {"y": 1}, "$.x"),
        ({"x": [1, 2]}, {"x": [1]}, "$.x[len]"),
        ({"x": [1, {"z": 2}]}, {"x": [1, {"z": 3}]}, "$.x[1].z"),
        ([1, 2], [1, 2], None),
        ("a", "b", "$"),
    ],
)
def test_first_difference(a: Any, b: Any, where: str | None) -> None:
    assert rp._first_difference(a, b) == where


async def test_replay_refuses_unknown_bundle_version() -> None:
    with pytest.raises(ValueError, match="unsupported bundle version 99"):
        await rp.replay_bundle({"bundle_version": 99})


def test_save_and_load_keep_key_order(tmp_path: Path) -> None:
    doc = {"z": 1, "a": "é", "m": [1, 2]}
    path = rp.save_bundle(doc, tmp_path / "deep" / "b.json")
    assert list(rp.load_bundle(path)) == ["z", "a", "m"]
    assert "é" in path.read_text(encoding="utf-8")


def test_redact_doc_walks_nested_containers() -> None:
    out = rp._redact_doc({"k": ["plain", {"n": 3}], "t": None})
    assert out == {"k": ["plain", {"n": 3}], "t": None}


class _FakeResult:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc
        self.status = type("S", (), {"value": "succeeded"})()
        self.state = type("S", (), {"value": "completed"})()

    def to_dict(self, include_trace: bool = False) -> dict[str, Any]:
        assert include_trace
        return self.doc


class _FakePlatform:
    """Exercises one tool call, one model call and one approval through whatever wrappers it holds."""

    def __init__(self) -> None:
        self.mcp_client = _Inner()
        self.llm = _LLM()
        self.approvals = _Gate()
        self._registry = "cached"
        self.settings = type("S", (), {"step_timeout_seconds": 5, "task_timeout_seconds": 60, "max_retries": 1})()
        self.seen_registry: Any = "unset"

    async def investigate(self, req: Any) -> _FakeResult:
        self.seen_registry = self._registry
        await self.mcp_client.discover_tools("market")
        await self.mcp_client.invoke("market", "q", {"n": 1})
        await self.llm.complete(LLMRequest(system_prompt="s", messages=[], purpose="planning"))
        await self.approvals.request(_approval("s1"))
        return _FakeResult({"live": True})


async def test_record_bundle_captures_inputs_and_restores_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    replayed: list[dict[str, Any]] = []

    async def fake_replay_run(bundle: dict[str, Any]) -> _FakeResult:
        replayed.append(bundle)
        return _FakeResult({"b": [1, 2], "a": "x"})

    monkeypatch.setattr(rp, "_replay_run", fake_replay_run)
    platform = _FakePlatform()
    originals = (platform.mcp_client, platform.llm, platform.approvals)
    bundle = await rp.record_bundle(platform, default_request(dataset="T01"), step_ms=5)

    assert platform.seen_registry is None  # the cached registry is dropped so discovery is recorded
    assert (platform.mcp_client, platform.llm, platform.approvals, platform._registry) == (*originals, "cached")
    assert bundle["bundle_version"] == rp.BUNDLE_VERSION and bundle["clock"]["step_ms"] == 5
    assert bundle["request"]["kind"] == "investigation" and bundle["seeds"]["dataset"] == "T01"
    assert len(bundle["tool_calls"]) == 1 and list(bundle["discovery"]) == ["market"]
    assert bundle["prompt_hashes"] == [bundle["llm"][0]["prompt_hash"]]
    assert bundle["approvals"][0]["step_id"] == "s1"
    assert bundle["live"] == {"status": "succeeded", "state": "completed"}
    assert bundle["result"] == {"a": "x", "b": [1, 2]} and len(bundle["result_hash"]) == 64
    assert replayed[0] is bundle

    report = await rp.replay_bundle(bundle)
    assert report.equal and report.first_difference is None

    monkeypatch.setattr(rp, "_replay_run", lambda b: _async(_FakeResult({"b": [1, 3], "a": "x"})))
    report = await rp.replay_bundle(bundle)
    assert not report.equal and report.first_difference == "$.b[1]"
    assert report.stored_hash == bundle["result_hash"] != report.replayed_hash


async def _async(value: Any) -> Any:
    return value
