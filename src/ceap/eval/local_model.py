"""Local-model evaluation runs (0.9.0 B1): agent evals, golden subset and claims, at n seeds.

Plain idea: run the same evaluations the mock runs, but with a real open-weight model on this
machine, once per seed, record every reply in a ``local_model`` cassette, and report each metric as
the pooled rate with a 95 % Wilson interval plus the spread between seeds.

Formal statement. For seeds ``s_1..s_m`` (m >= 3) a run calls

* :func:`ceap.eval.agents.agent_evals` (planner 10 gold plans, critic 5 cases, reporter 4 cases);
* :func:`ceap.evaluation_report.golden_scores` on the first ``golden_limit`` investigation items of
  the signed golden set (plus ``max(1, limit // 2)`` research items), through an
  :class:`ceap.llm.router.LLMRouter` with NO fallback client (R-01): a cassette miss or a model
  error is an error, never a mock answer; the repair rung is still reported to an
  :class:`AISLOMonitor` (B8);
* the claims check: share of proposed claims dropped for an untraceable number.

A run refuses to return ``evidence_class: local_model`` (raises :class:`EvaluationFallbackError`)
when any call missed the cassette or was answered by a fallback client (R-01: the 0.9.0 golden slice
was produced by the mock fallback after every request missed).

Pooled rates add ``k`` and ``n`` across seeds. Seeds are not independent draws of the case set
(the cases are the same), so the pooled interval understates between-case uncertainty; the per-seed
values and their range are reported next to it.

The compute budget is fixed before any run (a CPU-only laptop, ~5 output tokens/s): agent evals in
full, golden ``golden_limit=3``. That bar is in the CLI default and in
``docs/evaluation/local_model/README.md``; it was not changed after seeing results.

Worked example: ``python -m ceap.eval.local_model --model qwen2.5:7b-instruct --seeds 1 2 3``
writes ``tests/cassettes/local_model/qwen2.5_7b-instruct_s1.json`` (and s2, s3) and
``docs/evaluation/local_model/qwen2.5_7b-instruct.json``. ``--replay`` re-scores from the cassettes
with no Ollama, which is what CI does; ``--replay --write --seeds 1 2 3`` rewrites the pooled file
after seeds were recorded one process at a time (a single-seed live run writes only its ``_s<seed>``
checkpoint).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from pathlib import Path
from typing import Any

from ceap.eval.stats import rate
from ceap.llm.client import LLMClient
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
from ceap.llm.recording import CassetteStaleError

REPO = Path(__file__).resolve().parents[3]
CASSETTES = REPO / "tests" / "cassettes" / "local_model"
RESULTS = REPO / "docs" / "evaluation" / "local_model"
DEFAULT_SEEDS = (1, 2, 3)
GOLDEN_LIMIT = 3  # a priori compute budget (see module docstring)


class EvaluationFallbackError(RuntimeError):
    """A local-model evaluation call missed its cassette or was answered by a fallback (R-01)."""


def refuse_fallback(result: dict[str, Any], client: Any) -> None:
    """Raise unless every call of ``result`` was answered by the model (or its exact recording)."""
    misses = int(getattr(client, "misses", 0))
    fallbacks = int(result.get("fallbacks", 0))
    if misses or fallbacks:
        raise EvaluationFallbackError(
            f"refusing evidence_class local_model: {misses} cassette miss(es), "
            f"{fallbacks} fallback answer(s) in the run"
        )
TEMPERATURE = 0.7  # sampling on, so seeds differ; Ollama's own default is 0.8


def slug(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


def cassette_path(model: str, seed: int, kind: str = "eval") -> Path:
    return CASSETTES / f"{slug(model)}_{kind}_s{seed}.json"


async def claims_audit(client: LLMClient) -> dict[str, Any]:
    """The reporter's claims on :func:`reporter_cases`, audited and explained (0.10.0 T5, R-05).

    Names say what is checked. ``claims_kept_by_audit`` is the share of proposed claims that pass
    :func:`ceap.agents.reporter.validate_claims` (fact keys resolve, evidence ids known, each number
    traces to the claim's own fact keys with its metric and direction, a stated cause is the
    primary); it was published in 0.9.0 as "faithful claims". ``claims_with_number_not_in_own_facts``
    is the share of proposed claims carrying a number that is not traceable to their own fact keys;
    it was published as "untraceable numbers reaching the report", but it counts what the model
    proposed, before the audit. ``drop_reasons`` counts every reason of every dropped claim by
    category, and ``planted_rejected`` scores the audit itself on planted wrong-metric, wrong-sign
    and wrong-cause claims (deterministic; no model involved).
    """
    from ceap.agents.reporter import drop_reason_kind, validate_claims
    from ceap.eval.agents import _claims, reporter_cases
    from ceap.eval.clients import PLANTED_AUDIT_KINDS, planted_audit_claims

    proposed = kept_n = untraceable = 0
    reasons: dict[str, int] = {}
    dropped_rows: list[dict[str, Any]] = []
    planted = {k: [0, 0] for k in PLANTED_AUDIT_KINDS}  # kind -> [rejected, n]
    for case in reporter_cases():
        kept, dropped, _ = await _claims(client, case["facts"])
        proposed += len(kept) + len(dropped)
        kept_n += len(kept)
        for d in dropped:
            kinds = [drop_reason_kind(r) for r in d.get("reasons", [])]
            untraceable += any(k in ("number_not_traceable", "wrong_metric", "wrong_direction") for k in kinds)
            for k in kinds:
                reasons[k] = reasons.get(k, 0) + 1
            dropped_rows.append({"case": case["id"], "text": d.get("text", ""), "reasons": d.get("reasons", [])})
        for kind, c in planted_audit_claims(case["facts"]):
            _, rejected = validate_claims([c], case["facts"])
            planted[kind][0] += len(rejected)
            planted[kind][1] += 1
    return {
        "claims_kept_by_audit": rate(kept_n, proposed),
        "claims_with_number_not_in_own_facts": rate(untraceable, proposed),
        "drop_reasons": dict(sorted(reasons.items())),
        "dropped": dropped_rows,
        "planted_rejected": {k: rate(r, n) for k, (r, n) in planted.items()},
    }


async def untraceable_rate(client: LLMClient) -> dict[str, Any]:
    """Claims carrying a number not traceable to their own fact keys, with the drop-reason counts."""
    audit = await claims_audit(client)
    return {**audit["claims_with_number_not_in_own_facts"], "drop_reasons": audit["drop_reasons"]}


async def run_seed(
    client: LLMClient, golden_limit: int = GOLDEN_LIMIT, fallback: LLMClient | None = None
) -> dict[str, Any]:
    from ceap.eval.agents import agent_evals
    from ceap.evaluation_report import golden_scores
    from ceap.harness.memory import CaseMemory
    from ceap.llm.router import LLMRouter
    from ceap.observability.ai_slo import AISLOMonitor
    from ceap.platform import Platform

    agents = await agent_evals(client)
    untrace = await untraceable_rate(client)
    monitor = AISLOMonitor(
        p95_ms={k: 1_800_000 for k in ("planning", "claims", "narrative", "critique", "judge")}
    )
    router = LLMRouter(client, fallback=fallback)  # R-01: None in evaluation runs
    router.slo = monitor
    golden = await golden_scores(
        Platform(llm=router, case_memory=CaseMemory(enabled=False)), limit=golden_limit
    )
    return {
        "fallbacks": router.usage["fallbacks"],
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
    # 0.10.0 T5: published names say what is checked (0.9.0 names: reporter.faithfulness and
    # claims.untraceable_number; the 0.9.0 result files keep those keys until they are re-recorded)
    "reporter.claims_kept_by_audit": ("agents", "reporter", "faithfulness"),
    "reporter.claim_drop": ("agents", "reporter", "claim_drop"),
    "reporter.citation_verified": ("agents", "reporter", "citation_verified"),
    "claims.number_not_in_own_facts": ("untraceable_number",),
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
        grecs = [(s, v, round(v * g[n_key])) for s, v, g in items if v is not None]
        if grecs:
            n = sum(g[n_key] for _, v, g in items if v is not None)
            out[f"golden.{key}"] = {
                **rate(sum(k for _, _, k in grecs), n),
                "per_seed": {str(s): v for s, v, _ in grecs},
            }
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
            cas = LocalCassette(
                provenance=LocalProvenance(model=model, digest=digest, seed=seed, temperature=TEMPERATURE)
            )
            client = OllamaLLMClient(model, seed=seed, temperature=TEMPERATURE, recorder=cas, checkpoint=path)
        try:
            result = await run_seed(client, golden_limit)
        except CassetteStaleError as exc:
            raise EvaluationFallbackError(f"seed {seed}: cassette miss: {exc}") from exc
        refuse_fallback(result, client)
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


async def claims_audit_replay(model: str, seeds: tuple[int, ...]) -> dict[str, Any]:
    """:func:`claims_audit` on each recorded seed's cassette, pooled (no model call, no golden run)."""
    per_seed: dict[str, Any] = {}
    for seed in seeds:
        per_seed[str(seed)] = await claims_audit(LocalCassetteClient(load_local_cassette(cassette_path(model, seed))))
    pooled: dict[str, Any] = {}
    for name in ("claims_kept_by_audit", "claims_with_number_not_in_own_facts"):
        recs = [r[name] for r in per_seed.values()]
        pooled[name] = {
            **rate(sum(x["k"] for x in recs), sum(x["n"] for x in recs)),
            "per_seed": {s: r[name]["rate"] for s, r in per_seed.items()},
        }
    reasons: dict[str, int] = {}
    for r in per_seed.values():
        for k, v in r["drop_reasons"].items():
            reasons[k] = reasons.get(k, 0) + v
    pooled["drop_reasons"] = dict(sorted(reasons.items()))
    return {
        "evidence_class": "local_model",
        "model": model,
        "seeds": list(seeds),
        "audit": "0.10.0 T5: each number bound to the claim's own fact keys, metric, direction and cause checked",
        "pooled": pooled,
        "per_seed": per_seed,
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
    ap.add_argument(
        "--write", action="store_true",
        help="with --replay: write the pooled aggregate (run once after every seed has been recorded)",
    )
    ap.add_argument(
        "--claims-audit", action="store_true",
        help="with --replay: re-audit the recorded reporter claims only and write <model>_claims_audit.json",
    )
    a = ap.parse_args(argv)
    if a.claims_audit:
        if not a.replay:
            raise SystemExit("--claims-audit re-scores recorded claims; pass --replay")
        audit = asyncio.run(claims_audit_replay(a.model, tuple(a.seeds)))
        _write(RESULTS / f"{slug(a.model)}_claims_audit.json", audit)
        print(json.dumps(audit["pooled"], indent=1))
        return
    if not a.replay:
        from ceap.llm.ollama import live_enabled

        if not live_enabled():
            raise SystemExit("set CEAP_LOCAL_MODEL=1 to call the local model (or pass --replay)")
    doc = asyncio.run(run(a.model, tuple(a.seeds), replay=a.replay, golden_limit=a.golden_limit))
    # A live run of one seed only checkpoints that seed: writing the pooled file there would let each
    # seed process overwrite the others. The pooled file comes from a live multi-seed run or a replay.
    if (a.replay and a.write) or (not a.replay and len(a.seeds) > 1):
        _write(RESULTS / f"{slug(a.model)}.json", doc)
    print(json.dumps(doc["metrics"], indent=1))


if __name__ == "__main__":
    main()
