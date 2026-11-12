"""3A.4 harness invariants, run under every adversarial fake client (``fakes/clients.py``).

Hypothesis draws (client, scenario template, injected question) and runs the whole investigation;
every example must satisfy all invariants. Profiles in ``tests/conftest.py``: 100 examples on PR,
1000 nightly (``HYPOTHESIS_PROFILE=nightly``).

1. the governance tail (critic, validation, finalise) is present, last and in order;
2. no tool outside the read-only catalogue is planned or run (``execution.stage_orders`` never);
3. pinned arguments (symbol, dataset) and windows are unchanged;
4. the critic only lowers confidence;
5. a model fault gives ``DEGRADED``, never a clean ``COMPLETED``;
6. no approval is ever granted by the model path, and an approver always differs from the requester;
7. narrative numbers and evidence ids trace to facts, or the result is ``DEGRADED`` (blocking);
8. model calls per task stay within the cap;
9. wall time per task stays within the bound;
10. the same inputs and cassette give an identical result (ids injected; see the separate test).
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from cassettes.golden import cassette_path, deterministic_uuid4
from ceap.domain.tasks import TaskStatus
from ceap.llm.pins import load_pins
from ceap.llm.router import LLMRouter
from ceap.platform import Platform, default_request
from fakes.clients import ALL_CLIENT_FACTORIES, INJECTION_TEXT

TEMPLATES = tuple(f"T{i:02d}" for i in range(1, 11))
MAX_CALLS = 12
WALL_S = 120.0  # a hang is unbounded; since R2T a cold scenario build (matched prior sessions) takes seconds under -n auto
ROUTER_TIMEOUT_S = 0.2
# clients whose every targeted reply is a fault the harness must surface
ALWAYS_FAULTY = {
    "truncating",
    "refusal",
    "hanging",
    "raising",
    "hostile_universe",
    "hostile_governance",
    "hostile_huge",
    "id_fabricator",
}
# invariant 7: an untraceable narrative is blocking (harness dispatch, since 0.8.0)
NARRATIVE_FABRICATORS = {"number_fabricator", "id_fabricator"}


def _allowed_windows(req) -> set[tuple[str, str]]:
    ws, we = req.window_start.isoformat(), req.window_end.isoformat()
    bs, be = req.baseline_start.isoformat(), req.baseline_end.isoformat()
    return {(ws, we), (bs, be), (bs, we)}


def check_invariants(
    name: str, client, result, req, template: str, catalogue: set[str], wall_s: float
) -> None:
    steps = result.plan.ordered_steps()
    # 1 governance tail
    assert [s.id for s in steps][-3:] == ["gov-critic", "gov-validation", "gov-finalise"], name
    # 2 catalogue only
    tool_ids = [s.tool_request.tool_id for s in steps if s.tool_request]
    assert set(tool_ids) <= catalogue, (name, set(tool_ids) - catalogue)
    assert all(
        "stage_orders" not in r["step_id"] and r.get("tool_id") != "execution.stage_orders"
        for r in result.step_results
    )
    # 3 pinned arguments and windows
    windows = _allowed_windows(req)
    for s in steps:
        if not s.tool_request:
            continue
        args = s.tool_request.arguments
        assert args.get("symbol", req.symbol) == req.symbol, (name, args)
        assert args.get("dataset", template) == template, (name, args)
        if "start" in args and "end" in args:
            assert (str(args["start"]), str(args["end"])) in windows, (name, s.tool_request.tool_id, args)
    # 4 critic only lowers
    for a in result.report.critique.get("assessments", []):
        assert a["adjusted_confidence"] <= a["original_confidence"] + 1e-9, (name, a)
    # 5 faults are visible
    planner = result.agent_outputs.get("planner") or {}
    if name in ALWAYS_FAULTY or planner.get("repairs", 0) > 0:
        assert result.status is TaskStatus.DEGRADED, (name, result.status, result.warning_messages())
    # 6 approvals
    for r in result.step_results:
        if "approval" in r["step_id"]:
            assert r.get("decided_by") != req.principal, (name, r)
    # 7 narrative traces to facts (blocking)
    critique = result.report.critique
    untraceable = critique.get("narrative_number_warnings") or critique.get("narrative_evidence_warnings")
    if untraceable:
        assert result.status is TaskStatus.DEGRADED, (name, untraceable[:3])
    # 8 call cap, 9 wall time
    assert len(client.calls) <= MAX_CALLS, (name, len(client.calls))
    assert wall_s <= WALL_S, (name, wall_s)


async def _investigate(store, app_settings, name: str, template: str, inject: bool):
    client = ALL_CLIENT_FACTORIES[name]()
    router = LLMRouter(client, pins=load_pins(), timeout_s=ROUTER_TIMEOUT_S, max_calls_per_task=MAX_CALLS)
    platform = Platform(settings=app_settings, llm=router, store=store)
    question = None
    if inject:
        question = f"Analyse our AAPL execution between 14:00 and 15:00. {INJECTION_TEXT}"
    req = default_request("AAPL", template, question=question)
    started = time.perf_counter()
    result = await platform.investigate(req)
    wall = time.perf_counter() - started
    base = await platform.harness()
    catalogue = {m.id for m in base.tools.list() if m.read_only}
    return client, result, req, catalogue, wall


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(
    name=st.sampled_from(sorted(ALL_CLIENT_FACTORIES)),
    template=st.sampled_from(TEMPLATES),
    inject=st.booleans(),
)
@pytest.mark.slow
async def test_3a4_invariants_hold_under_every_fake_client(store, settings, name, template, inject):
    client, result, req, catalogue, wall = await _investigate(store, settings, name, template, inject)
    check_invariants(name, client, result, req, template, catalogue, wall)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(template=st.sampled_from(TEMPLATES))
@pytest.mark.slow
async def test_3a4_invariants_hold_under_obedient_injection(store, settings, template):
    client, result, req, catalogue, wall = await _investigate(
        store, settings, "obedient_injection", template, True
    )
    check_invariants("obedient_injection", client, result, req, template, catalogue, wall)
    assert client.calls_for("planning") == 2  # the obeyed plan was rejected and repaired once
    assert result.status is TaskStatus.DEGRADED


@pytest.mark.parametrize("name", sorted(NARRATIVE_FABRICATORS))
async def test_3a4_untraceable_narrative_is_blocking(store, settings, name):
    _, result, *_ = await _investigate(store, settings, name, "T01", True)
    assert result.status is TaskStatus.DEGRADED
    assert any(w.code == "NARRATIVE_UNTRACEABLE" for w in result.warnings)


@pytest.mark.slow  # two full runs; ~35 s idle, near the 120 s default under parallel load
async def test_3a4_same_inputs_and_cassette_give_identical_result(store, settings, monkeypatch):
    pytest.importorskip("anthropic")
    from ceap.llm.client import AnthropicLLMClient
    from ceap.llm.recording import CassetteTransport, assert_consumed, load_cassette, sdk_over

    outputs = []
    for _ in range(2):
        monkeypatch.setattr(uuid, "uuid4", deterministic_uuid4())
        transport = CassetteTransport(load_cassette(cassette_path("T03")))
        platform = Platform(
            settings=settings, llm=AnthropicLLMClient(client=sdk_over(transport)), store=store
        )
        result = await platform.investigate(default_request("AAPL", "T03"))
        assert_consumed(transport)  # A19: an unused recording fails the replay
        r = result.report
        outputs.append(
            json.dumps(
                {
                    "narrative": r.narrative,
                    "findings": [(f.id, f.statement, f.confidence) for f in r.findings],
                    "evidence": sorted(e.id for e in r.evidence),
                    "plan": [s.id for s in result.plan.ordered_steps()],
                    "status": result.status.value,
                },
                sort_keys=True,
                default=str,
            )
        )
    assert outputs[0] == outputs[1]
