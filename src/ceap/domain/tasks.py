"""Task: the unit of work a user submits to the platform."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ceap.domain.common import new_id, utc_now


class TaskPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


@dataclass(frozen=True)
class Task:
    """A user request such as *"Analyse AAPL execution between 14:00 and 15:00"*.

    ``input`` carries the structured parameters extracted at the API
    boundary (symbol, window, scenario, ...) so downstream components never
    have to re-parse natural language.
    """

    id: str
    description: str
    input: dict[str, Any]
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: datetime = field(default_factory=utc_now)
    requested_by: str = "anonymous"

    @staticmethod
    def create(description: str, input: dict[str, Any] | None = None, **kwargs: Any) -> Task:
        return Task(id=new_id("TASK"), description=description, input=dict(input or {}), **kwargs)
