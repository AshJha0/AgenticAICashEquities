"""Power curve (2.9, Q5): detection rate of the IC part of the PROMOTE gate against the planted premium.

For each premium on a grid the momentum drift scenario is generated on ``seeds`` seeds and the share
of seeds whose in-sample non-overlapping IC t clears the hurdle (and whose out-of-sample IC t clears
``oos_ic_t_min``) is recorded next to the closed-form prediction ``Phi(E[t] - hurdle)`` from
:mod:`ceap.data.ground_truth`. Since 0.10.0 (Q5, review finding R-19) the curve is computed at both
hurdles in force: the single-test hurdle 1.645 (one preregistered test, one-sided 5 %) and the mined
hurdle 3.0 for a result chosen from a search (Harvey, Liu and Zhu 2016, "... and the Cross-Section of
Expected Returns", RFS 29(1), recommend t above 3 for new factors). The 0.9.0 file was at hurdle 0.40,
which is no longer used by the gate.

``detectable_premium_bps`` is the smallest grid premium whose *simulated* detection rate is at least
80 % (the conventional power target, Cohen 1988). Run ``python -m ceap.analytics.power`` to regenerate
``docs/quant_power_curve.json``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from statistics import NormalDist
from typing import Any

PREMIUMS_BPS: tuple[float, ...] = (0.0, 600.0, 900.0, 1200.0, 1500.0, 1800.0, 2100.0, 2400.0)
POWER_TARGET = 0.8
OUT = Path(__file__).resolve().parents[3] / "docs" / "quant_power_curve.json"


def _one(args: tuple[float, int]) -> tuple[float, float]:
    from ceap.analytics.signal_statistics import evaluate_signal
    from ceap.data.historical import HistoricalGenerator
    from ceap.data.research_scenarios import RESEARCH_TEMPLATES_RAW

    premium, seed = args
    base = RESEARCH_TEMPLATES_RAW[0]
    spec = replace(base, id=f"P{int(premium)}_{seed}", seed=50_000 + seed, premium_bps=premium,
                   decay_half_life_years=0.0, mechanism="drift" if premium > 0 else "none")
    ds = HistoricalGenerator().generate(spec)
    stats = evaluate_signal(ds, "momentum_12_1", rebalance_days=21)
    ins, oos = stats.period("in_sample"), stats.period("out_of_sample")
    assert ins is not None and oos is not None
    return float(ins.ic_t_stat), float(oos.ic_t_stat)


def _key(hurdle: float) -> str:
    return f"{hurdle:g}"


def power_curve(seeds: int = 40, workers: int = 1) -> dict[str, Any]:
    from ceap.analytics.research_assessment import DEFAULT_THRESHOLDS
    from ceap.data.ground_truth import expected_statistics
    from ceap.data.research_scenarios import RESEARCH_TEMPLATES_RAW

    hurdles = (DEFAULT_THRESHOLDS.ic_t_single_test, DEFAULT_THRESHOLDS.ic_t_stat_min)
    oos_min = DEFAULT_THRESHOLDS.oos_ic_t_min
    jobs = [(p, s) for p in PREMIUMS_BPS for s in range(seeds)]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            ts = list(pool.map(_one, jobs, chunksize=4))
    else:
        ts = [_one(j) for j in jobs]
    rows = []
    base = RESEARCH_TEMPLATES_RAW[0]
    nd = NormalDist()
    for i, p in enumerate(PREMIUMS_BPS):
        sample = ts[i * seeds : (i + 1) * seeds]
        spec = replace(base, premium_bps=p, decay_half_life_years=0.0, mechanism="drift" if p > 0 else "none")
        e = expected_statistics(spec)
        rows.append({
            "premium_bps": p,
            "expected_t_is": round(e["t_is"], 4),
            "expected_t_oos": round(e["t_oos"], 4),
            "expected_gross_sharpe": round(e["gross_sharpe_is"], 4),
            "mean_t_is": round(sum(a for a, _ in sample) / seeds, 4),
            "predicted_detection": {
                _key(h): round(nd.cdf(e["t_is"] - h) * nd.cdf(e["t_oos"] - oos_min), 4) for h in hurdles},
            "detection_rate": {
                _key(h): sum(a >= h and b >= oos_min for a, b in sample) / seeds for h in hurdles},
        })
    detectable: dict[str, dict[str, float] | None] = {}
    for h in hurdles:
        hit = next((r for r in rows if r["detection_rate"][_key(h)] >= POWER_TARGET), None)
        detectable[_key(h)] = None if hit is None else {
            "premium_bps": hit["premium_bps"], "expected_gross_sharpe": hit["expected_gross_sharpe"]}
    return {"signal": "momentum_12_1", "rebalance_days": 21, "universe": "30-name research universe",
            "hurdles": [float(h) for h in hurdles],
            "hurdle_roles": {_key(hurdles[0]): "single preregistered test", _key(hurdles[1]): "result mined from a search"},
            "oos_ic_t_min": oos_min,
            "detection": "in-sample IC t >= hurdle and out-of-sample IC t >= oos_ic_t_min (the IC part of the gate)",
            "power_target": POWER_TARGET, "detectable_premium_bps": detectable,
            "evidence_class": "simulation on the synthetic generator; not a statement about real markets",
            "hurdle_sweep_hash": DEFAULT_THRESHOLDS.hurdle_sweep_hash, "seeds_per_point": seeds, "rows": rows}


if __name__ == "__main__":  # pragma: no cover
    doc = power_curve(int(sys.argv[1]) if len(sys.argv) > 1 else 40, workers=4)
    OUT.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(doc["detectable_premium_bps"], indent=1))
