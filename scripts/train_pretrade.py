"""Fit the parametric pre-trade cost model (2T.8) and write its model card.

    python scripts/train_pretrade.py            # fit, test under shift, write model + card
    python scripts/train_pretrade.py --check    # refit and fail if the committed model differs

Fit on five scenario families; conformal calibration on fresh seeds of the same five plus five
regime-stressed variants of T01, at the regime level (0.10.0 X4, R-24); coverage tested on the
other five families (shift) and on all five under a different impact law (functional-form shift).
Deterministic: seeds are fixed below. No network, no model calls. Takes more than ten minutes.

Exit 1 when a shifted family's coverage interval (Wilson at the design-effect sample size)
excludes nominal, or when coverage leaves [85 %, 95 %].
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np

from ceap.analytics.implementation_shortfall import DEFAULT_FEES, shortfall_components
from ceap.data.tca_universe import (
    LINEAR_LAW_CROSSOVER,
    UniverseConfig,
    generate_tca_universe,
    generator_hash,
)
from ceap.ml.pretrade_cost import (
    MODEL_PATH,
    PretradeFeatures,
    PretradeModel,
    conformal_quantile,
    coverage,
    fit_pretrade,
    group_conformal_quantile,
    normalised_scores,
)

TRAIN_FAMILIES = ("T01", "T03", "T05", "T07", "T09")
TEST_FAMILIES = ("T02", "T04", "T06", "T08", "T10")
FIT_SEEDS = (101, 102)
CAL_SEEDS = (201, 202, 203)
TEST_SEEDS = (301, 302)
#: calibration regimes: T01 with one generator axis stressed by a factor of two (depth halved).
#: Declared as a round grid over every regime axis, not chosen from the test families' results.
CAL_REGIME_BASE = "T01"
CAL_REGIMES: tuple[tuple[tuple[str, float], ...], ...] = (
    (("volatility_multiplier", 2.0),),
    (("spread_multiplier", 2.0),),
    (("depth_multiplier", 0.5),),
    (("order_size_multiplier", 2.0),),
    (("latency_multiplier", 2.0),),
)
LAW_SHIFT = "linear"
DIGITS = 6  # rounding of reported evaluation figures: no cross-platform float noise in the JSON
CARD_PATH = Path(__file__).resolve().parents[1] / "docs" / "quant" / "pretrade_model_card.md"

Row = tuple[PretradeFeatures, float]


@cache
def universe_rows(
    family: str, seed: int, law: str = "sqrt", regime: tuple[tuple[str, float], ...] = ()
) -> tuple[tuple[int, PretradeFeatures, float], ...]:
    """(first session ordinal, arrival-time features, realised arrival shortfall bps) per filled parent."""
    uni = generate_tca_universe(seed, UniverseConfig(family=family, impact_law=law, regime=regime))
    out = []
    for p in uni.parents:
        if not p.fill_qty.size:
            continue
        fees = np.array([DEFAULT_FEES.per_share(f) for f in p.fill_flags])
        comps = shortfall_components(
            p.side,
            p.decision_price,
            p.arrival_mid,
            p.fill_prices,
            p.fill_qty,
            p.fill_mids,
            float(p.quantity),
            p.final_mid,
            fees,
        )
        x = PretradeFeatures(
            p.arrival_spread_bps, p.sigma_daily_bps, p.quantity / p.adv_shares, p.algo, p.schedule
        )
        out.append((p.sessions[0].toordinal(), x, comps.arrival_bps))
    return tuple(out)


def rows_for(family: str, seed: int, law: str = "sqrt") -> list[Row]:
    return [(x, y) for _, x, y in universe_rows(family, seed, law)]


def regime_name(regime: tuple[tuple[str, float], ...]) -> str:
    return CAL_REGIME_BASE + "+" + ",".join(f"{k.removesuffix('_multiplier')}x{v:g}" for k, v in regime)


def wilson(hits: float, n: float, z: float = 1.959964) -> tuple[float, float]:
    """Wilson 95 % interval for a binomial proportion (Wilson 1927); (nan, nan) when n = 0."""
    if n <= 0:
        return float("nan"), float("nan")
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return float(centre - half), float(centre + half)


def design_effect(hit: np.ndarray, cluster: np.ndarray) -> float:
    """Kish design effect of a proportion under cluster sampling, floored at 1.

    Parents in one session share the market path (index, shocks, concurrent flow), so their
    hits are correlated. The cluster-robust variance of the ratio estimator over sessions,
    divided by the binomial variance, is the factor by which the rows overstate the information.
    """
    n, p = hit.size, float(hit.mean())
    if n == 0 or p in (0.0, 1.0):
        return 1.0
    ids, inv = np.unique(cluster, return_inverse=True)
    g = ids.size
    if g < 2:
        return 1.0
    h = np.bincount(inv, weights=hit)
    m = np.bincount(inv).astype(float)
    var = g / (g - 1) * float(np.sum((h - p * m) ** 2)) / n**2
    return max(1.0, var / (p * (1 - p) / n))


def challengers(fit: list[Row]) -> dict[str, tuple[float, float]]:
    """Naive challengers fitted on the same rows: a constant, and intercept + slope on spread only."""
    y = np.array([v for _, v in fit])
    s = np.array([x.spread_bps for x, _ in fit])
    slope, intercept = np.polyfit(s, y, 1)
    return {"constant": (float(y.mean()), 0.0), "spread_only": (float(intercept), float(slope))}


def evaluate_cell(
    model: PretradeModel, ch: dict[str, tuple[float, float]], family: str, law: str = "sqrt"
) -> dict[str, Any]:
    """n, coverage with its design-effect Wilson interval, MAE and bias of model and challengers."""
    rows = [(s, d, x, y) for s in TEST_SEEDS for d, x, y in universe_rows(family, s, law)]
    y = np.array([r[3] for r in rows])
    ests = [model.predict(r[2]) for r in rows]
    preds = {"model": np.array([e.estimate_bps for e in ests])}
    for name, (a, b) in ch.items():
        preds[name] = np.array([a + b * r[2].spread_bps for r in rows])
    hit = np.array([float(e.lower_bps <= v <= e.upper_bps) for e, v in zip(ests, y, strict=True)])
    cluster = np.array([s * 1_000_000 + d for s, d, _, _ in rows])
    deff = design_effect(hit, cluster)
    n_eff = hit.size / deff
    lo, hi = wilson(float(hit.mean()) * n_eff, n_eff)
    r = lambda v: round(float(v), DIGITS)  # noqa: E731
    return {
        "n": int(hit.size),
        "coverage": r(hit.mean()),
        "design_effect": r(deff),
        "n_effective": r(n_eff),
        "coverage_ci95": [r(lo), r(hi)],
        "coverage_ci95_iid": [r(v) for v in wilson(float(hit.sum()), float(hit.size))],
        "nominal_inside_ci": bool(lo <= model.nominal <= hi),
        "mae": {k: r(np.mean(np.abs(y - p))) for k, p in preds.items()},
        "bias": {k: r(np.mean(p - y)) for k, p in preds.items()},
    }


def train() -> tuple[PretradeModel, dict[str, float]]:
    fit = [r for f in TRAIN_FAMILIES for s in FIT_SEEDS for r in rows_for(f, s)]
    cal_groups: dict[str, list[Row]] = {
        f: [r for s in CAL_SEEDS for r in rows_for(f, s)] for f in TRAIN_FAMILIES
    }
    for reg in CAL_REGIMES:
        cal_groups[regime_name(reg)] = [
            (x, y) for s in CAL_SEEDS for _, x, y in universe_rows(CAL_REGIME_BASE, s, "sqrt", reg)
        ]
    cal = [r for f in TRAIN_FAMILIES for r in cal_groups[f]]
    meta: dict[str, Any] = {
        "train_families": list(TRAIN_FAMILIES),
        "test_families": list(TEST_FAMILIES),
        "fit_seeds": list(FIT_SEEDS),
        "calibration_seeds": list(CAL_SEEDS),
        "calibration_regimes": [regime_name(r) for r in CAL_REGIMES],
        "calibration_method": "two-layer conformal over regimes (rank ceil((K+1) * nominal) of K regime quantiles)",
        "test_seeds": list(TEST_SEEDS),
        "n_fit": len(fit),
        "n_calibration": sum(len(g) for g in cal_groups.values()),
        "universe": UniverseConfig().as_dict(),
        "features": ["spread_bps at arrival", "sigma_daily_bps (reference)", "Q/ADV", "algo"],
        "target": "arrival shortfall bps (Perold IS minus delay leg, fees included)",
    }
    # coefficients, kappa and s_exec as before; ``cal`` only gives the pooled q, replaced below
    pooled = fit_pretrade(fit, cal, generator_hash(), meta=meta)
    scores = {g: normalised_scores(pooled, rows) for g, rows in cal_groups.items()}
    group_q = {g: round(conformal_quantile(s, pooled.nominal), DIGITS) for g, s in scores.items()}
    q = group_conformal_quantile(list(scores.values()), pooled.nominal)
    model = PretradeModel(
        pooled.impact_coefficient,
        pooled.algo_terms,
        pooled.exec_noise_bps,
        q,
        pooled.nominal,
        pooled.generator_hash,
        dict(meta),
        pooled.price_risk_inflation,
    )
    cov = {f: coverage(model, [r for s in TEST_SEEDS for r in rows_for(f, s)]) for f in TEST_FAMILIES}
    cov_in = {f: coverage(model, [r for s in TEST_SEEDS for r in rows_for(f, s)]) for f in TRAIN_FAMILIES}
    ch = challengers(fit)
    meta.update(
        {
            "coverage_shifted": cov,
            "coverage_in_family_fresh_seed": cov_in,
            "pooled_conformal_q": round(pooled.conformal_q, DIGITS),
            "regime_conformal_q": group_q,
            "challengers": {k: [round(a, DIGITS), round(b, DIGITS)] for k, (a, b) in ch.items()},
            "shift_evaluation": {f: evaluate_cell(model, ch, f) for f in TEST_FAMILIES},
            "law_shift": {
                "law": LAW_SHIFT,
                "crossover_q_over_adv": LINEAR_LAW_CROSSOVER,
                "cells": {f: evaluate_cell(model, ch, f, LAW_SHIFT) for f in TEST_FAMILIES},
            },
        }
    )
    model = PretradeModel(
        model.impact_coefficient,
        model.algo_terms,
        model.exec_noise_bps,
        model.conformal_q,
        model.nominal,
        model.generator_hash,
        meta,
        model.price_risk_inflation,
    )
    return model, cov


def failures(model: PretradeModel) -> list[str]:
    """Shifted families whose coverage interval excludes nominal or whose coverage leaves [85 %, 95 %]."""
    ev = model.meta["shift_evaluation"]
    return [
        f
        for f, r in ev.items()
        if not r["nominal_inside_ci"] or not 0.85 <= model.meta["coverage_shifted"][f] <= 0.95
    ]


def _err_rows(cells: dict[str, dict[str, Any]]) -> str:
    return "\n".join(
        f"| {f} | {r['n']} | "
        + " | ".join(f"{r['mae'][k]:.2f} / {r['bias'][k]:+.2f}" for k in ("model", "spread_only", "constant"))
        + " |"
        for f, r in cells.items()
    )


def _cov_rows(cells: dict[str, dict[str, Any]]) -> str:
    return "\n".join(
        f"| {f} | {r['coverage']:.1%} | {r['design_effect']:.2f} | {r['n_effective']:.0f} | "
        f"[{r['coverage_ci95'][0]:.1%}, {r['coverage_ci95'][1]:.1%}] | "
        f"[{r['coverage_ci95_iid'][0]:.1%}, {r['coverage_ci95_iid'][1]:.1%}] | "
        f"{'yes' if r['nominal_inside_ci'] else '**no**'} |"
        for f, r in cells.items()
    )


def model_card(model: PretradeModel) -> str:
    m = model.meta
    rows = "\n".join(f"| {a} | {c0:+.3f} | {c1:+.3f} |" for a, (c0, c1) in sorted(model.algo_terms.items()))
    cov_in = "\n".join(f"| {f} | {v:.1%} |" for f, v in m["coverage_in_family_fresh_seed"].items())
    group_q = "\n".join(f"| {g} | {q:.4f} |" for g, q in m["regime_conformal_q"].items())
    ev, law = m["shift_evaluation"], m["law_shift"]
    failed = failures(model)
    verdict = (
        f"**Fails** in {', '.join(failed)}: the interval excludes nominal {model.nominal:.0%} (or coverage "
        "is outside [85 %, 95 %]). `scripts/train_pretrade.py` exits 1."
        if failed
        else f"Nominal {model.nominal:.0%} is inside every shifted family's interval; the script exits 0."
    )
    law_cov = [r["coverage"] for r in law["cells"].values()]
    law_bias = [r["bias"]["model"] for r in law["cells"].values()]
    k = len(m["regime_conformal_q"])
    return f"""# Model card: parametric pre-trade cost model

