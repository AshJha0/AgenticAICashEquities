"""Task: the unit of work a user submits to the platform."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ceap.domain.common import new_id, utc_now
from ceap.util.redact import redact


class TaskPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class TaskStatus(str, Enum):
    """Terminal and in-flight states a task can report.

    ``DEGRADED``: finished, but at least one fallback ran (recorded in ``warnings``).
    ``INCOMPLETE_ANALYSIS``: finished without every required analysis; must not read as a pass.
    ``INTERRUPTED``: stopped by restart or cancellation; resumable or abandoned, never "done".
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    DEGRADED = "DEGRADED"
    INCOMPLETE_ANALYSIS = "INCOMPLETE_ANALYSIS"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WarningSeverity(str, Enum):
    INFO = "INFO"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ResultWarning:
    """A structured warning attached to a result (``Result.warnings`` in the plan).

    Named ``ResultWarning`` so it does not shadow the built-in ``Warning`` class.
    """

    code: str
    message: str
    source: str = "harness"
    severity: WarningSeverity = WarningSeverity.DEGRADED


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
        # Question text is redacted at the boundary so no log, store or prompt sees secrets or PII.
        return Task(id=new_id("TASK"), description=redact(description), input=dict(input or {}), **kwargs)
