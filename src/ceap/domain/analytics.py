"""Deterministic analytics contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime

from ceap.domain.execution import Execution, ExecutionMetrics, Order
from ceap.domain.market import Quote, Trade


class ExecutionAnalytics(ABC):
    """Computes execution-quality metrics from orders, executions and market data.

    Implementations must be pure and deterministic. The LLM is never
    responsible for these numbers.
    """

    @abstractmethod
    def calculate(
        self,
        orders: Sequence[Order],
        executions: Sequence[Execution],
        quotes: Sequence[Quote],
        trades: Sequence[Trade],
        start: datetime,
        end: datetime,
    ) -> ExecutionMetrics: ...
