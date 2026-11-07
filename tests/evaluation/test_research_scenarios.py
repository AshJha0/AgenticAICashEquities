"""Research evaluation against parameter-derived ground truth (30 scenarios through the full pipeline, 2.11).

Ground truth comes from generator parameters (``ceap.data.ground_truth``), not from a run. Scenarios whose
expected gate margins are within one standard error are *near-threshold*: their realised verdict is partly
luck, so they are reported separately and held to a lower bar.
"""

from __future__ import annotations

import pytest

from ceap.evaluation import run_research_evaluation

pytestmark = pytest.mark.evaluation


async def test_thirty_research_scenarios_against_parameter_derived_truth(platform):
    summary = await run_research_evaluation(platform=platform)
    rows = summary["rows"]
    clear = [r for r in rows if not r["near_threshold"]]
    misses = [r for r in clear if not (r["verdict_hit"] and r["flags_coverage"])]
    assert summary["scenarios"] == 30 and summary["clear_scenarios"] == len(clear) >= 15
    assert summary["completed"] == 1.0
    assert summary["approved"] == 1.0
    assert summary["unresolved_findings"] == 0
    assert summary["number_warnings"] == 0
    assert summary["clear_verdict_accuracy"] >= 0.85, misses
    assert sum(r["flags_coverage"] for r in clear) / len(clear) >= 0.85, misses
    assert summary["near_threshold_accuracy"] >= 0.5
    assert not any(r["staged"] for r in rows)
    # every template is recognised at least once
    by_template: dict[str, list[bool]] = {}
    for r in summary["rows"]:
        by_template.setdefault(r["template"], []).append(r["verdict_hit"])
    assert len(by_template) == 10
    assert all(any(v) for v in by_template.values()), by_template
