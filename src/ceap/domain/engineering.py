"""Engineering / technology domain objects (logs, metrics, deployments)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class MetricPoint:
    timestamp: datetime
    service: str
    metric: str
    value: float
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LogEntry:
    timestamp: datetime
    service: str
    level: str  # DEBUG | INFO | WARN | ERROR
    message: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Deployment:
    timestamp: datetime
    service: str
    version: str
    change_id: str
    description: str
    author: str
    rollback: bool = False


@dataclass(frozen=True)
class LatencySummary:
    service: str
    start: datetime
    end: datetime
    p50_us: float
    p99_us: float
    max_us: float
    sample_count: int
