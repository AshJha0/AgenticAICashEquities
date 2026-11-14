"""Research evaluation on the 30-name and the 500-name universe, written into ``latest.json`` (0.10.0 Q6).

Usage: ``python scripts/research_eval_universes.py [workers]``. Each universe's 30 research scenarios are
split across ``workers`` processes (deterministic: every scenario runs on a fresh in-memory ledger and the
evaluation platform, exactly as ``ceap.evaluation.run_research_evaluation``). The script then sets
``capability.research_verdict_accuracy`` (30 names) and ``capability.research_500`` in
``docs/evaluation/latest.json`` and regenerates the evaluation-report block in
``docs/evaluation/evaluation.md``; nothing else in the report is touched. Set
``CEAP_STEP_TIMEOUT_SECONDS`` high enough for the 500-name backtests (600 was used).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LATEST = ROOT / "docs" / "evaluation" / "latest.json"
EVAL_MD = ROOT / "docs" / "evaluation" / "evaluation.md"


def _slice(job: tuple[str, list[str]]) -> list[dict]:
    from ceap.data.research_scenarios import get_research_scenario
    from ceap.evaluation import evaluate_research_scenario, evaluation_platform

    universe, ids = job

    async def run() -> list[dict]:
        rows = []
        for sid in ids:
            rows.append(await evaluate_research_scenario(evaluation_platform(universe), get_research_scenario(sid)))
        return rows

    return asyncio.run(run())


def evaluate(universe: str, workers: int) -> dict:
    from ceap.data.research_scenarios import all_research_scenarios

    ids = [s.id for s in all_research_scenarios()]
    chunks = [ids[i::workers] for i in range(workers)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows = [r for part in pool.map(_slice, [(universe, c) for c in chunks]) for r in part]
    order = {sid: i for i, sid in enumerate(ids)}
    rows.sort(key=lambda r: order[r["scenario"]])
    n = len(rows)
    clear = [r for r in rows if not r["near_threshold"]]
    return {
        "scenarios": n,
        "verdict_accuracy": sum(r["verdict_hit"] for r in rows) / n,
        "clear_verdict_accuracy": sum(r["verdict_hit"] for r in clear) / len(clear) if clear else 0.0,
        "flags_coverage": sum(r["flags_coverage"] for r in rows) / n,
        "completed": sum(r["completed"] for r in rows) / n,
        "verdicts": {v: sum(r["verdict"] == v for r in rows) for v in sorted({str(r["verdict"]) for r in rows})},
        "rows": rows,
    }


def main() -> None:
    from ceap.evaluation_report import render_markdown, research_universe_block

    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    t0 = time.time()
    core = evaluate("core30", workers)
    big = evaluate("synthetic500", workers)
    for name, res in (("core30", core), ("synthetic500", big)):
        if res["completed"] < 1.0:  # a timed-out run is not a result; never publish it
            raise SystemExit(f"{name}: {res['completed']:.0%} of scenarios completed; raise CEAP_STEP_TIMEOUT_SECONDS")
    latest = json.loads(LATEST.read_text(encoding="utf-8"))
    cap = latest["capability"]
    cap["research_scenarios"] = core["scenarios"]
    cap["research_verdict_accuracy"] = round(core["verdict_accuracy"], 4)
    cap["research_500"] = research_universe_block(big, "synthetic500", core)
    LATEST.write_bytes((json.dumps(latest, indent=1) + "\n").encode("utf-8"))
    text = EVAL_MD.read_text(encoding="utf-8")
    pattern = re.compile(r"(<!-- evaluation-report:begin -->\n)(.*?)(\n<!-- evaluation-report:end -->)", re.S)
    if pattern.search(text):
        EVAL_MD.write_bytes(pattern.sub(lambda m: m.group(1) + render_markdown(latest).strip() + m.group(3),
                                        text).encode("utf-8"))
    out = {"core30": {k: v for k, v in core.items() if k != "rows"}, "synthetic500": cap["research_500"],
           "core30_rows": [{k: r[k] for k in ("scenario", "expected_verdict", "verdict", "flags")} for r in core["rows"]],
           "synthetic500_rows": [{k: r[k] for k in ("scenario", "expected_verdict", "verdict", "flags")}
                                 for r in big["rows"]],
           "wall_seconds": round(time.time() - t0, 1)}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
