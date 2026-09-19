"""Scenario evaluation: run the full platform over the 50-scenario catalogue.

Each scenario's structured ground truth is compared with the platform's
output at three levels:

1. *primary accuracy* - the report's primary cause is one of the true causes;
2. *coverage*         - every true cause appears among the material causes;
3. *precision*        - material causes that are not in the ground truth
                        (false positives) are counted and reported.

The suite also checks governance invariants on every run: the harness
completed, every finding cites resolvable evidence, and the narrative
number audit produced no warnings.
"""

from __future__ import annotations

import time
from typing import Any

from ceap.data.scenarios import ScenarioSpec, all_scenarios
from ceap.platform import Platform, default_request


async def evaluate_scenario(platform: Platform, spec: ScenarioSpec) -> dict[str, Any]:
    started = time.perf_counter()
    result = await platform.investigate(default_request(spec.symbol, spec.id))
    truth = {c.value for c in spec.ground_truth}
    att = result.report.attribution if result.report else {}
    material = {r["cause"] for r in att.get("ranked", []) if r["score"] >= 0.35}
    primary = att.get("primary")
    evidence_ids = {e.id for e in result.evidence}
    unresolved = [f.id for f in result.findings if any(e not in evidence_ids for e in f.supporting_evidence)]
    return {
        "scenario": spec.id,
        "template": spec.template,
        "symbol": spec.symbol,
        "truth": sorted(truth),
        "primary": primary,
        "material": sorted(material),
        "primary_hit": primary in truth,
        "coverage": (truth <= material) or (truth == {"NORMAL"} and not material),
        "false_positives": sorted(material - truth),
        "completed": result.success,
        "unresolved_findings": unresolved,
        "number_warnings": len(
            (result.report.critique.get("narrative_number_warnings") if result.report else []) or []
        ),
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 1),
    }


async def run_evaluation(
    limit: int | None = None, verbose: bool = False, platform: Platform | None = None
) -> dict[str, Any]:
    platform = platform or Platform()
    specs = all_scenarios()[:limit] if limit else all_scenarios()
    rows = []
    for spec in specs:
        row = await evaluate_scenario(platform, spec)
        rows.append(row)
        if verbose:
            flag = "OK " if row["primary_hit"] and row["coverage"] else "MISS"
            print(
                f"{flag} {row['scenario']} {row['symbol']:5s} {row['template']:24s} primary={str(row['primary'] or '-'):20s} fp={row['false_positives']} {row['duration_ms']:.0f}ms"
            )
    n = len(rows)
    return {
        "scenarios": n,
        "primary_accuracy": sum(r["primary_hit"] for r in rows) / n if n else 0.0,
        "coverage": sum(r["coverage"] for r in rows) / n if n else 0.0,
        "false_positive_rate": sum(bool(r["false_positives"]) for r in rows) / n if n else 0.0,
        "completed": sum(r["completed"] for r in rows) / n if n else 0.0,
        "unresolved_findings": sum(len(r["unresolved_findings"]) for r in rows),
        "number_warnings": sum(r["number_warnings"] for r in rows),
        "mean_duration_ms": sum(r["duration_ms"] for r in rows) / n if n else 0.0,
        "rows": rows,
    }
