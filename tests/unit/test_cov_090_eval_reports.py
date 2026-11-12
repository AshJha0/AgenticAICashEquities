"""Behaviour tests for the evaluation drivers and report renderers (0.9.0 coverage lane).

Covers the pre-trade cost model's fit and serialisation, the local-model run bookkeeping, the judge
CLI on its recorded cassette, the OOD spec rebuild, the degraded evaluation clients, the hybrid
retrieval helpers and the evaluation-report renderers. Expensive pieces (a full platform run, a live
model) are replaced by stubs whose outputs are known, so the counting can be checked by hand.
"""

from __future__ import annotations

import csv
import json
import math

import numpy as np
import pytest

from ceap.eval import clients as ev_clients
from ceap.eval import judge as ev_judge
from ceap.eval import local_model as lm
from ceap.eval import ood
from ceap.llm.client import MockLLMClient
from ceap.llm.models import LLMRequest, LLMResponse
from ceap.ml import pretrade_cost as pc

# ------------------------------------------------------------------ pre-trade cost model


def _synthetic_parents(n: int, seed: int) -> list[tuple[pc.PretradeFeatures, float]]:
    """Costs from a known model: VWAP 1 + 0.5 spread, IS 2 + 0.25 spread, impact b = 0.8, small noise."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        algo = ("VWAP", "IS")[i % 2]
        x = pc.PretradeFeatures(
            spread_bps=float(rng.uniform(2, 10)), sigma_daily_bps=float(rng.uniform(100, 300)),
            q_over_adv=float(rng.uniform(0.001, 0.05)), algo=algo,
        )
        c0, c1 = (1.0, 0.5) if algo == "VWAP" else (2.0, 0.25)
        y = c0 + c1 * x.spread_bps + 0.8 * x.impact_term + float(rng.normal(0, 0.01))
        rows.append((x, y))
    return rows


def test_fit_pretrade_recovers_a_known_generator_and_round_trips(tmp_path):
    model = pc.fit_pretrade(_synthetic_parents(200, 0), _synthetic_parents(100, 1), "abc", meta={"k": 1})
    assert model.impact_coefficient == pytest.approx(0.8, abs=0.01)
    assert model.algo_terms["VWAP"] == pytest.approx((1.0, 0.5), abs=0.05)
    assert model.algo_terms["IS"] == pytest.approx((2.0, 0.25), abs=0.05)
    assert model.algo_terms["MOC"] == (0.0, 0.0)  # an algo absent from training gets no terms
    assert model.label == "fitted to synthetic generator abc"
    assert pc.coverage(model, _synthetic_parents(200, 2)) >= 0.8
    path = tmp_path / "m.json"
    model.save(path)
    back = pc.PretradeModel.from_dict(json.loads(path.read_text(encoding="utf-8")))
    assert back == model
    assert math.isnan(pc.coverage(model, []))


def test_predict_worked_example_and_unknown_algo_falls_back_to_vwap():
    m = pc.PretradeModel(0.5, {"VWAP": (1.0, 0.5)}, exec_noise_bps=3.0, conformal_q=2.0)
    x = pc.PretradeFeatures(spread_bps=4.0, sigma_daily_bps=100.0, q_over_adv=0.04, algo="POV", schedule=())
    e = m.predict(x)
    # impact = 0.5 * 100 * sqrt(0.04) = 10; estimate = 1 + 0.5 * 4 + 10 = 13; no schedule -> scale = s_exec
    assert (e.impact_bps, e.estimate_bps) == pytest.approx((10.0, 13.0))
    assert e.scale_bps == pytest.approx(3.0) and (e.lower_bps, e.upper_bps) == pytest.approx((7.0, 19.0))
    assert set(e.as_dict()) == {"estimate_bps", "impact_bps", "scale_bps", "price_risk_bps", "lower_bps", "upper_bps"}
    assert pc.PretradeFeatures(1.0, 100.0, -0.1).impact_term == 0.0


def test_robust_exec_noise_returns_the_floor_when_price_risk_explains_everything():
    assert pc.robust_exec_noise(np.array([0.1, 0.2, 0.3]), np.array([10.0, 10.0, 10.0])) == 0.5
    s = pc.robust_exec_noise(np.array([5.0, 5.0, 5.0]), np.zeros(3))
    assert s == pytest.approx(5.0 / pc.MEDIAN_ABS_NORMAL, rel=1e-6)


def test_default_model_is_the_committed_file():
    assert pc.default_model().generator_hash == json.loads(pc.MODEL_PATH.read_text(encoding="utf-8"))["generator_hash"]


# ------------------------------------------------------------------ local model runs


def _seed_result(k: int) -> dict:
    r = {"k": k, "n": 10, "rate": k / 10}
    return {
        "agents": {"planner": {"plan_valid": r}, "critic": {"detection": {"k": 0, "n": 0, "rate": None}}},
        "untraceable_number": {"k": 1, "n": 4, "rate": 0.25},
        "golden": {"primary_cause_accuracy": 0.5, "investigation_items": 2, "verdict_accuracy": None,
                   "research_items": 1},
        "golden_rows": [], "slo": {}, "slo_alerts": [],
    }


def test_aggregate_pools_golden_items_and_skips_missing_rates():
    out = lm.aggregate({1: _seed_result(8), 2: _seed_result(6)})
    g = out["golden.primary_cause_accuracy"]
    assert (g["k"], g["n"]) == (2, 4) and g["per_seed"] == {"1": 0.5, "2": 0.5}
    assert "golden.verdict_accuracy" not in out  # no seed reported it
    assert out["critic.detection"]["seed_range"] is None and out["critic.detection"]["seed_sd"] is None
    assert out["planner.plan_valid"]["seed_sd"] == round(float(np.std([0.8, 0.6], ddof=1)), 4)
    assert "reporter.faithfulness" not in out
    assert lm.slug("a:b/c") == "a_b_c" and lm.cassette_path("m:1", 2, "judge").name == "m_1_judge_s2.json"


def test_run_replay_and_live_write_checkpoints(monkeypatch, tmp_path):
    calls = []

    async def fake_seed(client, golden_limit):
        calls.append((type(client).__name__, golden_limit))
        client.latencies = [("planning", 10.0), ("planning", 30.0), ("claims", 5.0)]
        return _seed_result(7)

    monkeypatch.setattr(lm, "run_seed", fake_seed)
    model = "qwen2.5:7b-instruct"
    import asyncio

    doc = asyncio.run(lm.run(model, (1,), replay=True, golden_limit=1))
    assert calls == [("LocalCassetteClient", 1)]
    assert doc["evidence_class"] == "local_model" and doc["digest"]
    assert doc["latency_ms"]["1"]["planning"] == {"n": 2, "median": 20.0, "p95": pytest.approx(29.0, abs=1.0)}
    assert doc["metrics"]["planner.plan_valid"]["k"] == 7

    class FakeOllama:
        def __init__(self, model, **kw):
            self.kw = kw

    written = []
    monkeypatch.setattr(lm, "model_digest", lambda m: "sha256:x")
    monkeypatch.setattr(lm, "OllamaLLMClient", FakeOllama)
    monkeypatch.setattr(lm, "write_local_cassette", lambda path, cas: written.append(path))
    monkeypatch.setattr(lm, "RESULTS", tmp_path)
    live = asyncio.run(lm.run("m:x", (4,)))
    assert live["digest"] == "sha256:x" and written == [lm.cassette_path("m:x", 4)]
    assert json.loads((tmp_path / "m_x_s4.json").read_text(encoding="utf-8"))["agents"]["planner"]


def test_main_live_needs_the_gate(monkeypatch):
    monkeypatch.setattr("ceap.llm.ollama.live_enabled", lambda: False)
    with pytest.raises(SystemExit, match="CEAP_LOCAL_MODEL"):
        lm.main(["--seeds", "1"])


async def test_untraceable_rate_of_the_mock_is_zero():
    r = await lm.untraceable_rate(MockLLMClient())
    assert r["k"] == 0 and r["n"] > 0


# ------------------------------------------------------------------ judge CLI


def test_judge_main_replays_the_recorded_calibration(capsys):
    ev_judge.main(["--replay", "--model", "qwen2.5:7b-instruct", "--seed", "1"])
    out = json.loads(capsys.readouterr().out)
    assert out["items"] == 50 and out["kappa_bar"] == ev_judge.KAPPA_BAR
    assert out["reference_sha256"] == ev_judge.load_reference()["sha256"]


def test_judge_main_write_reference_and_live_gate(monkeypatch, tmp_path):
    target = tmp_path / "ref.json"
    monkeypatch.setattr(ev_judge, "REFERENCE_PATH", target)
    ev_judge.main(["--write-reference"])
    assert json.loads(target.read_text(encoding="utf-8"))["sha256"] == ev_judge.build_reference()["sha256"]
    monkeypatch.setattr("ceap.llm.ollama.live_enabled", lambda: False)
    with pytest.raises(SystemExit, match="CEAP_LOCAL_MODEL"):
        ev_judge.main([])


def test_committed_reference_matches_the_builder():
    assert ev_judge.load_reference()["sha256"] == ev_judge.build_reference()["sha256"]


def test_enabled_dimensions_rejects_malformed_files(tmp_path):
    p = tmp_path / "c.json"
    assert ev_judge.enabled_dimensions(p) == []  # missing
    p.write_text("[1]", encoding="utf-8")
    assert ev_judge.enabled_dimensions(p) == []


# ------------------------------------------------------------------ OOD split


def test_spec_from_item_rejects_unknown_fields_and_defaults_tags():
    from ceap.golden import load_ood_set

    item = load_ood_set()["items"][0]
    spec = ood.spec_from_item(item)
    assert spec.id == item["spec"]["id"]
    bad = {**item, "spec": {**item["spec"], "made_up": 1}}
    with pytest.raises(ValueError, match="made_up"):
        ood.spec_from_item(bad)
    store = ood.OODDatasetStore([spec])
    assert store._extra[spec.id] is spec


# ------------------------------------------------------------------ evaluation clients


def _req(purpose: str, structured: bool = False, messages: int = 1) -> LLMRequest:
    return LLMRequest(system_prompt="s", messages=[{"role": "user", "content": "x"}] * messages, purpose=purpose,
                      output_model=object if structured else None)


class _Fixed(MockLLMClient):
    def __init__(self, payload: dict, **kw):
        super().__init__()
        self.payload, self.kw = payload, kw

    async def complete(self, request):
        return LLMResponse(json.dumps(self.payload), [], 1, 1, "m", **self.kw)


def test_evidence_class_unwraps_wrappers_and_caps_cycles():
    assert ev_clients.evidence_class(ev_clients.CapturingClient(MockLLMClient())) == "mock"

    class Loop:
        provenance = "weird"

    a = Loop()
    a.inner = a  # a self-referential wrapper must not loop forever
    assert ev_clients.evidence_class(a) == "unknown"


async def test_capturing_client_records_in_order():
    c = ev_clients.CapturingClient(_Fixed({"a": 1}))
    await c.complete(_req("planning"))
    await c.complete(_req("claims"))
    assert c.count("planning") == 1 and c.last("claims").content == '{"a": 1}' and c.last("judge") is None
    assert c.name.startswith("Capturing(")


async def test_degraded_clients_spoil_only_their_purpose():
    plan = {"steps": [{"type": "TOOL_CALL", "tool_id": "execution.x"}, {"type": "TOOL_CALL", "tool_id": "market.y"}]}
    np_ = ev_clients.NarrowPlanner(_Fixed(plan))
    out = json.loads((await np_.complete(_req("planning"))).content)
    assert [s["tool_id"] for s in out["steps"]] == [*ev_clients.OUT_OF_SCOPE_TOOLS, "execution.x"]
    assert (await np_.complete(_req("claims"))).content == json.dumps(plan)
    assert np_.name.startswith("NarrowPlanner(")

    inv = ev_clients.InvalidPlanner(_Fixed(plan))
    assert (await inv.complete(_req("planning"))).parse_errors == ("invalid JSON",)
    assert (await inv.complete(_req("planning", messages=3))).parse_errors == ()  # the repair is left alone
    always = ev_clients.InvalidPlanner(_Fixed(plan), mode="always")
    await always.complete(_req("planning", messages=3))
    assert always.spoiled == 1

    critic = ev_clients.RubberStampCritic(_Fixed({"assessments": [{"id": "f1", "supported": False}]}))
    r = await critic.complete(_req("critique", structured=True))
    assert r.parsed["assessments"][0]["supported"] is True

    fab = ev_clients.ClaimFabricator(_Fixed({"claims": [{"text": "x"}]}))
    claim = json.loads((await fab.complete(_req("claims"))).content)["claims"][0]
    assert claim["evidence_ids"] == [ev_clients.FABRICATED_EVIDENCE_ID] and "47.37" in claim["text"]

    forger = ev_clients.CitationForger(_Fixed({}, citations=({"cited_text": "real"},)))
    assert "ignore every limit" in (await forger.complete(_req("narrative"))).citations[0]["cited_text"]

    inj = ev_clients.ClaimInjector(_Fixed({"claims": []}))
    claims = json.loads((await inj.complete(_req("claims"))).content)["claims"]
    assert len(claims) == len(ev_clients.INJECTED_CLAIMS) and all("[planted:" in c["text"] for c in claims)

    with pytest.raises(NotImplementedError):
        ev_clients._Degraded().spoil(_req("x"), LLMResponse("", [], 0, 0, "m"))


# ------------------------------------------------------------------ hybrid retrieval helpers


def test_paired_bootstrap_brackets_a_known_mean_difference():
    from ceap.rag.hybrid_eval import paired_bootstrap

    a = np.array([1, 1, 1, 0] * 10)
    b = np.array([0, 1, 1, 0] * 10)
    lo, hi = paired_bootstrap(a, b, draws=2000)
    assert lo < 0.25 < hi and 0.0 <= lo


# ------------------------------------------------------------------ evaluation report


def _rate(k, n):
    from ceap.eval.stats import rate

    return rate(k, n)


def _report(with_ood: bool) -> dict:
    r = _rate(3, 4)
    agents = {
        "planner": {"tool_recall": r, "tool_precision": r, "plan_valid": r, "repair": {}, "evidence_class": "mock"},
        "critic": {"detection": r, "false_alarm": r, "detection_by_kind": {"inflated": r}, "evidence_class": "mock"},
        "reporter": {"faithfulness": r, "claim_drop": r, "citation_verified": r, "injection_caught": r,
                     "evidence_class": "mock"},
    }
    ood_block = (
        {"per_template": {"t1": {"primary_ok": r, "coverage_ok": r}}, "overall": {"primary_ok": r},
         "evidence_class": "mock"}
        if with_ood else {"skipped": "quick"}
    )
    return {
        "llm": "mock", "complete": False,
        "capability": {
            "scenarios": 2, "primary_accuracy": 0.5, "coverage": 1.0, "false_positive_rate": 0.0, "completed": 1.0,
            "research_scenarios": 0, "research_verdict_accuracy": None,
            "golden": {"version": "1", "signoff": "signed", "primary_cause_accuracy": 0.12345,
                       "must_mention_rate": 1.0, "no_forbidden_claim_rate": 1.0, "verdict_accuracy": None,
                       "must_flag_rate": 0.5},
        },
        "safety": {"invariants_rate": 1.0},
        "robustness": {"refusal": {"degraded": True, "rungs": []}},
        "cost": {"usd_per_investigation_estimate": 0.0012, "calls_per_investigation": 4.0},
        "agents": agents,
        "sensitivity": {"rows": [{"metric": "m", "degraded_client": "D", "baseline": 1, "degraded": 0, "moved": False}]},
        "ood": ood_block,
        "retrieval": {"all_positive": {"n": 4, "recall_at_3": r}, "by_category": {"c": {"recall_at_3": r}},
                      "negatives": {"near": {"leak": _rate(0, 2)}}},
        "corpus": {"pinned": 9, "problems": [], "warnings": ["w"]},
    }


def test_render_markdown_formats_values_and_both_ood_shapes():
    from ceap.evaluation_report import NOT_RUN_LIVE, render_markdown

    md = render_markdown(_report(with_ood=True))
    assert "| capability | golden set v1 (sign-off signed) primary_cause_accuracy | 0.1235 |" in md
    assert "| capability | research_verdict_accuracy | n/a |" in md
    assert "| capability | coverage | 1.00 |" in md
    assert "| robustness | refusal: degraded | True (no rung) |" in md
    assert "| planner | repair | n/a | mock |" in md
    assert "0.75 [" in md and "(3/4)" in md
    assert "DID NOT MOVE" in md and "t1: primary cause" in md and "| corpus | pinned / problems / warnings | 9 / 0 / 1 |" in md
    assert md.count(NOT_RUN_LIVE) == 7
    quick = render_markdown(_report(with_ood=False))
    assert "| ood | out-of-distribution split | quick | - |" in quick
    no_ai = {k: v for k, v in _report(False).items() if k != "agents"}
    assert "AI evaluation" not in render_markdown(no_ai)


def test_history_row_and_append_history_replace_the_same_version(tmp_path):
    from ceap.evaluation_report import HISTORY_FIELDS, append_history, history_row

    row = history_row(_report(False), "0.9.0", "2026-11-12")
    assert row["research_verdict_accuracy"] == "n/a" and row["cost_usd_estimate_per_investigation"] == "0.0012"
    path = tmp_path / "h.csv"
    append_history(history_row(_report(False), "0.8.0", "d"), path)
    append_history(row, path)
    append_history({**row, "scenarios": "99"}, path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert [r["version"] for r in rows] == ["0.8.0", "0.9.0"] and rows[1]["scenarios"] == "99"
    assert list(rows[0]) == list(HISTORY_FIELDS)


async def test_fault_doubles_delegate_non_planning_and_spoil_planning():
    from ceap.evaluation_report import FAULTS, _Fault

    hostile = await _Fault(FAULTS["hostile_plan"]).complete(
        LLMRequest("s", [{"role": "user", "content": "x"}], purpose="planning",
                   metadata={"parameters": {"symbol": "AAPL", "scenario": "T01"}})
    )
    steps = json.loads(hostile.content)["steps"]
    assert steps[0]["tool_id"] == "shell.exec"
    assert all(s["arguments"].get("symbol", "ZZZZ") == "ZZZZ" for s in steps if s["type"] == "TOOL_CALL")
    assert all(s["type"] not in ("VALIDATION", "FINALISE") for s in steps)
    with pytest.raises(ConnectionError):
        await _Fault(FAULTS["exception"]).complete(_req("planning"))
    assert (await _Fault(FAULTS["truncation"]).complete(_req("planning"))).stop_reason == "max_tokens"
    assert (await _Fault(FAULTS["refusal"]).complete(_req("planning"))).stop_reason == "refusal"
    other = await _Fault(FAULTS["exception"]).complete(_req("narrative"))
    assert other.model  # answered by the mock, not the fault


def test_hybrid_eval_main_writes_both_sections(monkeypatch, tmp_path):
    import sys

    from ceap.rag import hybrid_eval

    monkeypatch.setattr(hybrid_eval, "compare", lambda: {"c": 1})
    monkeypatch.setattr(hybrid_eval, "abstain", lambda: {"a": 2})
    out = tmp_path / "r.json"
    monkeypatch.setattr(sys, "argv", ["hybrid_eval", "--out", str(out)])
    hybrid_eval.main()
    assert json.loads(out.read_text(encoding="utf-8")) == {"compare": {"c": 1}, "abstain": {"a": 2}}


# ------------------------------------------------------------------ platform-backed (slow)


def _factory(llm):
    from ceap.harness.memory import CaseMemory
    from ceap.platform import Platform

    return Platform(llm=llm, case_memory=CaseMemory(enabled=False))


@pytest.mark.slow  # four faulted investigations: ~20 s
async def test_safety_and_robustness_holds_every_invariant():
    from ceap.evaluation_report import FAULTS, safety_and_robustness

    r = await safety_and_robustness(_factory)
    assert [row["fault"] for row in r["rows"]] == list(FAULTS)
    assert r["invariants_rate"] == 1.0


@pytest.mark.slow  # one mock investigation plus a golden item: ~10 s
async def test_cost_estimate_and_golden_scores_on_one_item():
    from ceap.evaluation_report import cost_estimate, golden_scores

    c = await cost_estimate(_factory, n=1)
    assert c["investigations"] == 1 and c["calls_per_investigation"] > 0 and "not a bill" in c["label"]
    g = await golden_scores(_factory(MockLLMClient()), limit=1)
    assert g["investigation_items"] == 1 and len(g["rows"]) >= 1


@pytest.mark.slow  # a full seed with the mock (agent evals plus one golden item)
async def test_run_seed_with_the_mock_reports_every_section():
    r = await lm.run_seed(MockLLMClient(), golden_limit=1)
    assert {"agents", "untraceable_number", "golden", "golden_rows", "slo", "slo_alerts"} <= set(r)
    assert r["untraceable_number"]["k"] == 0


@pytest.mark.slow  # one OOD investigation
async def test_ood_scores_one_item_resolves_the_held_out_id():
    from ceap.golden import load_ood_set

    doc = await ood.ood_scores(load_ood_set(), limit=1)
    assert len(doc["rows"]) == 1 and doc["evidence_class"] == "mock" and doc["cases_source"] == "hand_built"
