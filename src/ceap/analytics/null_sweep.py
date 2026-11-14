"""Null sweep and PROMOTE-hurdle selection (2.10, decision D4).

On each seed of the pure-noise set (no premium of any kind) the sweep runs the
*full* search the agent can run - all 24 configurations of the enumerated
search space (2.8) - through the assessment's gate chain at a candidate
hurdle ``h``. A seed is a **false PROMOTE at h** when any configuration
passes every gate: in-sample non-overlapping IC t >= h, deflated Sharpe
probability >= ``dsr_min`` with the 24 trials counted, out-of-sample IC t >=
``oos_ic_t_min`` and not a regime break, out-of-sample net Sharpe >=
``sharpe_min`` without cost drag. (Concentration and limit checks are ignored,
which can only overstate the false-PROMOTE rate.)

0.10 (D3): the hurdle is now fixed a priori (``t >= 3`` for a mined result,
``1.645`` for one preregistered test). The sweep no longer chooses it; it
*measures* the false-PROMOTE rate at the a-priori hurdle and publishes the
per-seed statistic vector (D20) so anyone can recompute the curve. Each run
is appended to the sweep ledger with the generator version (D21), so a
re-sweep after a generator change is visible, not silent.

0.10 (Q2, finding R-16): the deflated-Sharpe gate is the production call,
:func:`ceap.analytics.research_assessment.alpha_deflated_sharpe`, with the 24
trials counted and no ledger variance. Production floors the ledger variance
at the null value ``1 / T``, and omitting it is exactly that floor, the most
permissive value the shipped gate can use; the measured false-PROMOTE rate is
therefore an upper bound for the shipped gate. The pass bar is
``dsr_min = 0.95`` (decision O5).

Each seed therefore has a statistic ``m = max over configurations of the
in-sample t among configurations that pass every other gate`` (``-inf`` when
none does); the false-PROMOTE rate at ``h`` is ``#{m >= h} / n``. The hurdle is
the lowest ``h`` on a 0.05 grid whose Clopper-Pearson 95 % upper bound is at
most 5 %. Backtests are run lazily, only for configurations whose IC gates
pass at the lowest candidate hurdle, which is exact for the AND of gates.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ceap.analytics.backtest import run_backtest
from ceap.analytics.research_assessment import DEFAULT_THRESHOLDS, ResearchThresholds, alpha_deflated_sharpe
from ceap.analytics.signal_statistics import evaluate_signal
from ceap.domain.research import search_space
from ceap.paths import data_path
from ceap.util.canonical import content_hash

TARGET_RATE = 0.05
GRID_STEP = 0.05
GRID_MIN = 0.0
GRID_MAX = 6.0
SWEEP_VERSION = "null-sweep-v1"
GENERATOR_VERSION = "realistic_v1"  # frozen per sweep (D21); bump when the noise generator changes
SWEEP_LEDGER = data_path("reference", "sweep_ledger.jsonl")
PER_SEED_PATH = Path(__file__).resolve().parents[3] / "docs" / "research" / "null_sweep_per_seed.json"


def binomial_cdf(k: int, n: int, p: float) -> float:
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    logp, logq = math.log(p), math.log1p(-p)
    total = 0.0
    for i in range(k + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * logp + (n - i) * logq)
    return min(1.0, total)


def clopper_pearson(k: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """Exact two-sided binomial confidence interval for ``k`` successes in ``n`` trials."""
    if n <= 0:
        return 0.0, 1.0
    a = (1.0 - level) / 2.0

    def solve(f: Any) -> float:
        lo, hi = 0.0, 1.0
        for _ in range(80):
            mid = (lo + hi) / 2.0
            if f(mid):
                hi = mid
            else:
                lo = mid
        return (lo + hi) / 2.0

    lower = 0.0 if k == 0 else solve(lambda p: 1.0 - binomial_cdf(k - 1, n, p) >= a)
    upper = 1.0 if k == n else solve(lambda p: binomial_cdf(k, n, p) <= a)
    return lower, upper


@dataclass
class SeedResult:
    seed: int
    stat: float  # max in-sample IC t over configurations passing every other gate (-inf if none)
    is_t: dict[str, float] = field(default_factory=dict)  # "signal/rebalance" -> in-sample gate t
    backtests: int = 0


def sweep_seed(seed: int, thresholds: ResearchThresholds | None = None) -> SeedResult:
    from ceap.data.historical import HistoricalGenerator
    from ceap.data.research_scenarios import noise_scenario

    th = thresholds or DEFAULT_THRESHOLDS
    ds = HistoricalGenerator().generate(noise_scenario(seed))
    configs = search_space()
    n_trials = len(configs)
    stats: dict[tuple[str, int], Any] = {}
    out = SeedResult(seed, float("-inf"))
    for cfg in configs:
        key = (cfg["signal"], cfg["rebalance_days"])
        if key not in stats:
            stats[key] = evaluate_signal(ds, cfg["signal"], rebalance_days=cfg["rebalance_days"])
            out.is_t[f"{key[0]}/{key[1]}"] = float(stats[key].period("in_sample").ic_t_stat)
        s = stats[key]
        is_t = s.period("in_sample").ic_t_stat
        oos_t = s.period("out_of_sample").ic_t_stat
        if not (math.isfinite(is_t) and is_t >= GRID_MIN and math.isfinite(oos_t) and oos_t >= th.oos_ic_t_min):
            continue
        if s.period("in_sample").n_dates < th.min_dates or is_t <= out.stat:
            continue
        bt = run_backtest(
            ds, cfg["signal"], rebalance_days=cfg["rebalance_days"], long_short=cfg["long_short"],
            weighting=cfg["weighting"], report_bounds=False,
        )
        out.backtests += 1
        ins, oos, full = bt.period("in_sample"), bt.period("out_of_sample"), bt.period("full")
        assert ins is not None and oos is not None and full is not None
        dsr, _, _ = alpha_deflated_sharpe(ins, {"n_trials": n_trials}, th)
        if not (math.isfinite(dsr) and dsr >= th.dsr_min):
            continue
        gross = full.gross_ann_return
        cost_share = (full.cost_drag_bps_annual / 10_000.0) / gross if gross and gross > 0 else float("inf")
        if not (math.isfinite(oos.sharpe) and oos.sharpe >= th.sharpe_min and cost_share < th.cost_share_max):
            continue
        out.stat = float(is_t)
    return out


def false_promote_curve(stats: list[float]) -> list[dict[str, float]]:
    n = len(stats)
    arr = np.array(stats, dtype=float)
    rows = []
    for h in np.round(np.arange(GRID_MIN, GRID_MAX + 1e-9, GRID_STEP), 2):
        k = int((arr >= h).sum())
        lo, hi = clopper_pearson(k, n)
        rows.append({"hurdle": float(h), "false_promotes": k, "rate": k / n if n else 0.0, "lower": lo, "upper": hi})
    return rows


def choose_hurdle(stats: list[float], target: float = TARGET_RATE) -> dict[str, float]:
    """Lowest grid hurdle whose Clopper-Pearson upper bound on the false-PROMOTE rate is <= ``target``."""
    for row in false_promote_curve(stats):
        if row["upper"] <= target:
            return row
    raise ValueError(f"no hurdle up to {GRID_MAX} keeps the false-PROMOTE upper bound at or below {target:.0%}")


def run_null_sweep(
    n_seeds: int, first_seed: int = 0, thresholds: ResearchThresholds | None = None, workers: int = 1
) -> dict[str, Any]:
    seeds = [first_seed + i for i in range(n_seeds)]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(sweep_seed, seeds, [thresholds] * n_seeds, chunksize=4))
    else:
        results = [sweep_seed(s, thresholds) for s in seeds]

    stats = [r.stat for r in results]
    per_seed = [
        {"seed": r.seed, "stat": None if not math.isfinite(r.stat) else round(r.stat, 6),
         "is_t": {k: round(v, 6) for k, v in sorted(r.is_t.items())}}
        for r in results
    ]
    th = thresholds or DEFAULT_THRESHOLDS
    gates = {"oos_ic_t_min": th.oos_ic_t_min, "sharpe_min": th.sharpe_min, "cost_share_max": th.cost_share_max,
             "dsr_min": th.dsr_min, "min_dates": th.min_dates}
    body = {"version": SWEEP_VERSION, "generator": GENERATOR_VERSION, "n_seeds": n_seeds, "first_seed": first_seed,
            "search_space": list(search_space()), "gates": gates, "per_seed": per_seed}
    sweep_hash = content_hash(body)
    curve = false_promote_curve(stats)
    try:
        chosen: dict[str, float] | None = choose_hurdle(stats)
    except ValueError:
        chosen = None
    # 2.7 null check on the gate statistic of every configuration family: share of |t| > 2
    all_t = np.array([v for r in results for v in r.is_t.values() if math.isfinite(v)])
    return {
        "sweep_hash": sweep_hash,
        "n_seeds": n_seeds,
        "first_seed": first_seed,
        "search_size": len(search_space()),
        "gates": gates,
        "chosen": chosen,
        "curve": curve,
        "abs_t_gt_2_rate": float((np.abs(all_t) > 2.0).mean()) if all_t.size else float("nan"),
        "abs_t_gt_2_by_family": {
            k: float(np.mean([abs(r.is_t[k]) > 2.0 for r in results])) for k in sorted(results[0].is_t)
        } if results else {},
        "backtests_run": sum(r.backtests for r in results),
        "per_seed": per_seed,
    }


def rate_at(summary: dict[str, Any], hurdle: float) -> dict[str, float]:
    """False-PROMOTE row of the sweep's curve at the first grid point at or above ``hurdle``."""
    return next(r for r in summary["curve"] if r["hurdle"] >= hurdle - 1e-9)