**{model.label}.** Generated by `scripts/train_pretrade.py`; do not edit by hand.

- **Form** (decision D11): `E[cost] = c0[algo] + c1[algo] * spread_bps + b * sigma_daily_bps * sqrt(Q/ADV)`,
  fitted by weighted least squares (weights 1/price-risk^2).
- **Target**: {m["target"]}.
- **Features** (arrival-time only): {", ".join(m["features"])}.
- **Interval**: conformal on normalised residuals, nominal {model.nominal:.0%};
  scale = sqrt((1 + kappa) * sigma^2 * V_schedule + s_exec^2) with V_schedule = T/3 for a linear
  schedule; the quantile q is calibrated at the regime level (below).
- **Data**: multi-day TCA universe ({m["universe"]["n_sessions"]} sessions x {m["universe"]["parents_per_session"]} parents);
  fit families {", ".join(m["train_families"])} (seeds {m["fit_seeds"]}, n = {m["n_fit"]});
  calibration seeds {m["calibration_seeds"]} of the fit families and of {len(m["calibration_regimes"])} stressed
  regimes (n = {m["n_calibration"]}); shift test on families {", ".join(m["test_families"])} (seeds {m["test_seeds"]}),
  and on the same families under a {law["law"]} impact law.
- **Not for**: real orders. It describes a synthetic generator; on real data it would need refitting
  and its coverage re-tested.

