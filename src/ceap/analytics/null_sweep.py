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
from ceap.analytics.research_assessment import DEFAULT_THRESHOLDS, ResearchThresholds, deflated_sharpe
from ceap.analytics.signal_statistics import evaluate_signal
from ceap.domain.research import search_space
from ceap.util.canonical import content_hash

TARGET_RATE = 0.05
GRID_STEP = 0.05
GRID_MIN = 0.0
GRID_MAX = 6.0
SWEEP_VERSION = "null-sweep-v1"


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
        dsr, _ = deflated_sharpe(ins.gross_sharpe, ins.n_days, ins.skew, ins.kurtosis, n_trials, None)
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
    body = {"version": SWEEP_VERSION, "generator": "realistic_v1", "n_seeds": n_seeds, "first_seed": first_seed,
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


def write_thresholds(summary: dict[str, Any], path: Path | None = None) -> Path:
    from ceap.analytics.research_assessment import THRESHOLDS_PATH

    target = path or THRESHOLDS_PATH
    chosen = summary["chosen"]
    if chosen is None:
        raise ValueError("the sweep found no admissible hurdle")
    doc = {
        "ic_t_stat_min": chosen["hurdle"],
        "sweep_hash": summary["sweep_hash"],
        "rule": "lowest t on a 0.05 grid whose Clopper-Pearson 95% upper bound on the false-PROMOTE rate is <= 5% "
        "over the full 24-configuration search on the pure-noise set",
        "n_seeds": summary["n_seeds"],
        "first_seed": summary["first_seed"],
        "false_promotes": chosen["false_promotes"],
        "rate": round(chosen["rate"], 6),
        "band95": [round(chosen["lower"], 6), round(chosen["upper"], 6)],
        "abs_t_gt_2_rate": round(summary["abs_t_gt_2_rate"], 6),
        "gates": summary["gates"],
        "curve": [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in row.items()} for row in summary["curve"]
                  if row["hurdle"] <= chosen["hurdle"] + 1.0],
    }
    target.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
