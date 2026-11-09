"""Adversarial power and false-positive curves across generator families (0.10 D2, D3).

A pass count over a handful of scenarios says little: the scenarios were written by the same people who
wrote the detector, and a premium four times a real one is detected by almost anything. This study instead
reports, for several generator families written without reference to the signal code, the **detection
rate as a function of the planted premium**, with the premium-zero point giving the false-positive rate of
that family.

Families (generator parameters in :class:`ceap.data.research_scenarios.ResearchScenarioSpec`):

* ``drift`` - the baseline latent AR(1) drift that 12-1 momentum is meant to find.
* ``hidden_factor`` - the premium sits on a latent factor with its own volatility (correlated noise).
* ``regime_switch`` - the drift's sign follows a two-state Markov chain (+1, -0.5).
* ``garch_jumps`` - the baseline drift plus Poisson idiosyncratic jumps (fat tails beyond Student-t).
* ``market_corr`` - expected return planted on market beta: a detection here is market exposure, not alpha,
  so every detection in this family counts as a false positive.

Detection is the IC part of the PROMOTE gate (in-sample non-overlapping IC t at or above the hurdle and
out-of-sample IC t at or above ``oos_ic_t_min``), at both a-priori hurdles (1.645 single test, 3.0 mined).
Run ``python -m ceap.analytics.adversarial [seeds] [workers]`` to regenerate
``docs/research/adversarial_power.json``. Evidence class: simulation (synthetic; says nothing about real
markets beyond the calibration in D1).
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

PREMIUMS_BPS: tuple[float, ...] = (0.0, 600.0, 1100.0, 2400.0)
FAMILIES: tuple[str, ...] = ("drift", "hidden_factor", "regime_switch", "garch_jumps", "market_corr")
FALSE_POSITIVE_FAMILIES = frozenset({"market_corr"})
OUT = Path(__file__).resolve().parents[3] / "docs" / "research" / "adversarial_power.json"


def family_spec(family: str, premium: float, seed: int) -> Any:
    """The scenario spec for one family, premium and seed (momentum 12-1, monthly)."""
    from ceap.data.research_scenarios import RESEARCH_TEMPLATES_RAW

    base = replace(RESEARCH_TEMPLATES_RAW[0], id=f"A-{family}-{int(premium)}-{seed}", seed=70_000 + seed,
                   premium_bps=0.0, mechanism="none", decay_half_life_years=0.0)
    if family == "drift":
        return replace(base, mechanism="drift" if premium > 0 else "none", premium_bps=premium)
    if family == "hidden_factor":
        return replace(base, hidden_factor_bps=premium)
    if family == "regime_switch":
        return replace(base, regime_switch=True, premium_bps=premium)
    if family == "garch_jumps":
        return replace(base, mechanism="drift" if premium > 0 else "none", premium_bps=premium, jump_intensity=4.0)
    if family == "market_corr":
        return replace(base, market_corr_premium_bps=premium)
    raise ValueError(f"unknown family {family!r}")


def _one(job: tuple[str, float, int]) -> tuple[float, float]:
    from ceap.analytics.signal_statistics import evaluate_signal
    from ceap.data.historical import HistoricalGenerator

    family, premium, seed = job
    ds = HistoricalGenerator().generate(family_spec(family, premium, seed))
    st = evaluate_signal(ds, "momentum_12_1", rebalance_days=21)
    ins, oos = st.period("in_sample"), st.period("out_of_sample")
    assert ins is not None and oos is not None
    return float(ins.ic_t_stat), float(oos.ic_t_stat)


def summarise(family: str, premium: float, ts: list[tuple[float, float]], oos_min: float) -> dict[str, Any]:
    from ceap.analytics.research_assessment import MINED_HURDLE, SINGLE_TEST_HURDLE

    n = len(ts)

    def rate(h: float) -> float:
        return sum(a >= h and b >= oos_min for a, b in ts) / n if n else float("nan")

    false_positive = premium == 0.0 or family in FALSE_POSITIVE_FAMILIES
    return {
        "family": family, "premium_bps": premium, "seeds": n,
        "kind": "false_positive_rate" if false_positive else "power",
        "rate_single_test": round(rate(SINGLE_TEST_HURDLE), 4), "rate_mined": round(rate(MINED_HURDLE), 4),
        "mean_t_is": round(sum(a for a, _ in ts) / n, 4) if n else None,
    }


def adversarial_study(seeds: int = 8, workers: int = 1) -> dict[str, Any]:
    from ceap.analytics.research_assessment import DEFAULT_THRESHOLDS, MINED_HURDLE, SINGLE_TEST_HURDLE

    jobs = [(f, p, s) for f in FAMILIES for p in PREMIUMS_BPS for s in range(seeds)]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            ts = list(pool.map(_one, jobs, chunksize=2))
    else:
        ts = [_one(j) for j in jobs]
    rows = []
    k = 0
    for f in FAMILIES:
        for p in PREMIUMS_BPS:
            rows.append(summarise(f, p, ts[k : k + seeds], DEFAULT_THRESHOLDS.oos_ic_t_min))
            k += seeds
    return {
        "signal": "momentum_12_1", "rebalance_days": 21, "hurdles": [SINGLE_TEST_HURDLE, MINED_HURDLE],
        "oos_ic_t_min": DEFAULT_THRESHOLDS.oos_ic_t_min, "seeds_per_point": seeds,
        "detection": "in-sample IC t >= hurdle and out-of-sample IC t >= oos_ic_t_min",
        "note": "market_corr detections are market exposure, so they count as false positives at every premium",
        "rows": rows,
    }


if __name__ == "__main__":  # pragma: no cover
    doc = adversarial_study(int(sys.argv[1]) if len(sys.argv) > 1 else 8,
                            workers=int(sys.argv[2]) if len(sys.argv) > 2 else 4)
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(doc["rows"], indent=0))
