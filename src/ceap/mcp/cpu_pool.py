"""Killable process pool for CPU-bound MCP tools (G3).

An ``async`` tool that runs pure-Python or NumPy work for tens of seconds blocks the event loop,
so ``asyncio.wait_for`` in the harness cannot fire and a cancelled task keeps running. Tools
registered with ``cpu_bound=True`` hand their heavy part to :func:`run_cpu_bound`, which runs it in
a worker process. When the awaiting coroutine is cancelled (step timeout, task deadline or an
operator cancel), the pool's worker processes are terminated and a fresh pool is started on the
next call, so the work really stops.

Where it is on. A library caller, the CLI and the tests run CPU tools in a thread (the event loop
stays free, but a timed-out call runs on to completion in the background). The API server turns
the process pool on at start-up (2 workers) and starts the workers before it serves, so their
start-up cost is never inside a step timeout. ``CEAP_CPU_TOOL_PROCESSES`` overrides both: ``0``
forces threads, ``N`` forces N workers.

Workers are started with the ``spawn`` method on every platform. ``spawn`` imports the parent's
``__main__`` module in each worker, so a script that enables the pool must keep its top-level
work under ``if __name__ == "__main__":`` (the ``ceap`` and ``uvicorn`` entry points do). The
worker initializer imports only ``ceap`` modules.

Determinism: the worker receives the caller's dataset and plain arguments and returns plain data.
The functions run there must not read the clock or draw ids (``utc_now``/``new_id``): those are
injected per task in the parent and are not visible in a worker.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, TypeVar

log = logging.getLogger(__name__)

PROCESSES_ENV = "CEAP_CPU_TOOL_PROCESSES"
DEFAULT_PROCESSES = 2  # what the API server uses when the env var is unset
_default_processes = 0  # library / CLI / tests: threads; ``enable_process_pool`` changes it

R = TypeVar("R")


def configured_processes() -> int:
    raw = os.getenv(PROCESSES_ENV)
    if raw is None or raw.strip() == "":
        return _default_processes
    try:
        return max(0, int(raw))
    except ValueError:
        log.warning("%s=%r is not an integer; using %d", PROCESSES_ENV, raw, _default_processes)
        return _default_processes


def enable_process_pool(processes: int = DEFAULT_PROCESSES) -> None:
    """Make the process pool the default when ``CEAP_CPU_TOOL_PROCESSES`` is unset (the API server)."""
    global _default_processes
    _default_processes = max(0, processes)


def _worker_init() -> None:
    """Runs once in each worker: import the analytics the CPU tools use, nothing else."""
    import ceap.analytics.backtest  # noqa: F401
    import ceap.analytics.profitability  # noqa: F401
    import ceap.analytics.signal_statistics  # noqa: F401


def _ready() -> bool:
    return True


def _new_worker() -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=1, mp_context=multiprocessing.get_context("spawn"), initializer=_worker_init
    )


def _terminate(executor: ProcessPoolExecutor) -> None:
    processes = list((getattr(executor, "_processes", None) or {}).values())
    for proc in processes:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001 - already gone
            pass
    executor.shutdown(wait=False, cancel_futures=True)
    for proc in processes:
        proc.join(timeout=5)


class KillablePool:
    """``processes`` single-worker slots; a cancelled call terminates only its own slot's worker.

    One shared ``ProcessPoolExecutor`` cannot do that: killing the call that timed out would also
    kill whatever another task was computing on a sibling worker. Each call checks out an idle slot
    (waiting, cancellably, if all are busy), runs there, and hands it back; on cancellation the
    slot's worker is terminated and replaced lazily.
    """

    POLL_S = 0.02

    def __init__(self, processes: int) -> None:
        if processes < 1:
            raise ValueError("processes must be >= 1")
        self.processes = processes
        self._lock = threading.Lock()
        self._idle: list[ProcessPoolExecutor | None] = [None] * processes  # None: start on first use
        self._busy: set[ProcessPoolExecutor] = set()
        self.kills = 0  # calls whose worker was terminated (observable in tests)

    def _checkout(self) -> ProcessPoolExecutor | None:
        with self._lock:
            if not self._idle:
                return None
            executor = self._idle.pop()
            if executor is None:
                executor = _new_worker()
            self._busy.add(executor)
            return executor

    def _checkin(self, executor: ProcessPoolExecutor | None) -> None:
        with self._lock:
            if executor is not None:
                self._busy.discard(executor)
            self._idle.append(executor)

    def _discard(self, executor: ProcessPoolExecutor) -> None:
        with self._lock:
            self._busy.discard(executor)
            self._idle.append(None)  # a fresh worker next time
        _terminate(executor)

    def warm(self) -> None:
        """Start every worker and run its initializer now (blocking), not inside the first tool call."""
        with self._lock:
            self._idle = [e if e is not None else _new_worker() for e in self._idle]
            idle = list(self._idle)
        for future in [e.submit(_ready) for e in idle if e is not None]:
            future.result()

    def kill(self) -> None:
        """Terminate every worker, idle or busy (shutdown)."""
        with self._lock:
            executors = [e for e in self._idle if e is not None] + list(self._busy)
            self._idle = [None] * self.processes
            self._busy.clear()
        for executor in executors:
            _terminate(executor)

    shutdown = kill

    async def run(self, fn: Callable[..., R], *args: Any) -> R:
        executor = self._checkout()
        while executor is None:  # all slots busy: wait without holding anything, so cancel is clean
            await asyncio.sleep(self.POLL_S)
            executor = self._checkout()
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(executor, fn, *args)
        except asyncio.CancelledError:
            log.warning("cancelled CPU-bound call %s: terminating its worker", getattr(fn, "__name__", fn))
            self.kills += 1
            self._discard(executor)
            raise
        except BrokenProcessPool:
            self._discard(executor)
            raise
        except BaseException:
            self._checkin(executor)  # the tool raised; the worker is fine
            raise
        self._checkin(executor)
        return result


_POOL: KillablePool | None = None
_POOL_LOCK = threading.Lock()


def default_pool() -> KillablePool | None:
    """The shared pool, or ``None`` when ``CEAP_CPU_TOOL_PROCESSES=0``."""
    global _POOL
    n = configured_processes()
    if n == 0:
        return None
    with _POOL_LOCK:
        if _POOL is None or _POOL.processes != n:
            if _POOL is not None:
                _POOL.kill()  # resized: the old workers go
            _POOL = KillablePool(n)
        return _POOL


def shutdown_default_pool() -> None:
    global _POOL
    with _POOL_LOCK:
        pool, _POOL = _POOL, None
    if pool is not None:
        pool.kill()


async def run_cpu_bound(fn: Callable[..., R], *args: Any) -> R:
    """Run ``fn(*args)`` off the event loop: in the killable pool, or in a thread when it is disabled.

    ``fn`` and ``args`` must be picklable (a module-level function and plain data).
    """
    pool = default_pool()
    if pool is None:
        return await asyncio.to_thread(fn, *args)
    return await pool.run(fn, *args)


# ---------------------------------------------------------------- research datasets
async def run_on_history(fn: Callable[..., R], ds: Any, *args: Any) -> R:
    """``fn(ds, *args)`` for a research dataset, off the loop and killable.

    The caller's dataset object is sent to the worker (about 9 MB pickled, under a second), so the
    worker computes on exactly the data the caller holds, whatever store it came from. An earlier
    version regenerated the dataset in the worker; a cold regeneration could exceed the step
    timeout and the result was lost.
    """
    return await run_cpu_bound(fn, ds, *args)
