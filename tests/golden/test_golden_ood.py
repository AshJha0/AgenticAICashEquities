"""B6: the out-of-distribution golden split is frozen, separate from the signed-off set, and scorable.

The 45 signed-off items in ``golden_set.json`` are untouched (their hash is checked by
``test_golden_set.py``); the OOD items live in ``golden_ood_v1.json`` with their own hash.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from ceap.data.scenarios import SCENARIO_TEMPLATES
from ceap.eval.ood import OODDatasetStore, ood_scores, spec_from_item
from ceap.golden import (
    OOD_TEMPLATES,
    golden_set_problems,
    load_golden_set,
    load_ood_set,
    ood_set_problems,
    score_ood,
    summarise_ood,
)


def test_signed_off_golden_set_is_unchanged():
    doc = load_golden_set()
    assert golden_set_problems(doc) == []
    assert doc["items_sha256"] == "c9a28cc878191a85504562fe1d51ae6bc94f19c8b9e91f106c4d018efa3f6d09"
    assert doc["signoff"]["status"] == "SIGNED_OFF"


def test_ood_split_is_frozen_and_held_out():
    doc = load_ood_set()
    assert ood_set_problems(doc) == []
    assert doc["change_policy"].startswith("edits only in a PR labelled golden-change")
    assert len(doc["items"]) == 15
    catalogue = {t.template for t in SCENARIO_TEMPLATES}
    assert not catalogue & set(OOD_TEMPLATES)
    edited = copy.deepcopy(doc)
    edited["items"][0]["must_not_claim"] = []
    assert "without a version bump" in " ".join(ood_set_problems(edited))


def test_ood_specs_are_new_combinations_on_new_seeds():
    doc = load_ood_set()
    specs = [spec_from_item(i) for i in doc["items"]]
    catalogue_truths = {frozenset(c.value for c in t.ground_truth) for t in SCENARIO_TEMPLATES}
    for s in specs:
        assert frozenset(c.value for c in s.ground_truth) not in catalogue_truths, s.id
        assert s.seed >= 7000 and "ood" in s.tags
    store = OODDatasetStore(specs[:1])
    assert store._extra[specs[0].id] is specs[0]
    with pytest.raises(ValueError):
        spec_from_item({**doc["items"][0], "spec": {**doc["items"][0]["spec"], "colour": "red"}})


def _result(primary: str, material: dict[str, float], tools: list[str]) -> SimpleNamespace:
    ranked = [{"cause": c, "score": s} for c, s in material.items()]
    evidence = [SimpleNamespace(attributes={"tool_id": t}) for t in tools]
    return SimpleNamespace(
        report=SimpleNamespace(attribution={"primary": primary, "ranked": ranked}, evidence=evidence)
    )


def test_ood_scoring_and_per_template_summary():
    item = load_ood_set()["items"][0]  # venue_and_spreads
    hit = score_ood(
        item,
        _result(
            "VENUE_DEGRADATION", {"VENUE_DEGRADATION": 0.8, "WIDE_SPREADS": 0.5}, item["must_mention_tools"]
        ),
    )
    assert hit["primary_ok"] and hit["coverage_ok"] and hit["mentions_ok"] and hit["no_forbidden_claim"]
    miss = score_ood(item, _result("TECHNOLOGY_LATENCY", {"TECHNOLOGY_LATENCY": 0.9}, []))
    assert not (miss["primary_ok"] or miss["coverage_ok"] or miss["no_forbidden_claim"])
    summary = summarise_ood([{**hit, "template": "a"}, {**miss, "template": "a"}, {**hit, "template": "b"}])
    assert summary["per_template"]["a"]["primary_ok"]["k"] == 1
    assert summary["overall"]["primary_ok"]["n"] == 3


@pytest.mark.slow
async def test_ood_end_to_end_one_item():
    r = await ood_scores(load_ood_set(), limit=1)
    assert r["evidence_class"] == "mock" and r["overall"]["items"] == 1
    assert set(r["rows"][0]) >= {"primary_ok", "coverage_ok", "mentions_ok", "no_forbidden_claim"}
