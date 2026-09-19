"""Repository interfaces (storage is an infrastructure concern)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ceap.domain.execution import Execution, Order
from ceap.domain.market import OrderBookSnapshot, Quote, Trade


class MarketDataRepository(ABC):
    @abstractmethod
    async def quotes(self, symbol: str, start: datetime, end: datetime) -> list[Quote]: ...

    @abstractmethod
    async def trades(self, symbol: str, start: datetime, end: datetime) -> list[Trade]: ...

    @abstractmethod
    async def order_books(self, symbol: str, start: datetime, end: datetime) -> list[OrderBookSnapshot]: ...

    @abstractmethod
    async def symbols(self) -> list[str]: ...


class ExecutionRepository(ABC):
    @abstractmethod
    async def orders(self, symbol: str, start: datetime, end: datetime) -> list[Order]: ...

    @abstractmethod
    async def executions(self, symbol: str, start: datetime, end: datetime) -> list[Execution]: ...
