"""Run study E1. Without flags: in-sample only (1927-1989). With ``--open-holdout``: the single,
ledgered holdout opening (refused if already opened). Writes JSON into ``docs/research/e1/``.

    python scripts/run_e1_industry_momentum.py
    python scripts/run_e1_industry_momentum.py --open-holdout
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ceap.studies.industry_momentum import STUDY_DIR, run  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open-holdout", action="store_true")
    ap.add_argument("--crash-rerun", default=None, help="reason; completes a crashed opening once")
    a = ap.parse_args()
    res = run(open_holdout=a.open_holdout, crash_rerun_reason=a.crash_rerun)
    a.open_holdout = a.open_holdout or bool(a.crash_rerun)
    name = "result_holdout.json" if a.open_holdout else "result_in_sample.json"
    (STUDY_DIR / name).write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
