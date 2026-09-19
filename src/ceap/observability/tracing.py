"""Execution tracing.

``InMemoryTracer`` records a tree of spans per investigation and is what the
API returns under ``/investigations/{id}/trace``. ``OpenTelemetryTracer``
forwards the same spans to an OTel SDK when it is installed and
configured (``pip install .[observability]``); the harness code is
identical in both cases.
"""

from __future__ import annotations

import contextlib
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ceap.domain.common import new_id, utc_now


@dataclass
class Span:
    id: str
    name: str
    parent_id: str | None
    started_at: str
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    ended_at: str | None = None
    duration_ms: float | None = None
    status: str = "OK"
    error: str | None = None
    _t0: float = field(default_factory=time.perf_counter, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "parent_id": self.parent_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error": self.error,
            "attributes": self.attributes,
            "events": self.events,
        }


class ExecutionTracer(ABC):
    @abstractmethod
    def span(self, name: str, **attributes: Any) -> contextlib.AbstractAsyncContextManager[Span]: ...

    @abstractmethod
    def event(self, name: str, **attributes: Any) -> None: ...

    @abstractmethod
    def export(self) -> dict[str, Any]: ...


class InMemoryTracer(ExecutionTracer):
    def __init__(self, trace_id: str | None = None) -> None:
        self.trace_id = trace_id or new_id("TRACE")
        self.spans: list[Span] = []
        self._stack: list[Span] = []

    @contextlib.asynccontextmanager
    async def span(self, name: str, **attributes: Any) -> AsyncIterator[Span]:
        parent = self._stack[-1].id if self._stack else None
        span = Span(
            id=new_id("SPAN"),
            name=name,
            parent_id=parent,
            started_at=utc_now().isoformat(),
            attributes=dict(attributes),
        )
        self.spans.append(span)
        self._stack.append(span)
        try:
            yield span
        except BaseException as exc:
            span.status = "ERROR"
            span.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            span.ended_at = utc_now().isoformat()
            span.duration_ms = round((time.perf_counter() - span._t0) * 1000.0, 3)
            self._stack.pop()

    def event(self, name: str, **attributes: Any) -> None:
        payload = {"name": name, "timestamp": utc_now().isoformat(), **attributes}
        if self._stack:
            self._stack[-1].events.append(payload)
        else:
            self.spans.append(
                Span(
                    id=new_id("SPAN"),
                    name=name,
                    parent_id=None,
                    started_at=payload["timestamp"],
                    attributes=attributes,
                    ended_at=payload["timestamp"],
                    duration_ms=0.0,
                )
            )

    def export(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, "spans": [s.to_dict() for s in self.spans]}

    def summary(self) -> dict[str, Any]:
        by_name: dict[str, dict[str, float]] = {}
        for s in self.spans:
            entry = by_name.setdefault(s.name.split(":")[0], {"count": 0, "total_ms": 0.0, "errors": 0})
            entry["count"] += 1
            entry["total_ms"] += s.duration_ms or 0.0
            entry["errors"] += 1 if s.status == "ERROR" else 0
        return {"trace_id": self.trace_id, "span_count": len(self.spans), "by_name": by_name}


class OpenTelemetryTracer(InMemoryTracer):  # pragma: no cover - requires optional dependency
    """Mirrors spans into OpenTelemetry while keeping the in-memory trace for the API."""

    def __init__(self, service_name: str = "ceap") -> None:
        super().__init__()
        from opentelemetry import trace

        self._otel = trace.get_tracer(service_name)

    @contextlib.asynccontextmanager
    async def span(self, name: str, **attributes: Any) -> AsyncIterator[Span]:
        with self._otel.start_as_current_span(name) as otel_span:
            for k, v in attributes.items():
                if isinstance(v, (str, int, float, bool)):
                    otel_span.set_attribute(k, v)
            async with super().span(name, **attributes) as span:
                yield span


def build_tracer() -> ExecutionTracer:
    """OpenTelemetry when configured (endpoint set + SDK installed), in-memory otherwise."""
    import os

    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        try:
            import opentelemetry  # noqa: F401

            return OpenTelemetryTracer()
        except ImportError:
            pass
    return InMemoryTracer()
