"""Run the full null sweep and write the per-seed vector (0.10 D20, D21).

Usage: ``python scripts/run_null_sweep_full.py OUT.json [n_seeds] [workers]``. The output holds the whole
sweep summary including the per-seed statistic vector; ``ceap.analytics.null_sweep.write_thresholds`` turns
it into ``data/reference/research_thresholds.json`` and appends the run to the sweep ledger.
"""

from __future__ import annotations

import json
import sys
import time

from ceap.analytics.null_sweep import run_null_sweep


def main() -> None:
    out = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    t0 = time.time()
    sweep = run_null_sweep(n, workers=workers)
    sweep["wall_seconds"] = round(time.time() - t0, 1)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(sweep, fh, indent=1, default=float)
    print("done", sweep["sweep_hash"], sweep["wall_seconds"])


if __name__ == "__main__":
    main()
