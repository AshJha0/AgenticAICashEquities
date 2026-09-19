"""Minimal process metrics with Prometheus text exposition.

Deliberately dependency-free and bounded: histograms keep running
count/sum/min/max (no sample lists), label values are escaped, and the
number of distinct label sets per metric is capped so an unbounded label
(e.g. a tool id taken from a plan) cannot grow memory without limit.
Swap for ``prometheus_client`` by replacing this module (the API only
calls ``metrics.render()``).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

MAX_SERIES_PER_METRIC = 500
_OVERFLOW_LABEL = (("overflow", "true"),)

Labels = tuple[tuple[str, str], ...]


@dataclass
class _Summary:
    count: int = 0
    total: float = 0.0
    minimum: float = float("inf")
    maximum: float = float("-inf")

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[Labels, float]] = {}
        self._summaries: dict[str, dict[Labels, _Summary]] = {}

    @staticmethod
    def _labels(labels: dict[str, str]) -> Labels:
        return tuple(sorted((k, str(v)) for k, v in labels.items()))

    @staticmethod
    def _series(table: dict[str, dict[Labels, Any]], name: str, labels: Labels) -> Labels:
        series = table.setdefault(name, {})
        if labels in series or len(series) < MAX_SERIES_PER_METRIC:
            return labels
        return _OVERFLOW_LABEL

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        with self._lock:
            key = self._series(self._counters, name, self._labels(labels))
            self._counters[name][key] = self._counters[name].get(key, 0.0) + value

    def observe(self, name: str, value: float, **labels: str) -> None:
        if value != value:  # NaN
            return
        with self._lock:
            key = self._series(self._summaries, name, self._labels(labels))
            self._summaries[name].setdefault(key, _Summary()).observe(float(value))

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            out: dict[str, float] = {}
            for name, series in self._counters.items():
                for labels, value in series.items():
                    out[f"{name}{_fmt(labels)}"] = value
            for name, summaries in self._summaries.items():
                for labels, s in summaries.items():
                    out[f"{name}_count{_fmt(labels)}"] = float(s.count)
                    out[f"{name}_sum{_fmt(labels)}"] = s.total
                    out[f"{name}_max{_fmt(labels)}"] = s.maximum
                    out[f"{name}_min{_fmt(labels)}"] = s.minimum
            return out

    def render(self) -> str:
        with self._lock:
            lines: list[str] = []
            for name in sorted(self._counters):
                lines.append(f"# TYPE {name} counter")
                for labels, value in sorted(self._counters[name].items()):
                    lines.append(f"{name}{_fmt(labels)} {value}")
            for name in sorted(self._summaries):
                lines.append(f"# TYPE {name} summary")
                for labels, s in sorted(self._summaries[name].items()):
                    lines.append(f"{name}_count{_fmt(labels)} {float(s.count)}")
                    lines.append(f"{name}_sum{_fmt(labels)} {s.total}")
                    lines.append(f"{name}_max{_fmt(labels)} {s.maximum}")
        return "\n".join(lines) + ("\n" if lines else "")

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._summaries.clear()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt(labels: Labels) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in labels) + "}"


metrics = MetricsRegistry()
