"""Uniform per-study rebuild check (0.10 D20).

Usage::

    python scripts/rebuild_study.py LOCK.json [--vintage data/real_cache/vintage.json] [--null-quick]

For any study it checks, in order, and prints one JSON report:

1. the preregistration named in the lock hashes to the recorded SHA-256 (LF-normalised);
2. when ``--vintage`` is given and the lock records ``vintage_sha256``, the data-vintage manifest
   (``ceap.data.real.vintage``) hashes to it, so the study runs on the bytes it was registered on;
3. when ``--null-quick`` is given, the first 50 seeds of the published null-sweep per-seed vector are
   recomputed and compared seed by seed (a few minutes; the full 500 seeds take about an hour).

It exits non-zero on the first mismatch. It never downloads data and never opens a holdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("lock", type=Path)
    ap.add_argument("--vintage", type=Path)
    ap.add_argument("--null-quick", action="store_true")
    args = ap.parse_args(argv)
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    report: dict[str, object] = {"lock": str(args.lock)}
    prereg = ROOT / lock["prereg"]
    report["prereg_ok"] = _sha(prereg) == lock["sha256"]
    if not report["prereg_ok"]:
        print(json.dumps(report, indent=1))
        return 1
    if args.vintage is not None and "vintage_sha256" in lock:
        report["vintage_ok"] = _sha(args.vintage) == lock["vintage_sha256"]
        if not report["vintage_ok"]:
            print(json.dumps(report, indent=1))
            return 1
    if args.null_quick:
        from ceap.analytics.null_sweep import PER_SEED_PATH, sweep_seed

        published = json.loads(PER_SEED_PATH.read_text(encoding="utf-8"))["per_seed"][:50]
        bad = []
        for p in published:
            got = sweep_seed(p["seed"]).stat
            got_r = None if got == float("-inf") else round(got, 6)
            if got_r != p["stat"]:
                bad.append({"seed": p["seed"], "published": p["stat"], "recomputed": got_r})
        report["null_quick_ok"] = not bad
        report["null_quick_mismatches"] = bad
        if bad:
            print(json.dumps(report, indent=1))
            return 1
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
