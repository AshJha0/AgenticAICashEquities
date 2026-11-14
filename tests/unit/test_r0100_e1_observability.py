"""AI SLO monitor, Prometheus exposition and JSON logging (0.10.0 E1 coverage for
``ceap.observability``). Each test uses its own registry, so the process metrics are untouched."""

from __future__ import annotations

import json
import logging
import math
import sys
from types import SimpleNamespace

import pytest

from ceap.llm.models import RUNG_DETERMINISTIC, RUNG_REPAIR
from ceap.observability import ai_slo
from ceap.observability import logging as obs_logging
from ceap.observability.metrics import MAX_SERIES_PER_METRIC, MetricsRegistry

# ------------------------------------------------------------------ AI SLO monitor


def _ev(purpose="claims", latency=100.0, **kw) -> ai_slo.AICallEvent:
    return ai_slo.AICallEvent(purpose=purpose, model="m-1", latency_ms=latency, **kw)


def _monitor(**kw) -> ai_slo.AISLOMonitor:
    return ai_slo.AISLOMonitor(window=20, baseline_n=20, min_n=10, registry=MetricsRegistry(), **kw)


def test_docstring_worked_example_fires_spc_but_not_the_objective():
    mon = _monitor()
    for i in range(20):  # baseline: 1 repair in 20, p0 = 0.05
        assert mon.record(_ev(repaired=i == 0)) == []
    fired = []
    for i in range(20):  # window: 4 repairs in 20 = 0.20, limit 0.05 + 3*sqrt(0.05*0.95/20) = 0.196
        fired = mon.record(_ev(repaired=i >= 16))
    assert [(a.kind, a.indicator, a.value, a.limit, a.n) for a in fired] == [
        ("spc", "repair", 0.2, 0.1962, 20)
    ]
    assert mon.snapshot()["claims"]["repair"] == {"value": 0.2, "n": 20, "objective": 0.20}


def test_objective_breaches_for_fallback_plan_validity_and_p95_latency():
    mon = _monitor(p95_ms={"planning": 500.0})
    fired = []
    for _ in range(10):
        fired = mon.record(_ev("planning", latency=900.0, plan_valid=False, fallback=True))
    kinds = {(a.kind, a.indicator) for a in fired}
    assert {
        ("slo_breach", "plan_valid"),
        ("slo_breach", "fallback"),
        ("slo_breach", "p95_latency_ms"),
    } <= kinds
    assert not any(a.kind == "spc" for a in fired)  # no baseline yet (10 < 20 calls)
    assert len(mon.spans) == 10 and mon.spans[0]["attributes"]["ceap.ai.fallback"] is True


def test_given_baseline_drives_spc_on_plan_validity_and_untraceable_numbers():
    mon = _monitor(baseline={"planning": {"plan_valid": 0.99, "untraceable_number": 0.0}})
    fired = []
    for i in range(10):
        fired = mon.record(_ev("planning", plan_valid=i % 2 == 0, claims_proposed=10, claims_untraceable=3))
    spc = {a.indicator: a for a in fired if a.kind == "spc"}
    assert spc["plan_valid"].value == 0.5 and spc["plan_valid"].limit < 0.99
    # p0 = 0 is floored at 0.5/20 = 0.025 so the limit stays finite
    floor = 0.025
    assert spc["untraceable_number"].limit == round(floor + 3 * math.sqrt(floor * (1 - floor) / 10), 4)


def test_latency_individuals_chart_fires_on_a_slow_window():
    mon = ai_slo.AISLOMonitor(window=10, baseline_n=10, min_n=10, registry=MetricsRegistry())
    for i in range(10):
        mon.record(_ev(latency=100.0 + i))  # baseline around 100 ms
    fired = []
    for _ in range(10):
        fired = mon.record(_ev(latency=1000.0))
    lat = [a for a in fired if a.indicator == "latency"]
    assert len(lat) == 1 and lat[0].value == pytest.approx(1000.0) and lat[0].limit < 200


def test_metrics_tracer_and_unknown_purpose():
    reg = MetricsRegistry()
    events: list = []
    tracer = SimpleNamespace(event=lambda name, **attrs: events.append((name, attrs)))
    mon = ai_slo.AISLOMonitor(registry=reg, tracer=tracer)
    mon.record(_ev(input_tokens=7, output_tokens=3, repaired=True))
    snap = reg.snapshot()
    labels = 'model="m-1",purpose="claims",token_type="input"'
    assert snap[f"gen_ai_client_token_usage_total{{{labels}}}"] == 7
    assert snap['ceap_ai_slo_events_total{indicator="repair",model="m-1",purpose="claims"}'] == 1
    assert events[0][0] == "gen_ai.chat" and events[0][1]["gen_ai.usage.output_tokens"] == 3
    assert mon.check("nobody") == []


def _resp(rungs=(), parsed=object(), parse_errors=(), fallback_reason=None):
    return SimpleNamespace(
        degradations=[SimpleNamespace(rung=r) for r in rungs],
        fallback_reason=fallback_reason,
        parsed=parsed,
        parse_errors=parse_errors,
        model="m-2",
        input_tokens=5,
        output_tokens=6,
    )


