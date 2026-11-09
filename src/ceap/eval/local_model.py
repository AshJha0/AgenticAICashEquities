"""Local-model evaluation runs (0.9.0 B1): agent evals, golden subset and claims, at n seeds.

Plain idea: run the same evaluations the mock runs, but with a real open-weight model on this
machine, once per seed, record every reply in a ``local_model`` cassette, and report each metric as
the pooled rate with a 95 % Wilson interval plus the spread between seeds.

Formal statement. For seeds ``s_1..s_m`` (m >= 3) a run calls

* :func:`ceap.eval.agents.agent_evals` (planner 10 gold plans, critic 5 cases, reporter 4 cases);
* :func:`ceap.evaluation_report.golden_scores` on the first ``golden_limit`` investigation items of
  the signed golden set (plus ``max(1, limit // 2)`` research items), through an
  :class:`ceap.llm.router.LLMRouter` with the deterministic mock as fallback, so the runtime
  ladder (repair, fallback) is exercised and reported to an :class:`AISLOMonitor` (B8);
* the claims check: share of proposed claims dropped for an untraceable number.

Pooled rates add ``k`` and ``n`` across seeds. Seeds are not independent draws of the case set
(the cases are the same), so the pooled interval understates between-case uncertainty; the per-seed
values and their range are reported next to it.

The compute budget is fixed before any run (a CPU-only laptop, ~5 output tokens/s): agent evals in
full, golden ``golden_limit=3``. That bar is in the CLI default and in
``docs/evaluation/local_model/README.md``; it was not changed after seeing results.

Worked example: ``python -m ceap.eval.local_model --model qwen2.5:7b-instruct --seeds 1 2 3``
writes ``tests/cassettes/local_model/qwen2.5_7b-instruct_s1.json`` (and s2, s3) and
``docs/evaluation/local_model/qwen2.5_7b-instruct.json``. ``--replay`` re-scores from the cassettes
with no Ollama, which is what CI does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from pathlib import Path
from typing import Any

from ceap.eval.stats import rate
from ceap.llm.client import LLMClient, MockLLMClient
from ceap.llm.ollama import (
    PRIMARY_LOCAL_MODEL,
    LocalCassette,
    LocalCassetteClient,
    LocalProvenance,
    OllamaLLMClient,
    load_local_cassette,
    model_digest,
    write_local_cassette,
)

REPO = Path(__file__).resolve().parents[3]
CASSETTES = REPO / "tests" / "cassettes" / "local_model"
RESULTS = REPO / "docs" / "evaluation" / "local_model"
DEFAULT_SEEDS = (1, 2, 3)
GOLDEN_LIMIT = 3  # a priori compute budget (see module docstring)
TEMPERATURE = 0.7  # sampling on, so seeds differ; Ollama's own default is 0.8


def slug(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


def cassette_path(model: str, seed: int, kind: str = "eval") -> Path:
    return CASSETTES / f"{slug(model)}_{kind}_s{seed}.json"


async def untraceable_rate(client: LLMClient) -> dict[str, Any]:
    from ceap.eval.agents import _claims, reporter_cases

    proposed = untraceable = 0
    for case in reporter_cases():
        kept, dropped, _ = await _claims(client, case["facts"])
        proposed += len(kept) + len(dropped)
        untraceable += sum(any("not traceable" in r for r in d.get("reasons", [])) for d in dropped)
    return rate(untraceable, proposed)


async def run_seed(client: LLMClient, golden_limit: int = GOLDEN_LIMIT) -> dict[str, Any]:
    from ceap.eval.agents import agent_evals
    from ceap.evaluation_report import golden_scores
    from ceap.harness.memory import CaseMemory
    from ceap.llm.router import LLMRouter
    from ceap.observability.ai_slo import AISLOMonitor
    from ceap.platform import Platform

    agents = await agent_evals(client)
    untrace = await untraceable_rate(client)
    monitor = AISLOMonitor(p95_ms={k: 1_800_000 for k in ("planning", "claims", "narrative", "critique", "judge")})
    router = LLMRouter(client, fallback=MockLLMClient())
    router.slo = monitor
    golden = await golden_scores(Platform(llm=router, case_memory=CaseMemory(enabled=False)), limit=golden_limit)
    return {
        "agents": agents,
        "untraceable_number": untrace,
        "golden": {k: v for k, v in golden.items() if k != "rows"},
        "golden_rows": golden["rows"],
        "slo": monitor.snapshot(),
        "slo_alerts": [a.__dict__ for a in monitor.alerts],
    }


# metric name -> path to a rate record in a run_seed result
RATE_PATHS: dict[str, tuple[str, ...]] = {
    "planner.tool_recall": ("agents", "planner", "tool_recall"),
    "planner.tool_precision": ("agents", "planner", "tool_precision"),
    "planner.plan_valid": ("agents", "planner", "plan_valid"),
    "planner.repair": ("agents", "planner", "repair"),
    "planner.canonical_fallback": ("agents", "planner", "canonical_fallback"),
    "critic.detection": ("agents", "critic", "detection"),
    "reporter.claims_usable": ("agents", "reporter", "claims_usable"),
    "reporter.faithfulness": ("agents", "reporter", "faithfulness"),
    "reporter.claim_drop": ("agents", "reporter", "claim_drop"),
    "reporter.citation_verified": ("agents", "reporter", "citation_verified"),
    "claims.untraceable_number": ("untraceable_number",),
}
GOLDEN_KEYS = ("primary_cause_accuracy", "must_mention_rate", "no_forbidden_claim_rate", "verdict_accuracy")


def _get(d: Any, path: tuple[str, ...]) -> Any:
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
    return d


def aggregate(per_seed: dict[int, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, path in RATE_PATHS.items():
        recs = [(s, _get(r, path)) for s, r in per_seed.items()]
        recs = [(s, x) for s, x in recs if isinstance(x, dict) and "k" in x]
        if not recs:
            continue
        pooled = rate(sum(x["k"] for _, x in recs), sum(x["n"] for _, x in recs))
        vals = [x["rate"] for _, x in recs if x["rate"] is not None]
        out[name] = {
            **pooled,
            "per_seed": {str(s): x["rate"] for s, x in recs},
            "seed_range": [min(vals), max(vals)] if vals else None,
            "seed_sd": round(statistics.stdev(vals), 4) if len(vals) > 1 else None,
        }
    for key in GOLDEN_KEYS:
        items = [(s, r["golden"].get(key), r["golden"]) for s, r in per_seed.items()]
        n_key = "research_items" if key == "verdict_accuracy" else "investigation_items"
        recs = [(s, v, round(v * g[n_key])) for s, v, g in items if v is not None]
        if recs:
            n = sum(g[n_key] for _, v, g in items if v is not None)
            out[f"golden.{key}"] = {**rate(sum(k for _, _, k in recs), n), "per_seed": {str(s): v for s, v, _ in recs}}
    return out


async def run(
    model: str, seeds: tuple[int, ...], *, replay: bool = False, golden_limit: int = GOLDEN_LIMIT
) -> dict[str, Any]:
    per_seed: dict[int, dict[str, Any]] = {}
    digest = ""
    for seed in seeds:
        path = cassette_path(model, seed)
        if replay:
            client: LLMClient = LocalCassetteClient(load_local_cassette(path))
            digest = load_local_cassette(path).provenance.digest
        else:
            digest = model_digest(model)
            cas = LocalCassette(provenance=LocalProvenance(model=model, digest=digest, seed=seed, temperature=TEMPERATURE))
            client = OllamaLLMClient(model, seed=seed, temperature=TEMPERATURE, recorder=cas)
        result = await run_seed(client, golden_limit)
        result["latency_ms"] = _latency(client)
        per_seed[seed] = result
        if not replay:
            write_local_cassette(path, cas)
            _write(RESULTS / f"{slug(model)}_s{seed}.json", result)  # checkpoint per seed
    return {
        "evidence_class": "local_model",
        "model": model,
        "digest": digest,
        "temperature": TEMPERATURE,
        "seeds": list(seeds),
        "golden_limit": golden_limit,
        "metrics": aggregate(per_seed),
        "latency_ms": {str(s): r["latency_ms"] for s, r in per_seed.items()},
        "slo_alerts": {str(s): r["slo_alerts"] for s, r in per_seed.items()},
        "note": "pooled k/n over seeds with a 95% Wilson interval; seeds share the case set, so also read per_seed and seed_range",
    }


def _latency(client: Any) -> dict[str, Any]:
    from ceap.observability.ai_slo import p95

    out: dict[str, Any] = {}
    for purpose in sorted({p for p, _ in client.latencies}):
        xs = [ms for p, ms in client.latencies if p == purpose]
        out[purpose] = {"n": len(xs), "median": round(statistics.median(xs), 1), "p95": round(p95(xs), 1)}
    return out


def _write(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default=PRIMARY_LOCAL_MODEL)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    ap.add_argument("--golden-limit", type=int, default=GOLDEN_LIMIT)
    ap.add_argument("--replay", action="store_true")
    a = ap.parse_args(argv)
    if not a.replay:
        from ceap.llm.ollama import live_enabled

        if not live_enabled():
            raise SystemExit("set CEAP_LOCAL_MODEL=1 to call the local model (or pass --replay)")
    doc = asyncio.run(run(a.model, tuple(a.seeds), replay=a.replay, golden_limit=a.golden_limit))
    if not a.replay:
        _write(RESULTS / f"{slug(a.model)}.json", doc)
    print(json.dumps(doc["metrics"], indent=1))


if __name__ == "__main__":
    main()
