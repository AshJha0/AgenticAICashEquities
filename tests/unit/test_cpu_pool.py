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
    worker = next(iter(pool._get()._processes.values()))
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
    assert cpu_pool.configured_processes() == cpu_pool.DEFAULT_PROCESSES
    monkeypatch.setenv(cpu_pool.PROCESSES_ENV, "nope")
    assert cpu_pool.configured_processes() == cpu_pool.DEFAULT_PROCESSES
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