def quick_check(summary: dict[str, Any], n: int = 50) -> dict[str, Any]:
    """The first ``n`` seeds of a published per-seed vector (D20): a cheap reproduction target."""
    from ceap.analytics.research_assessment import DEFAULT_THRESHOLDS

    stats = [p["stat"] if p["stat"] is not None else float("-inf") for p in summary["per_seed"][:n]]
    row = next(r for r in false_promote_curve(stats) if r["hurdle"] >= DEFAULT_THRESHOLDS.ic_t_stat_min - 1e-9)
    return {"n_seeds": len(stats), "false_promotes": row["false_promotes"], "rate": row["rate"]}


def append_sweep_ledger(summary: dict[str, Any], reason: str, path: Path | None = None) -> Path:
    """Append one record per sweep run (D21): hash, generator version, seeds, the rate at the a-priori hurdle
    and why it was run. Append-only; a re-sweep never overwrites the earlier record."""
    from ceap.analytics.research_assessment import DEFAULT_THRESHOLDS

    target = path or SWEEP_LEDGER
    row = rate_at(summary, DEFAULT_THRESHOLDS.ic_t_stat_min)
    rec = {
        "sweep_hash": summary["sweep_hash"], "version": SWEEP_VERSION, "generator": GENERATOR_VERSION,
        "n_seeds": summary["n_seeds"], "first_seed": summary["first_seed"],
        "hurdle": DEFAULT_THRESHOLDS.ic_t_stat_min, "false_promotes": row["false_promotes"],
        "rate": round(row["rate"], 6), "reason": reason,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return target


def write_thresholds(summary: dict[str, Any], path: Path | None = None, per_seed_path: Path | None = None) -> Path:
    """Record the sweep that measured the false-PROMOTE rate at the a-priori hurdle (D3) and publish the
    per-seed vector (D20). The hurdle itself is not taken from the sweep."""
    from ceap.analytics.research_assessment import DEFAULT_THRESHOLDS, THRESHOLDS_PATH

    target = path or THRESHOLDS_PATH
    hurdle = DEFAULT_THRESHOLDS.ic_t_stat_min
    row = rate_at(summary, hurdle)
    doc = {
        "ic_t_stat_min": hurdle,
        "ic_t_single_test": DEFAULT_THRESHOLDS.ic_t_single_test,
        "sweep_hash": summary["sweep_hash"],
        "generator": GENERATOR_VERSION,
        "rule": "a-priori hurdle: t >= 3 for a result chosen from a search, t >= 1.645 for one preregistered "
        "test (Harvey, Liu and Zhu 2016); the sweep measures the false-PROMOTE rate of the full 24-configuration "
        "search on the pure-noise set at that hurdle",
        "n_seeds": summary["n_seeds"],
        "first_seed": summary["first_seed"],
        "false_promotes": row["false_promotes"],
        "rate": round(row["rate"], 6),
        "band95": [round(row["lower"], 6), round(row["upper"], 6)],
        "lowest_hurdle_with_upper_le_5pct": None if summary.get("chosen") is None else summary["chosen"]["hurdle"],
        "abs_t_gt_2_rate": round(summary["abs_t_gt_2_rate"], 6),
        "gates": summary["gates"],
        "quick_check_50": quick_check(summary, 50),
        "curve": [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()} for r in summary["curve"]
                  if r["hurdle"] <= hurdle + 0.5 and round(r["hurdle"] * 4) == r["hurdle"] * 4],
    }
    target.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    seeds_target = per_seed_path or PER_SEED_PATH
    seeds_target.parent.mkdir(parents=True, exist_ok=True)
    seeds_target.write_text(
        json.dumps({"sweep_hash": summary["sweep_hash"], "generator": GENERATOR_VERSION,
                    "per_seed": [{"seed": p["seed"], "stat": p["stat"]} for p in summary["per_seed"]]},
                   indent=0, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
