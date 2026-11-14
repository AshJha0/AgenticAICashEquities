"""0.10.0 item Q6 (finding R-30): research evaluation on the 500-name universe, published beside the 30-name one."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_both_universe_results_are_in_latest_json_and_the_readme_matches():
    cap = json.loads((ROOT / "docs" / "evaluation" / "latest.json").read_text(encoding="utf-8"))["capability"]
    assert cap["research_scenarios"] == 30 and cap["research_verdict_accuracy"] is not None
    big = cap["research_500"]
    assert big["universe"] == "synthetic500" and big["names"] == 500 and big["scenarios"] == 30
    assert big["completed"] == 1.0 and sum(big["verdicts"].values()) == 30
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "RS01-RS30, 500-name universe" not in readme  # the 0.9 claim that the scenarios ran on 500 names
    assert "re-run on the 500-name universe" in readme


def test_evaluation_platform_builds_the_requested_universe():
    from ceap.evaluation import evaluation_platform

    assert evaluation_platform("synthetic500").history.get("RS01").n_symbols == 500
    assert evaluation_platform().history.get("RS01").n_symbols == 30
    with pytest.raises(ValueError):
        evaluation_platform("nope")
