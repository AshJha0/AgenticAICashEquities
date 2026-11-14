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
``docs/research/adversarial_power.json``.

0.10.0 (Q4, finding R-20): :func:`full_verdict_study` runs a family through the **whole verdict**
(:func:`ceap.analytics.research_assessment.assess_research` on the signal statistics, the backtest and the
target portfolio's risk report, as the research flow builds them, with the 24-trial search counted) and
reports the share of seeds that end in ``PROMOTE``. ``python -m ceap.analytics.adversarial full [seeds]
[workers]`` writes ``docs/research/adversarial_full_verdict.json``. Evidence class: simulation (synthetic; says nothing about real
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
OUT_FULL = Path(__file__).resolve().parents[3] / "docs" / "research" / "adversarial_full_verdict.json"
FULL_VERDICT_FAMILIES: tuple[str, ...] = ("market_corr", "drift")


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


def full_verdict(ds: Any, signal: str = "momentum_12_1", rebalance_days: int = 21) -> dict[str, Any]:
    """The production verdict on ``ds`` for the research flow's default request: signal statistics,
    backtest (search top fraction, the 24-trial floor counted) and the target portfolio's risk report with
    its as-of and history limit checks and stress."""
    from ceap.analytics.backtest import simulate_backtest
    from ceap.analytics.portfolio_risk import check_limits, history_limit_checks, stress, target_portfolio
    from ceap.analytics.research_assessment import assess_research
    from ceap.analytics.signal_statistics import evaluate_signal
    from ceap.domain.research import DEFAULT_GROSS_NOTIONAL, SEARCH_TOP_FRACTION

    stats = evaluate_signal(ds, signal, rebalance_days=rebalance_days)
    run = simulate_backtest(ds, signal, rebalance_days=rebalance_days, top_fraction=SEARCH_TOP_FRACTION,
                            report_bounds=False)
    backtest = replace(run.result, trial={"n_trials": 1})  # floored at the 24 enumerated configurations
    report = target_portfolio(ds, signal, None, True, DEFAULT_GROSS_NOTIONAL)
    end = ds.index_at_or_before(report.as_of)
    risk = replace(
        report, checks=check_limits(report), stress=stress(report, -500.0, ds),
        history_checks=history_limit_checks(ds, signal, True, DEFAULT_GROSS_NOTIONAL, rebalance_days, end_index=end),
    )
    a = assess_research(stats, backtest, risk)
    # the same inputs with the factor-neutral statistic removed: what the verdict was before Q4
    plain = replace(stats, periods={k: replace(v, ic_t_stat_factor_neutral=float("nan"))
                                    for k, v in stats.periods.items()})
    b = assess_research(plain, backtest, risk)
    return {"verdict": a.verdict.value, "flags": [f.value for f in a.flags],
            "verdict_without_factor_neutral": b.verdict.value,
            "is_ic_t": a.metrics.get("is_ic_t_stat"), "is_ic_t_factor_neutral": a.metrics.get("is_ic_t_factor_neutral")}


def _full(job: tuple[str, float, int]) -> dict[str, Any]:
    from ceap.data.historical import HistoricalGenerator

    family, premium, seed = job
    return {"family": family, "premium_bps": premium, "seed": seed,
            **full_verdict(HistoricalGenerator().generate(family_spec(family, premium, seed)))}


def full_verdict_study(seeds: int = 16, workers: int = 1,
                       families: tuple[str, ...] = FULL_VERDICT_FAMILIES) -> dict[str, Any]:
    """PROMOTE rate of the full verdict per family and premium; for ``market_corr`` every PROMOTE is false."""
    jobs = [(f, p, s) for f in families for p in PREMIUMS_BPS for s in range(seeds)]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            runs = list(pool.map(_full, jobs, chunksize=2))
    else:
        runs = [_full(j) for j in jobs]
    rows = []
    for f in families:
        for p in PREMIUMS_BPS:
            sel = [r for r in runs if r["family"] == f and r["premium_bps"] == p]
            n = len(sel)
            promote = sum(r["verdict"] == "PROMOTE" for r in sel)
            rows.append({
                "family": f, "premium_bps": p, "seeds": n,
                "kind": "false_positive_rate" if (p == 0.0 or f in FALSE_POSITIVE_FAMILIES) else "power",
                "promote": promote, "promote_rate": round(promote / n, 4) if n else None,
                "promote_without_factor_neutral": sum(r["verdict_without_factor_neutral"] == "PROMOTE" for r in sel),
                "plain_ic_t_ge_3": sum(r["is_ic_t"] >= 3.0 for r in sel),
                "factor_neutral_ic_t_ge_3": sum(r["is_ic_t_factor_neutral"] >= 3.0 for r in sel),
                "verdicts": {v: sum(r["verdict"] == v for r in sel) for v in sorted({r["verdict"] for r in sel})},
            })
    return {
        "signal": "momentum_12_1", "rebalance_days": 21, "seeds_per_point": seeds,
        "verdict": "full production verdict (assess_research) with the 24-trial search counted",
        "note": "market_corr PROMOTEs are market exposure, so every one is a false positive",
        "rows": rows, "runs": runs,
    }


if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) > 1 and sys.argv[1] == "full":
        full = full_verdict_study(int(sys.argv[2]) if len(sys.argv) > 2 else 16,
                                  workers=int(sys.argv[3]) if len(sys.argv) > 3 else 4)
        OUT_FULL.write_text(json.dumps(full, indent=1, default=float) + "\n", encoding="utf-8")
        print(json.dumps(full["rows"], indent=0))
    else:
        doc = adversarial_study(int(sys.argv[1]) if len(sys.argv) > 1 else 8,
                                workers=int(sys.argv[2]) if len(sys.argv) > 2 else 4)
        OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(doc["rows"], indent=0))
