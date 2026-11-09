"""0.9.0 (L3): deterministic statement checks in the critic, measured on two planted sets.

Bars: the 0.8.0 critic caught 6/15 on the existing set and 6/15 on the held-out set (measured
before the change, commit d04f19d). The bar is that every planted fault of the three new kinds is
caught on both sets and no control is flagged; the held-out set was written by the same author, so
this is a regression bar, not a generalisation claim (see docs/ai-agents/rag-hybrid-0.9.0.md).
"""

from __future__ import annotations

import pytest

from ceap.agents.critic_checks import check_statement, direction
from ceap.agents.critic_planted import existing_cases, heldout_cases, score


@pytest.mark.parametrize("cases", [existing_cases, heldout_cases], ids=["existing", "heldout_v1"])
def test_planted_detection(cases):
    r = score(cases())
    assert r["detection"]["k"] == r["detection"]["n"] == 15, r["missed"]
    assert r["false_alarm"]["k"] == 0, r["false_alarms"]


def test_heldout_set_shape():
    kinds = [f["planted"] for b in heldout_cases() for f in b["findings"]]
    assert len(kinds) == 25 and kinds.count(None) == 10
    assert {k for k in kinds if k} == {"uncited", "unresolved", "contradicted", "numeric_mismatch", "direction_mismatch"}


def test_direction_polarity():
    assert direction("Implementation shortfall improved by 6.4 bps", higher_is_better=False) == -1
    assert direction("Fill rate improved by 3 points", higher_is_better=True) == 1
    assert direction("Spread widened then narrowed", higher_is_better=False) == 0


def test_checks_examples():
    assert check_statement("Implementation shortfall improved by 6.4 bps", [{"is_change_bps": 6.4}])[0][0] == (
        "direction_mismatch"
    )
    assert check_statement("Implementation shortfall rose by 6.4 bps", [{"is_change_bps": 6.4}]) == []
    assert check_statement("Fill rate was 62% in the window", [{"fill_rate": 0.62}]) == []
    assert check_statement("depth_ratio was 0.85", [{"depth_ratio": 0.45}])[0][0] == "numeric_mismatch"
    flags = [f for f, _ in check_statement("spread widened", [{"spread_change_bps": 1.9}, {"spread_change_bps": 0.1}])]
    assert flags == ["contradicted"]
    assert check_statement("no metric here at all", [{"x": 1.0}]) == []
