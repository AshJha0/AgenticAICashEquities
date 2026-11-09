"""Runtime AI service-level objectives with rolling SPC alerts (0.9.0 B8).

Plain idea: the AI evaluation measures the model once, on a fixed set. In operation the model can
drift (a new build, a new prompt, different traffic), so every model call is also scored as it
happens, and an alert fires when a rate moves outside what the baseline makes plausible, not only
when someone changes a config.

Formal statement. For each ``purpose`` (planning, claims, narrative, critique, judge) the monitor
keeps a rolling window of the last ``window`` calls and tracks five indicators:

====================  ========================================================  ===============
indicator             definition (per call)                                     objective (SLO)
====================  ========================================================  ===============
plan_valid            planning reply parsed against the schema, no repair       >= 0.90
repair                the call needed a repair retry (degradation rung 2)       <= 0.20
fallback              answered by the deterministic fallback (rung 3)          <= 0.05
untraceable_number    claims dropped for a number not in the evidence /         <= 0.02
                      all proposed claims (fed by the reporter)
p95 latency           95th percentile of the window's latencies, per purpose    per-purpose ms
====================  ========================================================  ===============

Two kinds of alert are raised, and both are separate from each other:

* ``slo_breach``: the window value is on the wrong side of its objective (needs >= ``min_n`` calls).
* ``spc``: a Shewhart p-chart. With baseline proportion ``p0`` (frozen from the first
  ``baseline_n`` calls of that purpose, or given), a window of ``n`` calls is out of control when
  its rate ``p`` satisfies ``p > p0 + 3*sqrt(p0*(1-p0)/n)`` on the bad side (``<`` for plan_valid).
  ``p0`` is floored at ``0.5/baseline_n`` so a perfect baseline still has a finite limit.
  Latency uses an individuals chart on ``log(latency)``: out of control when the window mean exceeds
  the baseline mean by ``3*s/sqrt(n)``.

Each call is also exported as an OpenTelemetry GenAI span (semantic conventions
``gen_ai.operation.name``, ``gen_ai.request.model``, ``gen_ai.usage.input_tokens``,
``gen_ai.usage.output_tokens``) and the metrics ``gen_ai_client_operation_duration_ms`` and
``gen_ai_client_token_usage_total`` plus ``ceap_ai_slo_events_total`` / ``ceap_ai_slo_alerts_total``
in the process registry.

Worked example: planning baseline 20 calls with 1 repair, ``p0 = 0.05``; a window of 20 calls has
limit ``0.05 + 3*sqrt(0.05*0.95/20) = 0.196``; 4 repairs in 20 (0.20) is out of control and
also breaches the 0.20 objective only if it exceeds it, so here only the SPC alert fires.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ceap.observability.metrics import MetricsRegistry, metrics

RATES = ("plan_valid", "repair", "fallback", "untraceable_number")
# objective, and which direction is bad ("low": falling below is bad)
OBJECTIVES: dict[str, tuple[float, str]] = {
    "plan_valid": (0.90, "low"),
    "repair": (0.20, "high"),
    "fallback": (0.05, "high"),
    "untraceable_number": (0.02, "high"),
}
# p95 latency objective per purpose, ms. Hosted-API figures; a local CPU model is configured larger.
P95_MS: dict[str, float] = {"planning": 60_000, "claims": 30_000, "narrative": 45_000, "critique": 30_000, "judge": 15_000}


@dataclass(frozen=True)
class AICallEvent:
    purpose: str
    model: str
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    plan_valid: bool | None = None  # planning only
    repaired: bool = False
    fallback: bool = False
    claims_proposed: int = 0
    claims_untraceable: int = 0


@dataclass(frozen=True)
class Alert:
    kind: str  # slo_breach | spc
    purpose: str
    indicator: str
    value: float
    limit: float
    n: int
    at: float = field(default_factory=time.time)


def genai_span_attributes(event: AICallEvent, system: str = "ceap") -> dict[str, Any]:
    """OTel GenAI semantic-convention attributes for one call."""
    op = "chat"
    return {
        "gen_ai.operation.name": op,
        "gen_ai.system": system,
        "gen_ai.request.model": event.model,
        "gen_ai.response.model": event.model,
        "gen_ai.usage.input_tokens": event.input_tokens,
        "gen_ai.usage.output_tokens": event.output_tokens,
        "ceap.ai.purpose": event.purpose,
        "ceap.ai.plan_valid": event.plan_valid,
        "ceap.ai.repaired": event.repaired,
        "ceap.ai.fallback": event.fallback,
        "ceap.ai.latency_ms": round(event.latency_ms, 3),
    }


def event_from_response(request: Any, response: Any, latency_ms: float) -> AICallEvent:
    """Derive an event from an ``LLMRequest`` / ``LLMResponse`` pair (rungs from ``models.py``)."""
    from ceap.llm.models import RUNG_DETERMINISTIC, RUNG_REPAIR

    rungs = {d.rung for d in getattr(response, "degradations", ())}
    fallback = RUNG_DETERMINISTIC in rungs or bool(getattr(response, "fallback_reason", None))
    repaired = RUNG_REPAIR in rungs
    plan_valid = None
    if request.purpose == "planning":
        plan_valid = (
            not fallback
            and not repaired
            and getattr(response, "parsed", None) is not None
            and not getattr(response, "parse_errors", ())
        ) if getattr(request, "output_model", None) is not None else not fallback and not repaired
    return AICallEvent(
        purpose=request.purpose,
        model=str(response.model),
        latency_ms=latency_ms,
        input_tokens=int(response.input_tokens),
        output_tokens=int(response.output_tokens),
        plan_valid=plan_valid,
        repaired=repaired,
        fallback=fallback,
    )


def p95(values: list[float]) -> float:
    if not values:
        return float("nan")
    xs = sorted(values)
    k = max(0, math.ceil(0.95 * len(xs)) - 1)  # nearest-rank
    return xs[k]


class _Series:
    def __init__(self, window: int) -> None:
        self.window: dict[str, deque[float]] = {k: deque(maxlen=window) for k in (*RATES, "log_latency")}
        self.latency: deque[float] = deque(maxlen=window)
        self.baseline: dict[str, list[float]] = {k: [] for k in (*RATES, "log_latency")}


class AISLOMonitor:
    def __init__(
        self,
        *,
        window: int = 50,
        baseline_n: int = 20,
        min_n: int = 10,
        p95_ms: dict[str, float] | None = None,
        registry: MetricsRegistry | None = None,
        tracer: Any = None,
        baseline: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self.window, self.baseline_n, self.min_n = window, baseline_n, min_n
        self.p95_ms = {**P95_MS, **(p95_ms or {})}
        self.registry = registry or metrics
        self.tracer = tracer
        self.given_baseline = baseline or {}
        self._series: dict[str, _Series] = {}
        self.alerts: list[Alert] = []
        self.spans: deque[dict[str, Any]] = deque(maxlen=1000)

    def _values(self, e: AICallEvent) -> dict[str, float | None]:
        return {
            "plan_valid": None if e.plan_valid is None else float(e.plan_valid),
            "repair": float(e.repaired),
            "fallback": float(e.fallback),
            "untraceable_number": (e.claims_untraceable / e.claims_proposed) if e.claims_proposed else None,
            "log_latency": math.log(max(e.latency_ms, 1e-3)),
        }

    def record(self, event: AICallEvent) -> list[Alert]:
        s = self._series.setdefault(event.purpose, _Series(self.window))
        for k, v in self._values(event).items():
            if v is None:
                continue
            s.window[k].append(v)
            if len(s.baseline[k]) < self.baseline_n:
                s.baseline[k].append(v)
        s.latency.append(event.latency_ms)
        attrs = genai_span_attributes(event)
        self.spans.append({"name": f"chat {event.model}", "attributes": attrs})
        if self.tracer is not None:
            self.tracer.event("gen_ai.chat", **attrs)
        labels = {"purpose": event.purpose, "model": event.model}
        self.registry.observe("gen_ai_client_operation_duration_ms", event.latency_ms, **labels)
        self.registry.inc("gen_ai_client_token_usage_total", event.input_tokens, token_type="input", **labels)
        self.registry.inc("gen_ai_client_token_usage_total", event.output_tokens, token_type="output", **labels)
        for k, v in self._values(event).items():
            if v is not None and k in RATES and v > 0:
                self.registry.inc("ceap_ai_slo_events_total", 1, indicator=k, **labels)
        fired = self.check(event.purpose)
        for a in fired:
            self.registry.inc("ceap_ai_slo_alerts_total", 1, kind=a.kind, indicator=a.indicator, purpose=a.purpose)
        self.alerts.extend(fired)
        return fired

    def _p0(self, purpose: str, k: str) -> float | None:
        given = self.given_baseline.get(purpose, {}).get(k)
        if given is not None:
            return given
        base = self._series[purpose].baseline[k]
        if len(base) < self.baseline_n:
            return None
        return sum(base) / len(base)

    def check(self, purpose: str) -> list[Alert]:
        s = self._series.get(purpose)
        if s is None:
            return []
        out: list[Alert] = []
        for k in RATES:
            w = s.window[k]
            n = len(w)
            if n < self.min_n:
                continue
            p = sum(w) / n
            objective, bad = OBJECTIVES[k]
            if (bad == "high" and p > objective) or (bad == "low" and p < objective):
                out.append(Alert("slo_breach", purpose, k, round(p, 4), objective, n))
            p0 = self._p0(purpose, k)
            if p0 is None:
                continue
            floor = 0.5 / self.baseline_n
            p0c = min(max(p0, floor), 1 - floor)
            sigma = math.sqrt(p0c * (1 - p0c) / n)
            if bad == "high" and p > p0c + 3 * sigma:
                out.append(Alert("spc", purpose, k, round(p, 4), round(p0c + 3 * sigma, 4), n))
            if bad == "low" and p < p0c - 3 * sigma:
                out.append(Alert("spc", purpose, k, round(p, 4), round(p0c - 3 * sigma, 4), n))
        lat = list(s.latency)
        if len(lat) >= self.min_n:
            q = p95(lat)
            slo = self.p95_ms.get(purpose)
            if slo is not None and q > slo:
                out.append(Alert("slo_breach", purpose, "p95_latency_ms", round(q, 1), slo, len(lat)))
            base = s.baseline["log_latency"]
            if len(base) >= self.baseline_n:
                mu = sum(base) / len(base)
                sd = math.sqrt(sum((x - mu) ** 2 for x in base) / (len(base) - 1)) or 1e-9
                w = s.window["log_latency"]
                m = sum(w) / len(w)
                if m > mu + 3 * sd / math.sqrt(len(w)):
                    out.append(Alert("spc", purpose, "latency", round(math.exp(m), 1), round(math.exp(mu + 3 * sd / math.sqrt(len(w))), 1), len(w)))
        return out

    def snapshot(self) -> dict[str, Any]:
        """Window value of each indicator per purpose, with its objective (the SLO dashboard rows)."""
        out: dict[str, Any] = {}
        for purpose, s in self._series.items():
            row: dict[str, Any] = {}
            for k in RATES:
                w = s.window[k]
                row[k] = {"value": round(sum(w) / len(w), 4) if w else None, "n": len(w), "objective": OBJECTIVES[k][0]}
            lat = list(s.latency)
            row["p95_latency_ms"] = {"value": round(p95(lat), 1) if lat else None, "n": len(lat), "objective": self.p95_ms.get(purpose)}
            out[purpose] = row
        return out


#: process-wide monitor the router reports to when ``LLMRouter.slo`` is left as ``None`` and this is set
default_monitor: AISLOMonitor | None = None
