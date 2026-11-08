"""Minimal process metrics with Prometheus text exposition.

Deliberately dependency-free and bounded: histograms keep running
count/sum/min/max (no sample lists), label values are escaped, and the
number of distinct label sets per metric is capped so an unbounded label
(e.g. a tool id taken from a plan) cannot grow memory without limit.
Swap for ``prometheus_client`` by replacing this module (the API only
calls ``metrics.render()``).

Exposition (R4 item 4.8) passes ``promtool check metrics``: every family has
``# HELP`` and ``# TYPE``; counters end in ``_total``; durations recorded in
milliseconds (``*_ms``) are exported in base units (``*_seconds``); a summary
carries only ``_count`` and ``_sum``, and its maximum is a separate gauge
family ``*_seconds_max``. Internal names (``inc``/``observe``/``snapshot``)
are unchanged.
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
                family = name if name.endswith("_total") else f"{name}_total"
                lines.append(f"# HELP {family} {_help(name)}")
                lines.append(f"# TYPE {family} counter")
                for labels, value in sorted(self._counters[name].items()):
                    lines.append(f"{family}{_fmt(labels)} {_num(value)}")
            for name in sorted(self._summaries):
                family, scale = _base_unit(name)
                series = sorted(self._summaries[name].items())
                lines.append(f"# HELP {family} {_help(name)}")
                lines.append(f"# TYPE {family} summary")
                for labels, s in series:
                    lines.append(f"{family}_count{_fmt(labels)} {_num(float(s.count))}")
                    lines.append(f"{family}_sum{_fmt(labels)} {_num(s.total * scale)}")
                lines.append(f"# HELP {family}_max Largest observation of {family}")
                lines.append(f"# TYPE {family}_max gauge")
                for labels, s in series:
                    lines.append(f"{family}_max{_fmt(labels)} {_num(s.maximum * scale)}")
        return "\n".join(lines) + ("\n" if lines else "")

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._summaries.clear()


#: optional help text per internal metric name; anything else gets a generic line
HELP: dict[str, str] = {
    "ceap_http_requests_total": "HTTP requests by method, route template and status code",
    "ceap_http_errors_total": "HTTP responses with status >= 400 by route template and status class",
    "ceap_http_request_duration_seconds": "HTTP request latency",
    "ceap_tool_calls_total": "MCP tool calls by tool and outcome",
    "ceap_tool_latency_ms": "MCP tool call latency",
    "ceap_agent_duration_ms": "Agent step duration",
    "ceap_investigation_duration_ms": "End-to-end task duration",
}


def _help(name: str) -> str:
    return HELP.get(name, f"CEAP metric {name}")


def _base_unit(name: str) -> tuple[str, float]:
    """Prometheus wants base units: ``foo_ms`` is exported as ``foo_seconds`` with values / 1000."""
    if name.endswith("_ms"):
        return name[:-3] + "_seconds", 0.001
    return name, 1.0


def _num(value: float) -> str:
    if value != value:
        return "NaN"
    if value in (float("inf"), float("-inf")):
        return "+Inf" if value > 0 else "-Inf"
    return repr(float(value))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt(labels: Labels) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in labels) + "}"


metrics = MetricsRegistry()
