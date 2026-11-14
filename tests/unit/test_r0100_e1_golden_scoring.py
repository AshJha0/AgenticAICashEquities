"""Golden-set scoring and governance checks on hand-built results (0.10.0 E1 coverage for the
top-level ``ceap.golden``). The golden tier runs real investigations; these pin the scoring rules
themselves: the 0.35 materiality threshold, a missing report, rates and the frozen-set checks."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from ceap import golden


def _result(primary=None, ranked=(), tools=(), proposal=None, report=True):
    if not report:
        return SimpleNamespace(report=None)
    evidence = [SimpleNamespace(attributes={"tool_id": t} if t else {}) for t in tools]
    att = {"primary": primary, "ranked": list(ranked)}
    return SimpleNamespace(
        report=SimpleNamespace(attribution=att, evidence=evidence, proposal=proposal or {})
    )


INV = {
    "id": "G01",
    "primary": ["SPREAD_WIDENING"],
    "must_mention_tools": ["tca.slippage", "market.spread"],
    "must_not_claim": ["VENUE_OUTAGE"],
}


def test_investigation_score_uses_the_materiality_threshold():
    res = _result(
        "SPREAD_WIDENING",
        ranked=[{"cause": "SPREAD_WIDENING", "score": 0.9}, {"cause": "VENUE_OUTAGE", "score": 0.3499}],
        tools=["tca.slippage", "market.spread", None],
    )
    row = golden.score_investigation(INV, res)
    assert row == {
        "id": "G01",
        "primary_ok": True,
        "mentions_ok": True,
        "missing_mentions": [],
        "no_forbidden_claim": True,  # 0.3499 is below 0.35: not a material claim
        "forbidden_claims": [],
    }
    res.report.attribution["ranked"][1]["score"] = 0.35
    row = golden.score_investigation(INV, res)
    assert row["no_forbidden_claim"] is False and row["forbidden_claims"] == ["VENUE_OUTAGE"]


def test_investigation_without_report_fails_every_check_but_forbidden():
    row = golden.score_investigation(INV, _result(report=False))
    assert row["primary_ok"] is False and row["mentions_ok"] is False
    assert row["missing_mentions"] == ["market.spread", "tca.slippage"]
    assert row["no_forbidden_claim"] is True  # nothing was claimed


def test_research_score_and_missing_report():
    item = {"id": "R01", "verdict": "REJECT", "must_flag": ["OVERFIT"], "must_not_flag": ["LOOKAHEAD"]}
    good = golden.score_research(item, _result(proposal={"verdict": "REJECT", "flags": ["OVERFIT", "COSTS"]}))
    assert good == {"id": "R01", "verdict_ok": True, "flags_ok": True, "no_forbidden_flag": True}
    bad = golden.score_research(item, _result(proposal={"verdict": "ADOPT", "flags": ["LOOKAHEAD"]}))
    assert bad == {"id": "R01", "verdict_ok": False, "flags_ok": False, "no_forbidden_flag": False}
    none = golden.score_research(item, _result(report=False))
    assert none["verdict_ok"] is False and none["no_forbidden_flag"] is True


def test_summarise_rates_and_empty_kinds():
    rows = [
        {"primary_ok": True, "mentions_ok": False, "no_forbidden_claim": True},
        {"primary_ok": False, "mentions_ok": False, "no_forbidden_claim": True},
        {"primary_ok": True, "mentions_ok": True, "no_forbidden_claim": True},
    ]
    s = golden.summarise(rows)
    assert s["investigation_items"] == 3 and s["research_items"] == 0
    assert s["primary_cause_accuracy"] == pytest.approx(0.6667) and s["must_mention_rate"] == pytest.approx(
        0.3333
    )
    assert s["verdict_accuracy"] is None and s["must_flag_rate"] is None


def test_committed_golden_set_is_clean_and_each_edit_is_reported():
    doc = golden.load_golden_set()
    assert golden.golden_set_problems(doc) == []
    edited = copy.deepcopy(doc)
    edited["items"].append(copy.deepcopy(edited["items"][0]))  # duplicate id, wrong count, new hash
    problems = golden.golden_set_problems(edited)
    assert any("without a version bump" in p for p in problems)
    assert any("expected 30 investigation and 15 research items, found 31/15" in p for p in problems)
    assert "duplicate golden item ids" in problems
    edited = copy.deepcopy(doc)
    edited["signoff_sample"] = edited["signoff_sample"][:9]
    assert golden.golden_set_problems(edited) == ["signoff_sample must name 10 items of the set"]


def test_items_hash_is_order_of_keys_independent():
    assert golden.items_sha256([{"a": 1, "b": 2}]) == golden.items_sha256([{"b": 2, "a": 1}])
    assert golden.items_sha256([{"a": 1}]) != golden.items_sha256([{"a": 2}])


def test_committed_ood_set_is_clean_and_overlap_and_bad_truth_are_reported():
    doc = golden.load_ood_set()
    assert golden.ood_set_problems(doc) == []
    tpl = doc["items"][0]["template"]
    main = {"items": [{"template": tpl}]}
    assert any("also appear in the main golden set" in p for p in golden.ood_set_problems(doc, main))
    edited = copy.deepcopy(doc)
    first = edited["items"][0]
    first["must_not_claim"] = list(first["spec"]["ground_truth"])[:1]
    edited["items_sha256"] = golden.items_sha256(edited["items"])
    assert golden.ood_set_problems(edited) == [
        f"{first['id']}: primary must be a true cause and must_not_claim must exclude them"
    ]
    edited = copy.deepcopy(doc)
    edited["items"] = [i for i in edited["items"] if i["template"] != tpl]
    edited["items_sha256"] = golden.items_sha256(edited["items"])
    problems = golden.ood_set_problems(edited)
    assert any("expected the templates" in p for p in problems)
    assert "every OOD template needs at least 5 items" in problems


def test_ood_score_adds_coverage_and_summary_has_wilson_intervals():
    item = {**INV, "spec": {"ground_truth": ["SPREAD_WIDENING", "LARGE_ORDER"]}}
    res = _result(
        "SPREAD_WIDENING",
        ranked=[{"cause": "SPREAD_WIDENING", "score": 0.8}, {"cause": "LARGE_ORDER", "score": 0.2}],
        tools=["tca.slippage", "market.spread"],
    )
    row = golden.score_ood(item, res)
    assert row["primary"] == "SPREAD_WIDENING" and row["material"] == ["SPREAD_WIDENING"]
    assert row["coverage_ok"] is False  # LARGE_ORDER is true but not material
    rows = [{**row, "template": "a"}, {**row, "coverage_ok": True, "template": "b"}]
    s = golden.summarise_ood(rows)
    assert s["overall"]["items"] == 2 and set(s["per_template"]) == {"a", "b"}
    assert s["per_template"]["a"]["items"] == 1
    assert s["overall"]["coverage_ok"] != s["per_template"]["a"]["coverage_ok"]
