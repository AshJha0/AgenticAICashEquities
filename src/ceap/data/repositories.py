"""In-memory repositories backed by synthetic datasets.

``DatasetStore`` lazily generates and caches one dataset per scenario so
that MCP servers, tests and the API share the same deterministic data.
A Parquet/DuckDB backed implementation would implement the same
repository interfaces (see ``ceap.data.export``).
"""

from __future__ import annotations

import threading
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


def _slice_by_time(items: Sequence[T], start: datetime, end: datetime, key=lambda x: x.timestamp) -> list[T]:
    """Return items with ``start <= ts < end`` assuming ``items`` sorted by ``key``.

    Uses ``bisect_left`` for the exclusive upper bound: subtracting an epsilon
    from an epoch-scale float would underflow double precision.
    """
    keys = [key(i).timestamp() for i in items]
    lo = bisect_left(keys, start.timestamp())
    hi = bisect_left(keys, end.timestamp())
    return list(items[lo:hi])


class DatasetStore:
    """Thread-safe cache of generated datasets keyed by scenario id."""

    def __init__(self, generator: SyntheticMarketGenerator | None = None) -> None:
        self._generator = generator or SyntheticMarketGenerator()
        self._datasets: dict[str, SyntheticDataset] = {}
        self._lock = threading.Lock()

    def get(self, scenario: str | ScenarioSpec = "T01") -> SyntheticDataset:
        spec = scenario if isinstance(scenario, ScenarioSpec) else get_scenario(scenario)
        key = f"{spec.id}:{spec.symbol}:{spec.seed}"
        with self._lock:
            ds = self._datasets.get(key)
            if ds is None:
                ds = self._generator.generate(spec)
                self._datasets[key] = ds
        return ds

    def put(self, dataset: SyntheticDataset) -> None:
        spec = dataset.scenario
        with self._lock:
            self._datasets[f"{spec.id}:{spec.symbol}:{spec.seed}"] = dataset

    def clear(self) -> None:
        with self._lock:
            self._datasets.clear()


class InMemoryMarketDataRepository(MarketDataRepository):
    def __init__(self, dataset: SyntheticDataset) -> None:
        self._ds = dataset
        self._quotes = sorted(dataset.quotes, key=lambda q: q.timestamp)
        self._trades = sorted(dataset.trades, key=lambda t: t.timestamp)
        self._books = sorted(dataset.order_books, key=lambda b: b.timestamp)

    async def quotes(self, symbol: str, start: datetime, end: datetime) -> list[Quote]:
        if symbol != self._ds.symbol:
            return []
        return _slice_by_time(self._quotes, start, end)

    async def trades(self, symbol: str, start: datetime, end: datetime) -> list[Trade]:
        if symbol != self._ds.symbol:
            return []
        return _slice_by_time(self._trades, start, end)

    async def order_books(self, symbol: str, start: datetime, end: datetime) -> list[OrderBookSnapshot]:
        if symbol != self._ds.symbol:
            return []
        return _slice_by_time(self._books, start, end)

    async def symbols(self) -> list[str]:
        return [self._ds.symbol]


class InMemoryExecutionRepository(ExecutionRepository):
    def __init__(self, dataset: SyntheticDataset) -> None:
        self._ds = dataset
        self._orders = sorted(dataset.orders, key=lambda o: o.timestamp)
        self._executions = sorted(dataset.executions, key=lambda e: e.timestamp)

    async def orders(self, symbol: str, start: datetime, end: datetime) -> list[Order]:
        if symbol != self._ds.symbol:
            return []
        return _slice_by_time(self._orders, start, end)

    async def executions(self, symbol: str, start: datetime, end: datetime) -> list[Execution]:
        if symbol != self._ds.symbol:
            return []
        return _slice_by_time(self._executions, start, end)
