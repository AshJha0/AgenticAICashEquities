"""In-memory repositories backed by synthetic datasets.

``DatasetStore`` lazily generates and caches one dataset per scenario so
that MCP servers, tests and the API share the same deterministic data.
A Parquet/DuckDB backed implementation would implement the same
repository interfaces (see ``ceap.data.export``).
"""

from __future__ import annotations

import asyncio
import threading
import weakref
from bisect import bisect_left
from collections.abc import Sequence
from datetime import datetime
from typing import TypeVar

from ceap.data.scenarios import ScenarioSpec, get_scenario
from ceap.data.synthetic import SyntheticDataset, SyntheticMarketGenerator
from ceap.domain.execution import Execution, Order
from ceap.domain.market import OrderBookSnapshot, Quote, Trade
from ceap.domain.repositories import ExecutionRepository, MarketDataRepository

T = TypeVar("T")


def _time_keys(items: Sequence[T], key=lambda x: x.timestamp) -> list[float]:
    return [key(i).timestamp() for i in items]


def _slice_by_time(
    items: Sequence[T],
    start: datetime,
    end: datetime,
    key=lambda x: x.timestamp,
    keys: Sequence[float] | None = None,
) -> list[T]:
    """Return items with ``start <= ts < end`` assuming ``items`` sorted by ``key``.

    Uses ``bisect_left`` for the exclusive upper bound: subtracting an epsilon
    from an epoch-scale float would underflow double precision. Repositories pass
    precomputed ``keys`` so a slice is O(log n), not a pass over the whole series.
    """
    if keys is None:
        keys = _time_keys(items, key)
    lo = bisect_left(keys, start.timestamp())
    hi = bisect_left(keys, end.timestamp())
    return list(items[lo:hi])


# Sorted series and their epoch keys, computed once per dataset object. The MCP servers build
# repositories per call, so caching on the repository would redo an O(n) pass on every tool call.
_INDEX: dict[int, dict[str, tuple[list, list[float]]]] = {}
_INDEX_LOCK = threading.Lock()


def _indexed(dataset: SyntheticDataset, attr: str) -> tuple[list, list[float]]:
    with _INDEX_LOCK:
        per = _INDEX.get(id(dataset))
        if per is None:
            per = _INDEX[id(dataset)] = {}
            weakref.finalize(dataset, _INDEX.pop, id(dataset), None)
        hit = per.get(attr)
    if hit is None or len(hit[0]) != len(getattr(dataset, attr)):  # rebuilt if the series grew
        items = sorted(getattr(dataset, attr), key=lambda x: x.timestamp)
        hit = (items, _time_keys(items))
        with _INDEX_LOCK:
            per[attr] = hit
    return hit


class DatasetStore:
    """Thread-safe cache of generated datasets keyed by scenario id.

    Generation is CPU-bound (about 30-80 s for a cold intraday scenario), so each key has its own
    lock: two callers asking for the same scenario generate it once, while a caller asking for a
    different scenario is not held up. Async callers use :meth:`aget`, which runs the generation in
    a worker thread so the event loop (and ``/health``) keeps answering.
    """

    def __init__(self, generator: SyntheticMarketGenerator | None = None) -> None:
        self._generator = generator or SyntheticMarketGenerator()
        self._datasets: dict[str, SyntheticDataset] = {}
        self._lock = threading.Lock()  # guards the two dicts only, never held while generating
        self._key_locks: dict[str, threading.Lock] = {}

    @staticmethod
    def _key(spec: ScenarioSpec) -> str:
        return f"{spec.id}:{spec.symbol}:{spec.seed}"

    def get(self, scenario: str | ScenarioSpec = "T01") -> SyntheticDataset:
        spec = scenario if isinstance(scenario, ScenarioSpec) else get_scenario(scenario)
        key = self._key(spec)
        with self._lock:
            ds = self._datasets.get(key)
            if ds is not None:
                return ds
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:
            with self._lock:
                ds = self._datasets.get(key)
            if ds is None:
                ds = self._generator.generate(spec)
                with self._lock:
                    ds = self._datasets.setdefault(key, ds)
        return ds

    async def aget(self, scenario: str | ScenarioSpec = "T01") -> SyntheticDataset:
        """:meth:`get` off the event loop: a cache hit returns at once, a miss generates in a thread."""
        spec = scenario if isinstance(scenario, ScenarioSpec) else get_scenario(scenario)
        with self._lock:
            ds = self._datasets.get(self._key(spec))
        if ds is not None:
            return ds
        return await asyncio.to_thread(self.get, spec)

    def is_cached(self, scenario: str | ScenarioSpec) -> bool:
        spec = scenario if isinstance(scenario, ScenarioSpec) else get_scenario(scenario)
        with self._lock:
            return self._key(spec) in self._datasets

    def put(self, dataset: SyntheticDataset) -> None:
        with self._lock:
            self._datasets[self._key(dataset.scenario)] = dataset

    def clear(self) -> None:
        with self._lock:
            self._datasets.clear()


class InMemoryMarketDataRepository(MarketDataRepository):
    def __init__(self, dataset: SyntheticDataset) -> None:
        self._ds = dataset
        self._quotes, self._quotes_keys = _indexed(dataset, "quotes")
        self._trades, self._trades_keys = _indexed(dataset, "trades")
        self._books, self._books_keys = _indexed(dataset, "order_books")

    async def quotes(self, symbol: str, start: datetime, end: datetime) -> list[Quote]:
        if symbol != self._ds.symbol:
            return []
        return _slice_by_time(self._quotes, start, end, keys=self._quotes_keys)

    async def trades(self, symbol: str, start: datetime, end: datetime) -> list[Trade]:
        if symbol != self._ds.symbol:
            return []
        return _slice_by_time(self._trades, start, end, keys=self._trades_keys)

    async def order_books(self, symbol: str, start: datetime, end: datetime) -> list[OrderBookSnapshot]:
        if symbol != self._ds.symbol:
            return []
        return _slice_by_time(self._books, start, end, keys=self._books_keys)

    async def symbols(self) -> list[str]:
        return [self._ds.symbol]


class InMemoryExecutionRepository(ExecutionRepository):
    def __init__(self, dataset: SyntheticDataset) -> None:
        self._ds = dataset
        self._orders, self._orders_keys = _indexed(dataset, "orders")
        self._executions, self._executions_keys = _indexed(dataset, "executions")

    async def orders(self, symbol: str, start: datetime, end: datetime) -> list[Order]:
        if symbol != self._ds.symbol:
            return []
        return _slice_by_time(self._orders, start, end, keys=self._orders_keys)

    async def executions(self, symbol: str, start: datetime, end: datetime) -> list[Execution]:
        if symbol != self._ds.symbol:
            return []
        return _slice_by_time(self._executions, start, end, keys=self._executions_keys)
