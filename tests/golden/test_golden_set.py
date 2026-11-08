"""3E.1 golden set governance: frozen, versioned, signed off through a golden-change PR."""

from __future__ import annotations

import copy

from ceap.golden import golden_set_problems, load_golden_set, score_investigation, score_research
from ceap.platform import default_request, default_research_request


def test_3e1_golden_set_is_frozen_and_versioned():
    doc = load_golden_set()
    assert golden_set_problems(doc) == []
    assert doc["version"] and doc["change_policy"].startswith("edits only in a PR labelled golden-change")
    edited = copy.deepcopy(doc)
    edited["items"][3]["must_not_claim"] = []
    assert "without a version bump" in " ".join(golden_set_problems(edited))


async def test_3e1_golden_scoring_end_to_end(platform):
    doc = load_golden_set()
    inv = next(i for i in doc["items"] if i["kind"] == "investigation" and i["template"] == "high_volatility")
    res = next(i for i in doc["items"] if i["kind"] == "research")
    s_inv = score_investigation(
        inv, await platform.investigate(default_request(inv["symbol"], inv["scenario"]))
    )
    s_res = score_research(res, await platform.research(default_research_request(res["scenario"])))
    assert set(s_inv) >= {"primary_ok", "mentions_ok", "no_forbidden_claim"}
    assert s_inv["mentions_ok"], s_inv["missing_mentions"]
    assert s_res["verdict_ok"] and s_res["flags_ok"]