## Coefficients

- impact coefficient b = {model.impact_coefficient:.4f} (generator permanent-impact gamma is 0.7; b also absorbs
  the realised share of the drift under each schedule, so they are not expected to be equal)
- execution noise s_exec = {model.exec_noise_bps:.3f} bps
- price-risk inflation kappa = {model.price_risk_inflation:.4f} (realised over analytic price variance, minus one)
- conformal quantile q = {model.conformal_q:.4f} (pooled split-conformal would give {m["pooled_conformal_q"]:.4f})

| algo | c0 (bps) | c1 (per bps of spread) |
|---|---|---|
{rows}

## Why the quantile is calibrated per regime (0.10.0 X4, finding R-24)

Pooled split-conformal guarantees nominal coverage only for new orders exchangeable with the
calibration orders, that is, drawn from the same mixture of regimes. A shifted family is a new
regime, not a new order, so that guarantee does not apply to it, and in 0.9.0 two of the five
shifted families were significantly under nominal.

The repair moves the exchangeability assumption up one level. Each calibration regime g (the five
fit families and five stressed variants of {CAL_REGIME_BASE}, each with one generator axis doubled or depth
halved) has its own conformal quantile q_g, the score level that gives nominal coverage inside that
regime. If a new regime is exchangeable with the K = {k} calibration regimes, its own quantile is
exchangeable with the q_g, and the ceil((K + 1) * {model.nominal:.2f})-th smallest q_g bounds it with probability
at least {model.nominal:.0%} (the split-conformal rank argument applied to regimes; Dunn, Wasserman and
Ramdas 2022, two-layer hierarchical conformal). With K = {k} that rank is the largest q_g. The stressed
regimes are a round grid over every axis the generator exposes, declared before the test families
were scored; the test families take no part in choosing q.

