"""Scenario evaluation against structured ground truth (50 scenarios)."""

from __future__ import annotations

import pytest

from ceap.evaluation import run_evaluation

pytestmark = pytest.mark.evaluation


async def test_fifty_scenarios_against_ground_truth(platform):
    summary = await run_evaluation(platform=platform)
    misses = [r for r in summary["rows"] if not (r["primary_hit"] and r["coverage"])]
    assert summary["scenarios"] == 50
    assert summary["completed"] == 1.0
    assert summary["unresolved_findings"] == 0
    assert summary["number_warnings"] == 0
    assert summary["primary_accuracy"] >= 0.9, misses
    assert summary["coverage"] >= 0.9, misses
    assert summary["false_positive_rate"] <= 0.1, [r for r in summary["rows"] if r["false_positives"]]


async def test_each_template_is_recognised_at_least_once(platform):
    summary = await run_evaluation(limit=50, platform=platform)
    by_template: dict[str, list[bool]] = {}
    for r in summary["rows"]:
        by_template.setdefault(r["template"], []).append(r["primary_hit"])
    assert len(by_template) == 10
    assert all(any(v) for v in by_template.values())
