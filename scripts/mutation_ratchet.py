"""Surviving-mutant ratchet for the nightly mutmut job (H3, 0.9.0).

Plain idea: mutmut changes one operator at a time in ``ceap.analytics`` and ``ceap.domain`` and
reruns the tests; a mutant the tests do not notice "survives". The number of survivors may go down
but never up. ``tests/mutation/baseline.json`` holds the current ceiling.

Usage (after ``mutmut run``):
    python scripts/mutation_ratchet.py            # compare with the baseline; exit 1 if worse
    python scripts/mutation_ratchet.py --update   # write the current count as the new baseline

Until a baseline is committed (``"survived": null``) the script reports and passes: the first
nightly run measures the number, a maintainer commits it, and from then on it is a ceiling.
mutmut does not run natively on Windows, so the baseline can only come from the Linux nightly.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

BASELINE = Path(__file__).resolve().parents[1] / "tests" / "mutation" / "baseline.json"


def survivors() -> list[str]:
    out = subprocess.run(["mutmut", "results"], capture_output=True, text=True, check=True).stdout
    return sorted(line.split(":")[0].strip() for line in out.splitlines() if line.rstrip().endswith(": survived"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="surviving-mutant ratchet")
    ap.add_argument("--update", action="store_true")
    args = ap.parse_args(argv)
    names = survivors()
    print(f"surviving mutants: {len(names)}")
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
