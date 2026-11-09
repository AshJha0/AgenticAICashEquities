"""Killable process pool for CPU-bound MCP tools (G3).

An ``async`` tool that runs pure-Python or NumPy work for tens of seconds blocks the event loop,
so ``asyncio.wait_for`` in the harness cannot fire and a cancelled task keeps running. Tools
registered with ``cpu_bound=True`` hand their heavy part to :func:`run_cpu_bound`, which runs it in
a worker process. When the awaiting coroutine is cancelled (step timeout, task deadline or an
operator cancel), the pool's worker processes are terminated and a fresh pool is started on the
next call, so the work really stops.

``CEAP_CPU_TOOL_PROCESSES`` sets the number of worker processes (default 2). ``0`` runs the work in
a thread instead: the event loop stays free, but a timed-out call runs on to completion in the
background. Tests use ``0`` unless they exercise the pool itself, because starting worker
processes on Windows costs a few seconds each.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, TypeVar

log = logging.getLogger(__name__)

PROCESSES_ENV = "CEAP_CPU_TOOL_PROCESSES"
DEFAULT_PROCESSES = 2

R = TypeVar("R")


def configured_processes() -> int:
    raw = os.getenv(PROCESSES_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_PROCESSES
    try:
        return max(0, int(raw))
    except ValueError:
        log.warning("%s=%r is not an integer; using %d", PROCESSES_ENV, raw, DEFAULT_PROCESSES)
        return DEFAULT_PROCESSES


class KillablePool:
    """A ``ProcessPoolExecutor`` that is torn down (workers terminated) when a caller is cancelled."""

    def __init__(self, processes: int) -> None:
        if processes < 1:
            raise ValueError("processes must be >= 1")
        self.processes = processes
        self._lock = threading.Lock()
        self._executor: ProcessPoolExecutor | None = None
        self.kills = 0  # how many times a cancellation terminated the workers (observable in tests)

    def _get(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = ProcessPoolExecutor(max_workers=self.processes)
            return self._executor

    def kill(self) -> None:
        """Terminate every worker process and forget the pool; the next call starts a new one."""
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is None:
            return
        self.kills += 1
        processes = list((getattr(executor, "_processes", None) or {}).values())
        for proc in processes:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001 - already gone
                pass
        executor.shutdown(wait=False, cancel_futures=True)
        for proc in processes:
            proc.join(timeout=5)

    def shutdown(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    async def run(self, fn: Callable[..., R], *args: Any) -> R:
        loop = asyncio.get_running_loop()
        try:
            future = loop.run_in_executor(self._get(), fn, *args)
        except BrokenProcessPool:
            self.kill()
            future = loop.run_in_executor(self._get(), fn, *args)
        try:
            return await future
        except asyncio.CancelledError:
            log.warning("cancelled CPU-bound call %s: terminating worker processes", getattr(fn, "__name__", fn))
            self.kill()
            raise
        except BrokenProcessPool:
            self.kill()
            raise


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
                _POOL.kill()
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
class DatasetMismatchError(Exception):
    """The worker regenerated a dataset whose hash differs from the caller's (an injected store)."""


_WORKER_HISTORY: Any = None


def _worker_history_dataset(spec: Any, expected_hash: str) -> Any:
    global _WORKER_HISTORY
    from ceap.data.historical import HistoricalStore

    if _WORKER_HISTORY is None:
        _WORKER_HISTORY = HistoricalStore()
    ds = _WORKER_HISTORY.get(spec)
    if ds.dataset_hash != expected_hash:
        raise DatasetMismatchError(spec.id)
    return ds


def _call_on_spec(fn: Callable[..., R], spec: Any, expected_hash: str, *args: Any) -> R:
    return fn(_worker_history_dataset(spec, expected_hash), *args)


async def run_on_history(fn: Callable[..., R], ds: Any, *args: Any) -> R:
    """``fn(ds, *args)`` for a research dataset, off the loop and killable.

    Research datasets are deterministic, so the worker regenerates (and caches) the dataset from its
    scenario rather than receiving ~9 MB of pickled bars per call. If the regenerated content hash
    differs (a test or caller injected its own data), the caller's dataset is sent instead.
    """
    if default_pool() is None:
        return await asyncio.to_thread(fn, ds, *args)
    try:
        return await run_cpu_bound(_call_on_spec, fn, ds.scenario, ds.dataset_hash, *args)
    except DatasetMismatchError:
        return await run_cpu_bound(fn, ds, *args)
