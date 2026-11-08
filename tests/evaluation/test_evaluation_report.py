"""3E.2 evaluation taxonomy and report; 3E.3 history rows."""

from __future__ import annotations

import csv

from ceap.evaluation_report import (
    HISTORY_FIELDS,
    HISTORY_PATH,
    LIVE_ONLY,
    NOT_RUN_LIVE,
    append_history,
    build_report,
    history_row,
    render_markdown,
)


async def test_3e2_one_command_produces_the_table_and_no_cell_claims_a_live_number(tmp_path):
    report = await build_report(limit=1, research_limit=1)
    assert set(report) >= {"capability", "safety", "robustness", "cost", "live_only"}
    assert report["live_only"] == {k: NOT_RUN_LIVE for k, _ in LIVE_ONLY}
    assert report["complete"] is False and "mock" in report["llm"]
    assert report["safety"]["invariants_rate"] == 1.0  # every fault keeps the governance invariants
    assert all(r["degraded"] for r in report["robustness"].values())
    assert "estimate" in report["cost"]["label"]
    table = render_markdown(report)
    for _, label in LIVE_ONLY:
        assert f"| live only | {label} | {NOT_RUN_LIVE} |" in table
    # 3E.3: a history row round-trips through the CSV
    path = tmp_path / "history.csv"
    append_history(history_row(report, "9.9.9", "2026-11-08"), path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert rows[-1]["version"] == "9.9.9" and set(rows[-1]) == set(HISTORY_FIELDS)


def test_3e3_history_has_one_row_per_release_from_0_4_0():
    versions = [r["version"] for r in csv.DictReader(HISTORY_PATH.open(encoding="utf-8"))]
    assert versions[:2] == ["0.4.0", "0.5.0"] and len(versions) == len(set(versions))
