"""Execution tracing.

``InMemoryTracer`` records a tree of spans per investigation and is what the
API returns under ``/investigations/{id}/trace``. The *current span* lives in
a ``contextvars.ContextVar`` so spans opened by concurrent asyncio tasks
(tool batches, agents calling tools) get the right parent and events attach
to the span that emitted them. ``OpenTelemetryTracer`` forwards the same
The tracer that opened the current span is kept in a second context variable, so code that has no
tracer handle (the LLM router) can add a child span: :func:`current_tracer`. Each routed model call
becomes one ``gen_ai.chat`` span under the agent span, with OpenTelemetry GenAI attribute names.
"""

from __future__ import annotations

import contextlib
import contextvars
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ceap.domain.common import new_id, utc_now

_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar("ceap_current_span", default=None)
_current_tracer: contextvars.ContextVar[ExecutionTracer | None] = contextvars.ContextVar(
    "ceap_current_tracer", default=None
)


def current_tracer() -> ExecutionTracer | None:
    """The tracer whose span is open in this context, or ``None`` outside any span."""
    return _current_tracer.get()


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
    _t0: float = field(default_factory=lambda: time.perf_counter(), repr=False)  # late-bound: replay injects it

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
    def __init__(self, trace_id: str | None = None, max_spans: int = 10_000) -> None:
        self.trace_id = trace_id or new_id("TRACE")
        self.spans: list[Span] = []
        self.max_spans = max_spans
        self.dropped_spans = 0

    @contextlib.asynccontextmanager
    async def span(self, name: str, **attributes: Any) -> AsyncIterator[Span]:
        parent = _current_span.get()
        span = Span(
            id=new_id("SPAN"),
            name=name,
            parent_id=parent.id if parent else None,
            started_at=utc_now().isoformat(),
            attributes=dict(attributes),
        )
        if len(self.spans) < self.max_spans:
            self.spans.append(span)
        else:
            self.dropped_spans += 1
        token = _current_span.set(span)
        tracer_token = _current_tracer.set(self)
        try:
            yield span
        except BaseException as exc:
            span.status = "ERROR"
            span.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            span.ended_at = utc_now().isoformat()
            span.duration_ms = round((time.perf_counter() - span._t0) * 1000.0, 3)
            _current_tracer.reset(tracer_token)
            _current_span.reset(token)

    def event(self, name: str, **attributes: Any) -> None:
        payload = {"name": name, "timestamp": utc_now().isoformat(), **attributes}
        current = _current_span.get()
        if current is not None:
            current.events.append(payload)
        elif len(self.spans) < self.max_spans:
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
        return {
            "trace_id": self.trace_id,
            "spans": [s.to_dict() for s in self.spans],
            "dropped_spans": self.dropped_spans,
        }

    def summary(self) -> dict[str, Any]:
        by_name: dict[str, dict[str, float]] = {}
        for s in self.spans:
            entry = by_name.setdefault(s.name.split(":")[0], {"count": 0, "total_ms": 0.0, "errors": 0})
            entry["count"] += 1
            entry["total_ms"] += s.duration_ms or 0.0
            entry["errors"] += 1 if s.status == "ERROR" else 0
        return {"trace_id": self.trace_id, "span_count": len(self.spans), "by_name": by_name}