| calibration regime | q_g |
|---|---|
{group_q}

What this does not buy: the guarantee is for regimes like the calibration regimes. A regime far
outside them (an order-size multiplier of ten against two) can still be under-covered; the table
below is the check.

## Coverage against nominal under regime shift

Parents in one session share the market path, so their hits are correlated and the row count
overstates the information. The test uses a Wilson 95 % interval at the effective sample size
n / deff, where deff is the Kish design effect estimated from session clusters in that cell; the
row-independent Wilson interval is shown for comparison. Coverage must also stay in [85 %, 95 %].

| family | coverage | deff | n eff | 95 % interval (deff) | 95 % interval (iid) | nominal inside |
|---|---|---|---|---|---|---|
{_cov_rows(ev)}

{verdict}

Fit families on fresh seeds (not a shift test):

| family | coverage |
|---|---|
{cov_in}

## Point error against naive challengers (shifted families, MAE / bias in bps)

The challengers are fitted on the fit rows: a constant (the mean cost) and an intercept plus slope
on the arrival spread. Bias is prediction minus realised.

| family | n | model | spread only | constant |
|---|---|---|---|---|
{_err_rows(ev)}

Most of the cost variance is price risk that no arrival-time feature predicts, so MAE differs little
between the three; the model's gain is in bias where impact is large.

