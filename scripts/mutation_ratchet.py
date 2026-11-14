"""Surviving-mutant ratchet for the nightly mutmut job (H3, 0.9.0).

Plain idea: mutmut changes one operator at a time in ``ceap.analytics`` and ``ceap.domain`` and
reruns the tests; a mutant the tests do not notice "survives". The number of survivors may go down
but never up. ``tests/mutation/baseline.json`` holds the current ceiling.

Usage (after ``mutmut run``):
    python scripts/mutation_ratchet.py            # compare with the baseline; exit 1 if worse
    python scripts/mutation_ratchet.py --update   # write the current count as the new baseline

A null baseline (``"survived": null``) is tolerated only until ``null_allowed_until`` in the
baseline file; after that date a null fails the job (R-25, 0.10.0 E2), so "nightly mutation testing
with a ratchet" cannot stay an unmeasured claim. A run in which mutmut produced no results at all
(it crashed before testing a mutant) also fails, instead of reading as "0 survivors".
mutmut does not run natively on Windows; measure on Linux (the nightly job or WSL).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

BASELINE = Path(__file__).resolve().parents[1] / "tests" / "mutation" / "baseline.json"


def results() -> dict[str, str]:
    """``{mutant: status}`` for every mutant mutmut recorded (``mutmut results --all true``)."""
    out = subprocess.run(["mutmut", "results", "--all", "true"], capture_output=True, text=True, check=True).stdout
    rows = (line.strip().rsplit(":", 1) for line in out.splitlines() if ":" in line)
    return {name.strip(): status.strip() for name, status in rows if name.strip()}


def survivors(rows: dict[str, str]) -> list[str]:
    return sorted(name for name, status in rows.items() if status == "survived")


def null_expired(base: dict[str, object], today: date) -> bool:
    """True once a null baseline is past its ``null_allowed_until`` date (a missing date counts as past)."""
    until = base.get("null_allowed_until")
    return not isinstance(until, str) or today > date.fromisoformat(until)


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    ap = argparse.ArgumentParser(description="surviving-mutant ratchet")
    ap.add_argument("--update", action="store_true")
    args = ap.parse_args(argv)
    rows = results()
    if not rows:
        print("::error::mutmut recorded no mutants: the run failed before testing any (see the step above)")
        return 1
    names = survivors(rows)
    print(f"mutants: {len(rows)}; surviving: {len(names)}")
    for n in names:
        print(f"  {n}")
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    if args.update:
        base["survived"] = len(names)
        BASELINE.write_text(json.dumps(base, indent=2) + "\n", encoding="utf-8")
        print(f"baseline set to {len(names)}")
        return 0
    ceiling = base.get("survived")
    if ceiling is None:
        if null_expired(base, today or date.today()):
            print(
                f"::error::no mutation baseline committed and null_allowed_until "
                f"({base.get('null_allowed_until')}) has passed; commit this count with --update"
            )
            return 1
        print("no baseline committed yet: reporting only (commit this count to tests/mutation/baseline.json)")
        return 0
    if len(names) > ceiling:
        print(f"::error::surviving mutants rose from {ceiling} to {len(names)}; add tests that kill them")
        return 1
    if len(names) < ceiling:
        print(f"improved: {ceiling} -> {len(names)}; lower the baseline with --update")
    return 0


if __name__ == "__main__":
    sys.exit(main())
