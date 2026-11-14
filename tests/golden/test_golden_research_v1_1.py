"""0.10 research golden items (D1, D4): a separate file, signed by the owner 2026-11-09 (option A); the signed set is untouched."""

from __future__ import annotations

import json

from ceap.data.research_scenarios import get_research_scenario, golden_scenario_id
from ceap.golden import GOLDEN_RESEARCH_V11_PATH, GOLDEN_SET_PATH, items_sha256, load_golden_set


# 0.10.0 Q2 (R-16): with the deflated-Sharpe bar at 0.95 the alpha chain stops these two scenarios at
# NO_ALPHA before the cost check runs, so the COST_DRAG the signed v1.1 file asks for is no longer the
# expected flag. The verdict (REJECT) is unchanged. The signed file stays frozen; the divergence is
# listed exactly here and any other disagreement still fails.
Q2_FLAG_DIVERGENCE = {"G-RES11-13": ("COST_DRAG", "NO_ALPHA"), "G-RES11-14": ("COST_DRAG", "NO_ALPHA")}


def test_research_v1_1_file_is_signed_and_frozen():
    doc = json.loads(GOLDEN_RESEARCH_V11_PATH.read_text(encoding="utf-8"))
    assert doc["version"] == "1.1.0" and doc["signoff"]["status"] == "SIGNED"
    assert doc["items_sha256"] == items_sha256(doc["items"])
    for item in doc["items"]:
        spec = get_research_scenario(golden_scenario_id(item))
        assert spec.template == item["template"] and spec.expected_verdict.value == item["verdict"]
        flags = {f.value for f in spec.expected_flags}
        if item["id"] in Q2_FLAG_DIVERGENCE:
            signed, now = Q2_FLAG_DIVERGENCE[item["id"]]
            assert item["must_flag"] == [signed] and now in flags and signed not in flags, item["id"]
            continue
        assert set(item["must_flag"]) <= flags, item["id"]


def test_signed_set_items_still_resolve_to_their_authored_truth():
    doc = load_golden_set(GOLDEN_SET_PATH)
    assert doc["version"] == "1.0.0" and doc["signoff"]["status"] == "SIGNED_OFF"
    for item in (i for i in doc["items"] if i["kind"] == "research"):
        spec = get_research_scenario(golden_scenario_id(item))
        assert spec.template == item["template"], item["id"]
        assert spec.expected_verdict.value == item["verdict"], item["id"]
        assert set(item["must_flag"]) <= {f.value for f in spec.expected_flags}, item["id"]
