"""G3: CPU-bound work in a killable process pool, so asyncio timeouts and cancellation really stop it."""

from __future__ import annotations

import asyncio
import operator
import time

import pytest

from ceap.mcp import cpu_pool


@pytest.fixture
def one_worker(monkeypatch):
    monkeypatch.setenv(cpu_pool.PROCESSES_ENV, "1")
    cpu_pool.shutdown_default_pool()
    yield
    cpu_pool.shutdown_default_pool()


@pytest.mark.slow
async def test_g3_timeout_terminates_the_worker_and_the_pool_recovers(one_worker):
    assert await cpu_pool.run_cpu_bound(operator.add, 2, 3) == 5  # warm the worker
    pool = cpu_pool.default_pool()
    assert pool is not None
    executor = pool._idle[0]
    assert executor is not None
    worker = next(iter(executor._processes.values()))
    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(cpu_pool.run_cpu_bound(time.sleep, 60), timeout=1.0)
    assert time.perf_counter() - started < 20
    assert pool.kills == 1
    worker.join(timeout=10)
    assert not worker.is_alive(), "the worker running the timed-out call is still alive"
    assert await cpu_pool.run_cpu_bound(operator.mul, 6, 7) == 42  # a fresh pool


async def test_g3_disabled_pool_runs_in_a_thread(monkeypatch):
    monkeypatch.setenv(cpu_pool.PROCESSES_ENV, "0")
    assert cpu_pool.default_pool() is None
    assert await cpu_pool.run_cpu_bound(operator.add, 1, 1) == 2


def test_g3_processes_setting(monkeypatch):
    monkeypatch.delenv(cpu_pool.PROCESSES_ENV, raising=False)
    assert cpu_pool.configured_processes() == 0  # library, CLI and tests: threads
    cpu_pool.enable_process_pool()
    try:
        assert cpu_pool.configured_processes() == cpu_pool.DEFAULT_PROCESSES  # the API server
        monkeypatch.setenv(cpu_pool.PROCESSES_ENV, "nope")
        assert cpu_pool.configured_processes() == cpu_pool.DEFAULT_PROCESSES
    finally:
        cpu_pool.enable_process_pool(0)
    monkeypatch.setenv(cpu_pool.PROCESSES_ENV, "-3")
    assert cpu_pool.configured_processes() == 0
    with pytest.raises(ValueError):
        cpu_pool.KillablePool(0)


@pytest.mark.slow
async def test_g3_research_dataset_runs_in_a_worker_and_matches_inline(one_worker, history):
    """The worker regenerates the dataset from its scenario; the answer equals the in-process one."""
    from ceap.analytics.signal_statistics import evaluate_signal
    from ceap.domain.common import to_jsonable

    ds = history.get("R01")
    inline = to_jsonable(evaluate_signal(ds, "momentum_12_1", None, None, None, None, None))
    pooled = to_jsonable(
        await cpu_pool.run_on_history(evaluate_signal, ds, "momentum_12_1", None, None, None, None, None)
    )
    assert pooled == inline


@pytest.mark.slow
async def test_g3_research_report_is_identical_in_process_and_thread_mode(monkeypatch, store, history):
    """Coordinator repro: R03 in process mode lost run_backtest and read the clock a different number
    of times. Same report, byte for byte, whichever way the CPU tools run."""
    import json

    from ceap.config import Settings
    from ceap.domain.common import to_jsonable
    from ceap.harness.replay import DeterministicClock, injected_clock
    from ceap.llm.client import MockLLMClient
    from ceap.platform import Platform, default_research_request

    async def report() -> tuple[str, int]:
        p = Platform(settings=Settings(llm_provider="mock", auto_approve=True), llm=MockLLMClient(),
                     store=store, history=history)
        clock = DeterministicClock()
        try:
            with injected_clock(clock):
                result = await p.research(default_research_request("R03"))
        finally:
            p.close()
        assert result.report is not None, result.error
        return json.dumps(to_jsonable(result.report), sort_keys=True, default=str), clock._ticks

    monkeypatch.setenv(cpu_pool.PROCESSES_ENV, "0")
    thread_report, thread_reads = await report()
    monkeypatch.setenv(cpu_pool.PROCESSES_ENV, str(cpu_pool.DEFAULT_PROCESSES))  # what the API server runs
    cpu_pool.shutdown_default_pool()
    pool = cpu_pool.default_pool()
    assert pool is not None
    await asyncio.to_thread(pool.warm)  # as the API server does: workers up before the first step
    try:
        process_report, process_reads = await report()
    finally:
        cpu_pool.shutdown_default_pool()
    assert pool.kills == 0, "a CPU tool timed out in process mode"
    assert process_report == thread_report
    assert process_reads == thread_reads