## Functional-form shift: a {law["law"]} impact law

Every family above is drawn from a generator whose permanent impact is gamma * sigma * sqrt(Q/ADV),
the form the model fits. Here the same five shifted families are drawn with impact
gamma * sigma * sqrt(x0) * (Q/ADV) / x0, x0 = {law["crossover_q_over_adv"]:.0%} of ADV (the Kyle / Almgren-Chriss linear law,
equal to the square-root law at x0). The model is not refitted. This is a report, not a gate: the
square-root model is wrong on this generator by construction.

| family | coverage | deff | n eff | 95 % interval (deff) | 95 % interval (iid) | nominal inside |
|---|---|---|---|---|---|---|
{_cov_rows(law["cells"])}

| family | n | model | spread only | constant |
|---|---|---|---|---|
{_err_rows(law["cells"])}

Coverage ranges from {min(law_cov):.1%} to {max(law_cov):.1%} and the model's bias from {min(law_bias):+.2f} to
{max(law_bias):+.2f} bps across the five families (bias is prediction minus realised: negative means the model
under-predicts cost).
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    model, cov = train()
    if args.check:
        committed = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        fresh = json.loads(json.dumps(model.to_dict()))
        same = json.dumps(committed, sort_keys=True) == json.dumps(fresh, sort_keys=True)
        print("committed model matches a refit" if same else "committed model differs from a refit")
        return 0 if same else 1
    model.save()
    CARD_PATH.write_text(model_card(model), encoding="utf-8")
    print(
        json.dumps({"coverage_shifted": cov, "q": model.conformal_q, "b": model.impact_coefficient}, indent=2)
    )
    bad = failures(model)
    if bad:
        print(f"shifted coverage fails in {bad} (R-24)", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
