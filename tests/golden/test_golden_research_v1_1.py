"""0.10 research golden items (D1, D4): a separate file, signed by the owner 2026-11-09 (option A); the signed set is untouched."""

from __future__ import annotations

import json

from ceap.data.research_scenarios import get_research_scenario, golden_scenario_id
from ceap.golden import GOLDEN_RESEARCH_V11_PATH, GOLDEN_SET_PATH, items_sha256, load_golden_set


def test_research_v1_1_file_is_signed_and_frozen():
    doc = json.loads(GOLDEN_RESEARCH_V11_PATH.read_text(encoding="utf-8"))
    assert doc["version"] == "1.1.0" and doc["signoff"]["status"] == "SIGNED"
    assert doc["items_sha256"] == items_sha256(doc["items"])
    for item in doc["items"]:
        spec = get_research_scenario(golden_scenario_id(item))
        assert spec.template == item["template"] and spec.expected_verdict.value == item["verdict"]
        assert set(item["must_flag"]) <= {f.value for f in spec.expected_flags}


def test_signed_set_items_still_resolve_to_their_authored_truth():
    doc = load_golden_set(GOLDEN_SET_PATH)
    assert doc["version"] == "1.0.0" and doc["signoff"]["status"] == "SIGNED_OFF"
    for item in (i for i in doc["items"] if i["kind"] == "research"):
        spec = get_research_scenario(golden_scenario_id(item))
        assert spec.template == item["template"], item["id"]
        assert spec.expected_verdict.value == item["verdict"], item["id"]
        assert set(item["must_flag"]) <= {f.value for f in spec.expected_flags}, item["id"]
