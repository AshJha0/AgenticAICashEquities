"""Scenario evaluation: the 50 investigation scenarios, the 30 research scenarios and the null sweep.

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

from ceap.data.research_scenarios import ResearchScenarioSpec, all_research_scenarios
from ceap.data.scenarios import ScenarioSpec, all_scenarios
from ceap.harness.memory import CaseMemory
from ceap.llm.recording import llm_provenance, model_metrics_label
from ceap.platform import Platform, default_request, default_research_request


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


def evaluation_platform() -> Platform:
    """R3B 3B.7 (D10): case memory is OFF in evaluation, so no run can learn from another."""
    return Platform(case_memory=CaseMemory(enabled=False))


async def run_evaluation(
    limit: int | None = None, verbose: bool = False, platform: Platform | None = None
) -> dict[str, Any]:
    platform = platform or evaluation_platform()
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
    provenance = llm_provenance(platform.llm)  # R3A 3A.5: never publish model behaviour from a non-recorded run
    return {
        "scenarios": n,
        "llm_provenance": provenance,
        "model_behaviour_metrics": model_metrics_label(provenance),
        "case_memory": {"enabled": platform.case_memory.enabled, "reads": platform.case_memory.reads},
        "primary_accuracy": sum(r["primary_hit"] for r in rows) / n if n else 0.0,
        "coverage": sum(r["coverage"] for r in rows) / n if n else 0.0,
        "false_positive_rate": sum(bool(r["false_positives"]) for r in rows) / n if n else 0.0,
        "completed": sum(r["completed"] for r in rows) / n if n else 0.0,
        "unresolved_findings": sum(len(r["unresolved_findings"]) for r in rows),
        "number_warnings": sum(r["number_warnings"] for r in rows),
        "mean_duration_ms": sum(r["duration_ms"] for r in rows) / n if n else 0.0,
        "rows": rows,
    }


# ------------------------------------------------------------------ research
async def evaluate_research_scenario(platform: Platform, spec: ResearchScenarioSpec) -> dict[str, Any]:
    """Run the research pipeline on one scenario and compare the proposal with its ground truth."""
    started = time.perf_counter()
    result = await platform.research(default_research_request(spec.id))
    proposal = result.report.proposal if result.report else {}
    verdict = proposal.get("verdict")
    flags = set(proposal.get("flags") or [])
    expected = {f.value for f in spec.expected_flags}
    evidence_ids = {e.id for e in result.evidence}
    unresolved = [f.id for f in result.findings if any(e not in evidence_ids for e in f.supporting_evidence)]
    approval = next((s["status"] for s in result.step_results if s.get("step_id") == "gov-approval"), None)
    from ceap.data.ground_truth import derive_ground_truth

    truth = derive_ground_truth(spec)
    return {
        "near_threshold": truth.near_threshold,
        "expected_t_is": round(truth.metrics.get("t_is", float("nan")), 3),
        "expected_net_sharpe_oos": round(truth.metrics.get("net_sharpe_oos", float("nan")), 3),
        "scenario": spec.id,
        "template": spec.template,
        "signal": spec.signal,
        "expected_verdict": spec.expected_verdict.value,
        "verdict": verdict,
        "verdict_hit": verdict == spec.expected_verdict.value,
        "expected_flags": sorted(expected),
        "flags": sorted(flags),
        "flags_coverage": expected <= flags,
        "false_flags": sorted(flags - expected),
        "completed": result.success,
        "unresolved_findings": unresolved,
        "number_warnings": len(
            (result.report.critique.get("narrative_number_warnings") if result.report else []) or []
        ),
        "approval_state": approval,
        "staged": bool((proposal.get("staging") or {}).get("staged")),
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 1),
    }


async def run_research_evaluation(
    limit: int | None = None, verbose: bool = False, platform: Platform | None = None
) -> dict[str, Any]:
    platform = platform or evaluation_platform()
    specs = all_research_scenarios()[:limit] if limit else all_research_scenarios()
    rows = []
    for spec in specs:
        row = await evaluate_research_scenario(platform, spec)
        rows.append(row)
        if verbose:
            flag = "OK " if row["verdict_hit"] and row["flags_coverage"] else "MISS"
            print(
                f"{flag} {row['scenario']} {row['template']:18s} expected={row['expected_verdict']:7s}{row['expected_flags']} "
                f"got={str(row['verdict']):7s}{row['flags']} {row['duration_ms']:.0f}ms"
            )
    n = len(rows)
    clear = [r for r in rows if not r["near_threshold"]]
    near = [r for r in rows if r["near_threshold"]]
    return {
        "scenarios": n,
        "verdict_accuracy": sum(r["verdict_hit"] for r in rows) / n if n else 0.0,
        "clear_scenarios": len(clear),
        "clear_verdict_accuracy": sum(r["verdict_hit"] for r in clear) / len(clear) if clear else 0.0,
        "near_threshold_scenarios": len(near),
        "near_threshold_accuracy": sum(r["verdict_hit"] for r in near) / len(near) if near else float("nan"),

        "flags_coverage": sum(r["flags_coverage"] for r in rows) / n if n else 0.0,
        "false_flag_rate": sum(bool(r["false_flags"]) for r in rows) / n if n else 0.0,
        "completed": sum(r["completed"] for r in rows) / n if n else 0.0,
        "approved": sum(r["approval_state"] == "APPROVED" for r in rows) / n if n else 0.0,
        "unresolved_findings": sum(len(r["unresolved_findings"]) for r in rows),
        "number_warnings": sum(r["number_warnings"] for r in rows),
        "mean_duration_ms": sum(r["duration_ms"] for r in rows) / n if n else 0.0,
        "rows": rows,
    }


# ---------------------------------------------------------------------- null
def run_null_evaluation(n_seeds: int = 50, workers: int = 1) -> dict[str, Any]:
    """False-PROMOTE rate of the full search on the pure-noise set at the committed hurdle (2.10)."""
    from ceap.analytics.null_sweep import false_promote_curve, run_null_sweep
    from ceap.analytics.research_assessment import DEFAULT_THRESHOLDS

    sweep = run_null_sweep(n_seeds, workers=workers)
    stats = [p["stat"] if p["stat"] is not None else float("-inf") for p in sweep["per_seed"]]
    row = next(r for r in false_promote_curve(stats) if r["hurdle"] >= DEFAULT_THRESHOLDS.ic_t_stat_min - 1e-9)
    return {
        "seeds": n_seeds,
        "hurdle": DEFAULT_THRESHOLDS.ic_t_stat_min,
        "hurdle_sweep_hash": DEFAULT_THRESHOLDS.hurdle_sweep_hash,
        "false_promotes": row["false_promotes"],
        "false_promote_rate": row["rate"],
        "band95": (row["lower"], row["upper"]),
        "abs_t_gt_2_rate": sweep["abs_t_gt_2_rate"],
        "sweep_hash": sweep["sweep_hash"],
        "chosen_by_this_sweep": sweep["chosen"],
    }


# ----------------------------------------------------------------------- TCA (R2T)
def tca_attribution_for(spec: ScenarioSpec, generator: Any = None, matched: bool = True) -> dict[str, Any]:
    """Deterministic TCA attribution for one scenario, computed offline (no agents, no model calls).

    Mirrors what the quant agent computes from the MCP tools: window metrics, the same-day baseline,
    the time-of-day-matched peer windows (2T.2) and the market statistics, then
    :func:`attribute_causes`. Used by the seed sweeps behind the 2T.2 / 2T.4 acceptance numbers.
    """
    from datetime import timedelta

    from ceap.analytics.attribution import PeerWindow, attribute_causes
    from ceap.analytics.execution_metrics import StandardExecutionAnalytics
    from ceap.analytics.market_statistics import calculate_market_statistics
    from ceap.data.session import matched_windows
    from ceap.data.synthetic import SyntheticMarketGenerator

    ds = (generator or SyntheticMarketGenerator()).generate(spec)
    ds.orders.sort(key=lambda o: o.timestamp)
    ds.executions.sort(key=lambda x: x.timestamp)
    analytics = StandardExecutionAnalytics()
    post = timedelta(seconds=analytics.post_trade_horizon_seconds)

    import bisect

    keys: dict[int, list[float]] = {}

    def between(items: list[Any], s: Any, e: Any) -> list[Any]:
        # the generator returns every series sorted by timestamp, so slice by bisection
        if id(items) not in keys:
            keys[id(items)] = [x.timestamp.timestamp() for x in items]
        k = keys[id(items)]
        return items[bisect.bisect_left(k, s.timestamp()) : bisect.bisect_left(k, e.timestamp())]

    def metrics(s: Any, e: Any) -> Any:
        return analytics.calculate(
            between(ds.orders, s, e),
            between(ds.executions, s, e),
            between(ds.quotes, s, e + post),
            between(ds.trades, s, e),
            s,
            e,
        )

    def market(s: Any, e: Any) -> Any:
        return calculate_market_statistics(
            spec.symbol, s, e, between(ds.quotes, s, e), between(ds.trades, s, e), between(ds.order_books, s, e)
        )

    ws, we, bs, be = ds.window_start, ds.window_end, ds.baseline_start, ds.baseline_end
    window, baseline = metrics(ws, we), metrics(bs, be)
    peers = [PeerWindow(metrics(s, e), market(s, e)) for s, e in matched_windows(ws, we)] if matched else []
    result = attribute_causes(
        window, baseline, market(ws, we), market(bs, be), {"reject_rate": window.reject_rate}, peers=peers
    )
    material = sorted(c.cause.value for c in result.material)
    return {
        "scenario": spec.id,
        "template": spec.template,
        "seed": spec.seed,
        "side": spec.side,
        "truth": sorted(c.value for c in spec.ground_truth),
        "deteriorated": result.deteriorated,
        "basis": result.basis,
        "residual_bps": result.residual_bps,
        "residual_z": result.residual_z,
        "primary": result.primary.value,
        "material": material,
        "is_bps": window.implementation_shortfall_bps,
        "market_impact_bps": window.market_impact_bps,
        "markout_300s_bps": window.markouts_bps.get("300s", float("nan")),
    }


def _seed_specs(template: str, n_seeds: int, offset: int = 10_000) -> list[ScenarioSpec]:
    from dataclasses import replace

    from ceap.data.scenarios import SYMBOLS, get_scenario

    base = get_scenario(template)
    return [
        replace(
            base,
            id=f"{template}-s{k}",
            symbol=SYMBOLS[k % 5],
            seed=offset + 97 * k + base.seed,
            side="SELL" if k % 2 else "BUY",
        )
        for k in range(n_seeds)
    ]


def run_tca_false_flag(n_seeds: int = 200, template: str = "T01", matched: bool = True) -> dict[str, Any]:
    """Share of seeds of a benign template flagged as deteriorated, or given any material cause."""
    rows = [tca_attribution_for(s, matched=matched) for s in _seed_specs(template, n_seeds)]
    flagged = sum(r["deteriorated"] for r in rows)
    caused = sum(bool(r["material"]) for r in rows)
    return {
        "template": template,
        "seeds": n_seeds,
        "matched_baseline": matched,
        "deteriorated_rate": flagged / n_seeds,
        "any_cause_rate": caused / n_seeds,
        "mean_residual_z": sum(r["residual_z"] for r in rows) / n_seeds,
        "mean_market_impact_bps": sum(r["market_impact_bps"] for r in rows) / n_seeds,
        "rows": rows,
    }


def run_tca_cause_recall(
    n_seeds: int = 200, templates: tuple[str, ...] = ("T05", "T06", "T07", "T08", "T09", "T10")
) -> dict[str, Any]:
    """Per template: share of seeds whose true causes all appear among the material causes."""
    out: dict[str, Any] = {}
    for t in templates:
        rows = [tca_attribution_for(s) for s in _seed_specs(t, n_seeds)]
        hit = sum(set(r["truth"]) <= set(r["material"]) for r in rows)
        primary = sum(r["primary"] in r["truth"] for r in rows)
        out[t] = {"seeds": n_seeds, "coverage": hit / n_seeds, "primary_accuracy": primary / n_seeds}
    return out


# ---------------------------------------------------------------------- calibration (2B.4)
def run_calibration_evaluation(recompute: bool = False) -> dict[str, Any]:
    """Held-out calibration of finding confidences: Brier and ECE on seeds 100-199 (fit on 0-99).

    Reads the registered calibration card; ``recompute`` re-derives the held-out numbers from the
    generator with the registered detector and map (minutes, not seconds).
    """
    from ceap.analytics.calibration import (
        CHANGE_POINT_KEY,
        EVAL_SEEDS,
        brier,
        calibration_model_name,
        change_point_samples,
        ece,
    )
    from ceap.ml.registry import ModelRegistry

    model = ModelRegistry().load(calibration_model_name(CHANGE_POINT_KEY))
    out: dict[str, Any] = {
        "key": CHANGE_POINT_KEY,
        "model_hash": model.model_hash,
        "label": model.card.label,
        **model.card.metrics,
    }
    if recompute:
        from ceap.analytics.calibration import IsotonicMap

        p, y = change_point_samples(EVAL_SEEDS)
        cal = IsotonicMap(tuple(model.params["x"]), tuple(model.params["y"]))(p)
        out["recomputed"] = {
            "n_eval": len(p),
            "brier_raw": brier(p, y),
            "brier_calibrated": brier(cal, y),
            "ece_raw": ece(p, y),
            "ece_calibrated": ece(cal, y),
        }
    return out
