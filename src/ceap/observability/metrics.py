"""Minimal process metrics with Prometheus text exposition.

Deliberately dependency-free; swap for ``prometheus_client`` by replacing
this module (the API only calls ``metrics.render()``).
"""

from __future__ import annotations

import threading
from collections import defaultdict


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._hist: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        with self._lock:
            self._counters[(name, tuple(sorted(labels.items())))] += value

    def observe(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self._hist[(name, tuple(sorted(labels.items())))].append(value)

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            out = {f"{n}{_fmt(lbl)}": v for (n, lbl), v in self._counters.items()}
            for (n, lbl), values in self._hist.items():
                if values:
                    out[f"{n}_count{_fmt(lbl)}"] = float(len(values))
                    out[f"{n}_sum{_fmt(lbl)}"] = float(sum(values))
                    out[f"{n}_max{_fmt(lbl)}"] = float(max(values))
            return out

    def render(self) -> str:
        lines = [f"{k} {v}" for k, v in sorted(self.snapshot().items())]
        return "\n".join(lines) + ("\n" if lines else "")

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._hist.clear()


def _fmt(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}"


metrics = MetricsRegistry()
