"""Per-package branch coverage from a ``coverage json`` report (0.10.0 item E1, finding C-P2-1).

    pytest --cov=ceap --cov-branch --cov-report=json:coverage.json ...
    python scripts/coverage_by_package.py coverage.json [--markdown docs/testing/coverage-by-package.md]

A package is the first component under ``ceap`` (``ceap.analytics``, ``ceap.api``, ...); modules
directly under ``ceap/`` are grouped as ``ceap (top-level)``. Coverage is the coverage.py
"branch" figure: (covered lines + covered branches) / (statements + branches).

The floor is 90 % per package and 95 % for ``analytics`` and ``domain``. Packages measured under
their floor when E1 landed are listed in ``BASELINE`` with the figure measured then: they may not
drop below it (a ratchet), and the table marks them ``under floor``. Raising one means writing
behaviour tests and then raising or deleting its baseline entry; nothing here hides a gap.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_FLOOR = 90.0
FLOORS: dict[str, float] = {"analytics": 95.0, "domain": 95.0}
#: unit tier, ratchet only. First measured 2026-11-14 (E1); raised the same day by lane W2's behaviour
#: tests (docs/testing/coverage-by-package.md). api, studies and observability now meet the floor.
BASELINE: dict[str, float] = {
    "agents": 81.0,
    "analytics": 89.1,
    "ceap (top-level)": 79.2,
    "eval": 73.0,
    "harness": 78.1,
    "llm": 74.7,
    "mcp": 84.3,
    "paper": 84.0,
}
TOLERANCE = 0.1  # percentage points, for run-to-run noise in branch counting


def package_of(path: str) -> str:
    parts = Path(path).as_posix().split("/")
    if "ceap" not in parts:
        return "(outside ceap)"
    rest = parts[len(parts) - 1 - parts[::-1].index("ceap") + 1 :]
    return rest[0] if len(rest) > 1 else "ceap (top-level)"


def per_package(report: dict) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for path, data in report["files"].items():
        s = data["summary"]
        t = totals[package_of(path)]
        t["statements"] += s["num_statements"]
        t["covered_lines"] += s["covered_lines"]
        t["branches"] += s.get("num_branches", 0)
        t["covered_branches"] += s.get("covered_branches", 0)
    return {k: dict(v) for k, v in totals.items()}


def percent(t: dict[str, int]) -> float:
    denom = t["statements"] + t["branches"]
    return 100.0 * (t["covered_lines"] + t["covered_branches"]) / denom if denom else 100.0


def evaluate(totals: dict[str, dict[str, int]], baseline: dict[str, float] | None = None) -> list[dict]:
    baseline = BASELINE if baseline is None else baseline
    rows = []
    for pkg in sorted(totals):
        pct = percent(totals[pkg])
        floor = FLOORS.get(pkg, DEFAULT_FLOOR)
        base = baseline.get(pkg)
        if pct + 1e-9 >= floor:
            status = "ok"
        elif base is not None and pct + TOLERANCE >= base:
            status = "under floor (baseline)"
        else:
            status = "FAIL"
        rows.append({"package": pkg, "percent": pct, "floor": floor, "baseline": base, "status": status, **totals[pkg]})
    return rows


def to_markdown(rows: list[dict]) -> str:
    out = [
        "| Package | Statements | Branches | Branch coverage | Floor | Status |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        out.append(
            f"| `{r['package']}` | {r['statements']} | {r['branches']} | {r['percent']:.1f} % | "
            f"{r['floor']:.0f} % | {r['status']} |"
        )
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="per-package branch coverage gate")
    ap.add_argument("report", type=Path)
    ap.add_argument("--markdown", type=Path, help="also write the table to this file")
    args = ap.parse_args(argv)
    rows = evaluate(per_package(json.loads(args.report.read_text(encoding="utf-8"))))
    table = to_markdown(rows)
    print(table)
    if args.markdown:
        args.markdown.write_text(table, encoding="utf-8")
    failed = [r["package"] for r in rows if r["status"] == "FAIL"]
    if failed:
        print(f"coverage below floor (and below baseline): {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
