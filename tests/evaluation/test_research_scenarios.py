"""Research evaluation against structured ground truth (21 scenarios through the full pipeline)."""

from __future__ import annotations

import pytest

from ceap.evaluation import run_research_evaluation

pytestmark = pytest.mark.evaluation


async def test_twenty_one_research_scenarios_against_ground_truth(platform):
    summary = await run_research_evaluation(platform=platform)
    misses = [r for r in summary["rows"] if not (r["verdict_hit"] and r["flags_coverage"])]
    assert summary["scenarios"] == 21
    assert summary["completed"] == 1.0
    assert summary["approved"] == 1.0
    assert summary["unresolved_findings"] == 0
    assert summary["number_warnings"] == 0
    assert summary["verdict_accuracy"] >= 0.9, misses
    assert summary["flags_coverage"] >= 0.9, misses
    assert not any(r["staged"] for r in summary["rows"])


async def test_each_research_template_is_recognised_at_least_once(platform):
    summary = await run_research_evaluation(platform=platform)
    by_template: dict[str, list[bool]] = {}
    for r in summary["rows"]:
        by_template.setdefault(r["template"], []).append(r["verdict_hit"] and r["flags_coverage"])
    assert len(by_template) == 7
    assert all(any(v) for v in by_template.values())
