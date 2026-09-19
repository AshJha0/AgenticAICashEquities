"""State machine, retry, cancellation, memory, tracer, metrics."""

from __future__ import annotations

import asyncio

import pytest

from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.findings import Finding
from ceap.domain.tools import ToolRequest, ToolResult, ToolStatus
from ceap.harness.cancellation import CancellationToken, TaskCancelled
from ceap.harness.memory import InvestigationMemory
from ceap.harness.retry import RetryableError, RetryPolicy, retry_async
from ceap.harness.state_machine import HarnessState, InvalidTransition, StateMachine
from ceap.observability.metrics import MetricsRegistry
from ceap.observability.tracing import InMemoryTracer


def test_state_machine_happy_path_and_invalid_transition():
    sm = StateMachine()
    for s in (
        HarnessState.PLANNING,
        HarnessState.VALIDATING_PLAN,
        HarnessState.EXECUTING,
        HarnessState.AWAITING_APPROVAL,
        HarnessState.EXECUTING,
        HarnessState.CRITIQUING,
        HarnessState.EXECUTING,
        HarnessState.VALIDATING_EVIDENCE,
        HarnessState.FINALISING,
        HarnessState.COMPLETED,
    ):
        sm.transition(s, "ok")
    assert sm.state.terminal
    with pytest.raises(InvalidTransition):
        sm.transition(HarnessState.EXECUTING)
    assert len(sm.export()) == 10 and sm.export()[0]["from"] == "CREATED"


def test_state_machine_fail_and_cancel_are_idempotent():
    sm = StateMachine()
    sm.transition(HarnessState.PLANNING)
    sm.fail("boom")
    sm.fail("again")  # no-op on terminal
    assert sm.state is HarnessState.FAILED
    sm2 = StateMachine()
    sm2.cancel("user")
    assert sm2.state is HarnessState.CANCELLED
    with pytest.raises(InvalidTransition):
        StateMachine().transition(HarnessState.COMPLETED)


async def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableError("transient")
        return "ok"

    retries = []
    out = await retry_async(
        flaky, RetryPolicy(max_attempts=3, base_delay_seconds=0.001), lambda n, e: retries.append(n)
    )
    assert out == "ok" and retries == [1, 2]


async def test_retry_gives_up_and_ignores_non_retryable():
    async def always():
        raise RetryableError("nope")

    with pytest.raises(RetryableError):
        await retry_async(always, RetryPolicy(max_attempts=2, base_delay_seconds=0.001))

    async def fatal():
        raise ValueError("not transient")

    with pytest.raises(ValueError):
        await retry_async(fatal, RetryPolicy(max_attempts=5, base_delay_seconds=0.001))


def test_retry_delay_is_bounded():
    p = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=2.0, backoff_multiplier=10, jitter=0.0)
    assert p.delay(1) == 1.0 and p.delay(5) == 2.0


async def test_cancellation_token():
    tok = CancellationToken()
    tok.raise_if_cancelled()
    tok.cancel("user")
    assert tok.is_cancelled and tok.reason == "user"
    with pytest.raises(TaskCancelled):
        tok.raise_if_cancelled()
    await asyncio.wait_for(tok.wait(), 0.1)


def test_memory_records_tools_and_resolves_evidence():
    mem = InvestigationMemory()
    ev = Evidence.create(EvidenceType.EXECUTION_DATA, "t", "d")
    res = ToolResult(ToolStatus.SUCCESS, data={"count": 1}, evidence=(ev,))
    out = mem.record_tool(
        "s1", ToolRequest("execution.get_executions", {"symbol": "AAPL", "start": "x"}), res
    )
    assert out is not None and mem.find("execution.get_executions", symbol="AAPL") is out
    assert mem.find("execution.get_executions", symbol="MSFT") is None
    assert mem.record_tool("s2", ToolRequest("x"), ToolResult(ToolStatus.ERROR, error="e")) is None
    f_ok = Finding.create("ok", [ev.id], 0.9)
    f_bad = Finding.create("bad", ["EXEC-deadbeef"], 0.9)
    assert mem.unresolved_evidence(f_ok) == [] and mem.unresolved_evidence(f_bad) == ["EXEC-deadbeef"]
    assert len(mem.find_all("execution.get_executions")) == 1


async def test_tracer_nests_spans_and_records_errors():
    tr = InMemoryTracer()
    async with tr.span("outer", a=1):
        tr.event("hello", x=2)
        async with tr.span("inner"):
            pass
        with pytest.raises(RuntimeError):
            async with tr.span("failing"):
                raise RuntimeError("x")
    exported = tr.export()
    names = [s["name"] for s in exported["spans"]]
    assert names == ["outer", "inner", "failing"]
    assert exported["spans"][1]["parent_id"] == exported["spans"][0]["id"]
    assert exported["spans"][2]["status"] == "ERROR"
    assert exported["spans"][0]["events"][0]["name"] == "hello"
    assert tr.summary()["span_count"] == 3


def test_metrics_registry_renders_prometheus_text():
    m = MetricsRegistry()
    m.inc("calls_total", tool="a")
    m.inc("calls_total", tool="a")
    m.observe("latency_ms", 5.0)
    m.observe("latency_ms", 7.0)
    text = m.render()
    assert 'calls_total{tool="a"} 2.0' in text
    assert "latency_ms_count 2.0" in text and "latency_ms_max 7.0" in text
    m.reset()
    assert m.render() == ""