@pytest.mark.parametrize(
    ("output_model", "resp", "plan_valid", "repaired", "fallback"),
    [
        (object, _resp(), True, False, False),
        (object, _resp(parsed=None), False, False, False),
        (object, _resp(parse_errors=("bad",)), False, False, False),
        (object, _resp(rungs=(RUNG_REPAIR,)), False, True, False),
        (None, _resp(rungs=(RUNG_DETERMINISTIC,)), False, False, True),
        (None, _resp(fallback_reason="timeout"), False, False, True),
        (None, _resp(), True, False, False),
    ],
)
def test_event_from_response_derives_rungs_and_plan_validity(
    output_model, resp, plan_valid, repaired, fallback
):
    req = SimpleNamespace(purpose="planning", output_model=output_model)
    e = ai_slo.event_from_response(req, resp, 12.5)
    assert (e.plan_valid, e.repaired, e.fallback) == (plan_valid, repaired, fallback)
    assert (e.model, e.input_tokens, e.output_tokens, e.latency_ms) == ("m-2", 5, 6, 12.5)
    other = ai_slo.event_from_response(SimpleNamespace(purpose="narrative"), resp, 1.0)
    assert other.plan_valid is None


def test_p95_nearest_rank():
    assert math.isnan(ai_slo.p95([]))
    assert ai_slo.p95([5.0]) == 5.0
    assert ai_slo.p95([float(i) for i in range(1, 101)]) == 95.0
    assert ai_slo.p95([float(i) for i in range(1, 21)]) == 19.0


# ------------------------------------------------------------------ Prometheus exposition


def test_render_base_units_escaping_and_summary_shape():
    reg = MetricsRegistry()
    reg.inc("jobs", 2, job='a"b\\c\nd')
    reg.observe("step_ms", 1500.0, agent="x")
    reg.observe("step_ms", 500.0, agent="x")
    reg.observe("step_ms", float("nan"), agent="x")  # NaN is dropped, not counted
    text = reg.render()
    assert "# TYPE jobs_total counter" in text
    assert 'jobs_total{job="a\\"b\\\\c\\nd"} 2.0' in text
    assert "# TYPE step_seconds summary" in text
    assert 'step_seconds_count{agent="x"} 2.0' in text and 'step_seconds_sum{agent="x"} 2.0' in text
    assert "# TYPE step_seconds_max gauge" in text and 'step_seconds_max{agent="x"} 1.5' in text
    snap = reg.snapshot()
    assert snap['step_ms_min{agent="x"}'] == 500.0 and snap['step_ms_max{agent="x"}'] == 1500.0
    reg.reset()
    assert reg.render() == "" and reg.snapshot() == {}


def test_label_sets_are_capped_into_one_overflow_series():
    reg = MetricsRegistry()
    for i in range(MAX_SERIES_PER_METRIC + 5):
        reg.inc("tool_calls_total", tool=f"t{i}")
    snap = reg.snapshot()
    assert len(snap) == MAX_SERIES_PER_METRIC + 1
    assert snap['tool_calls_total{overflow="true"}'] == 5
    reg.inc("tool_calls_total", tool="t0")  # an existing series still counts normally
    assert reg.snapshot()['tool_calls_total{tool="t0"}'] == 2


def test_number_formatting_of_infinities():
    from ceap.observability.metrics import _num

    assert (_num(float("inf")), _num(float("-inf")), _num(float("nan")), _num(3)) == (
        "+Inf",
        "-Inf",
        "NaN",
        "3.0",
    )


# ------------------------------------------------------------------ JSON logging


def _record(msg="hello", **extra) -> logging.LogRecord:
    rec = logging.LogRecord("ceap.test", logging.INFO, __file__, 1, msg, None, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_json_formatter_carries_ids_and_redacts():
    token = obs_logging.REQUEST_ID.set("req-42")
    try:
        rec = _record("key sk-ant-api03-" + "x" * 40 + " used", task_id="t1", tool_id="tca.x")
        obs_logging.RequestIdFilter().filter(rec)
        out = json.loads(obs_logging.JsonFormatter().format(rec))
    finally:
        obs_logging.REQUEST_ID.reset(token)
    assert out["request_id"] == "req-42" and out["task_id"] == "t1" and out["tool_id"] == "tca.x"
    assert out["level"] == "INFO" and out["logger"] == "ceap.test"
    assert "x" * 40 not in out["message"]


def test_json_formatter_includes_redacted_exception_and_filter_keeps_explicit_id():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        rec = logging.LogRecord("ceap.test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    rec.request_id = "explicit"
    obs_logging.RequestIdFilter().filter(rec)
    out = json.loads(obs_logging.JsonFormatter().format(rec))
    assert out["request_id"] == "explicit" and "RuntimeError: boom" in out["exception"]


def test_configure_logging_adds_one_handler_only_when_none_exists(monkeypatch):
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", root.level)
    obs_logging.configure_logging("debug", json_lines=False)
    assert len(root.handlers) == 1 and root.level == logging.DEBUG
    handler = root.handlers[0]
    assert any(isinstance(f, obs_logging.RequestIdFilter) for f in handler.filters)
    assert not isinstance(handler.formatter, obs_logging.JsonFormatter)
    obs_logging.configure_logging()  # second call is a no-op
    assert root.handlers == [handler]
