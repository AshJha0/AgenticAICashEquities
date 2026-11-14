"""Fit the parametric pre-trade cost model (2T.8) and write its model card.

    python scripts/train_pretrade.py            # fit, test under shift, write model + card
    python scripts/train_pretrade.py --check    # refit and fail if the committed model differs

Fit on five scenario families, conformal calibration on fresh seeds of the same five, coverage
tested on the other five families (shift). Deterministic: seeds are fixed below. No network, no
model calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import cache
from pathlib import Path

import numpy as np

from ceap.analytics.implementation_shortfall import DEFAULT_FEES, shortfall_components
from ceap.data.tca_universe import UniverseConfig, generate_tca_universe, generator_hash
from ceap.ml.pretrade_cost import MODEL_PATH, PretradeFeatures, PretradeModel, coverage, fit_pretrade

TRAIN_FAMILIES = ("T01", "T03", "T05", "T07", "T09")
TEST_FAMILIES = ("T02", "T04", "T06", "T08", "T10")
FIT_SEEDS = (101, 102)
CAL_SEEDS = (201, 202, 203)
TEST_SEEDS = (301, 302)
CARD_PATH = Path(__file__).resolve().parents[1] / "docs" / "quant" / "pretrade_model_card.md"


@cache
def rows_for(family: str, seed: int) -> list[tuple[PretradeFeatures, float]]:
    """(arrival-time features, realised arrival shortfall bps) for every filled parent."""
    uni = generate_tca_universe(seed, UniverseConfig(family=family))
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
        out.append((x, comps.arrival_bps))
    return out


def wilson(hits: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson 95 % interval for a binomial proportion (Wilson 1927); (nan, nan) when n = 0."""
    if n == 0:
        return float("nan"), float("nan")
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return float(centre - half), float(centre + half)


def challengers(fit: list[tuple[PretradeFeatures, float]]) -> dict[str, tuple[float, float]]:
    """Naive challengers fitted on the same rows: a constant, and intercept + slope on spread only."""
    y = np.array([v for _, v in fit])
    s = np.array([x.spread_bps for x, _ in fit])
    slope, intercept = np.polyfit(s, y, 1)
    return {"constant": (float(y.mean()), 0.0), "spread_only": (float(intercept), float(slope))}


def evaluate(model: PretradeModel, fit: list[tuple[PretradeFeatures, float]]) -> dict[str, dict[str, object]]:
    """Per shifted family: n, coverage with its Wilson interval, MAE and bias of model and challengers."""
    ch = challengers(fit)
    out: dict[str, dict[str, object]] = {}
    for f in TEST_FAMILIES:
        rows = [r for s in TEST_SEEDS for r in rows_for(f, s)]
        y = np.array([v for _, v in rows])
        preds = {"model": np.array([model.predict(x).estimate_bps for x, _ in rows])}
        for name, (a, b) in ch.items():
            preds[name] = np.array([a + b * x.spread_bps for x, _ in rows])
        hits = sum(int(model.predict(x).lower_bps <= v <= model.predict(x).upper_bps) for x, v in rows)
        lo, hi = wilson(hits, len(rows))
        out[f] = {
            "n": len(rows),
            "coverage": hits / len(rows) if rows else float("nan"),
            "coverage_ci95": [lo, hi],
            "nominal_inside_ci": bool(lo <= model.nominal <= hi),
            "mae": {k: float(np.mean(np.abs(y - p))) for k, p in preds.items()},
            "bias": {k: float(np.mean(p - y)) for k, p in preds.items()},
        }
    return out


def train() -> tuple[PretradeModel, dict[str, float]]:
    fit = [r for f in TRAIN_FAMILIES for s in FIT_SEEDS for r in rows_for(f, s)]
    cal = [r for f in TRAIN_FAMILIES for s in CAL_SEEDS for r in rows_for(f, s)]
    meta = {
        "train_families": list(TRAIN_FAMILIES),
        "test_families": list(TEST_FAMILIES),
        "fit_seeds": list(FIT_SEEDS),
        "calibration_seeds": list(CAL_SEEDS),
        "test_seeds": list(TEST_SEEDS),
        "n_fit": len(fit),
        "n_calibration": len(cal),
        "universe": UniverseConfig().as_dict(),
        "features": ["spread_bps at arrival", "sigma_daily_bps (reference)", "Q/ADV", "algo"],
        "target": "arrival shortfall bps (Perold IS minus delay leg, fees included)",
    }
    model = fit_pretrade(fit, cal, generator_hash(), meta=meta)
    cov = {f: coverage(model, [r for s in TEST_SEEDS for r in rows_for(f, s)]) for f in TEST_FAMILIES}
    cov_in = {f: coverage(model, [r for s in TEST_SEEDS for r in rows_for(f, s)]) for f in TRAIN_FAMILIES}
    model = PretradeModel(
        model.impact_coefficient,
        model.algo_terms,
        model.exec_noise_bps,
        model.conformal_q,
        model.nominal,
        model.generator_hash,
        {**meta, "coverage_shifted": cov, "coverage_in_family_fresh_seed": cov_in},
        model.price_risk_inflation,
    )
    return model, cov


def model_card(model: PretradeModel, ev: dict[str, dict[str, object]] | None = None) -> str:
    m = model.meta
    rows = "\n".join(f"| {a} | {c0:+.3f} | {c1:+.3f} |" for a, (c0, c1) in sorted(model.algo_terms.items()))
    cov = "\n".join(f"| {f} (shifted) | {v:.1%} |" for f, v in m["coverage_shifted"].items())
    cov_in = "\n".join(
        f"| {f} (fit family, fresh seed) | {v:.1%} |" for f, v in m["coverage_in_family_fresh_seed"].items()
    )
    return f"""# Model card: parametric pre-trade cost model

**{model.label}.** Generated by `scripts/train_pretrade.py`; do not edit by hand.

- **Form** (decision D11): `E[cost] = c0[algo] + c1[algo] * spread_bps + b * sigma_daily_bps * sqrt(Q/ADV)`,
  fitted by weighted least squares (weights 1/price-risk^2).
- **Target**: {m["target"]}.
- **Features** (arrival-time only): {", ".join(m["features"])}.
- **Interval**: split-conformal on normalised residuals, nominal {model.nominal:.0%};
  scale = sqrt(sigma^2 * V_schedule + s_exec^2) with V_schedule = T/3 for a linear schedule.
- **Data**: multi-day TCA universe ({m["universe"]["n_sessions"]} sessions x {m["universe"]["parents_per_session"]} parents);
  fit families {", ".join(m["train_families"])} (seeds {m["fit_seeds"]}, n = {m["n_fit"]}),
  calibration seeds {m["calibration_seeds"]} (n = {m["n_calibration"]}),
  shift test on families {", ".join(m["test_families"])} (seeds {m["test_seeds"]}).
- **Not for**: real orders. It describes a synthetic generator; on real data it would need refitting
  and its coverage re-tested.

## Coefficients

- impact coefficient b = {model.impact_coefficient:.4f} (generator permanent-impact gamma is 0.7; b also absorbs
  the realised share of the drift under each schedule, so they are not expected to be equal)
- execution noise s_exec = {model.exec_noise_bps:.3f} bps
- price-risk inflation kappa = {model.price_risk_inflation:.4f} (realised over analytic price variance, minus one)
- conformal quantile q = {model.conformal_q:.4f}

| algo | c0 (bps) | c1 (per bps of spread) |
|---|---|---|
{rows}

## Coverage at nominal {model.nominal:.0%} (acceptance: shifted families within [85 %, 95 %])

| family | coverage |
|---|---|
{cov}
{cov_in}
{_eval_tables(model, ev) if ev else ""}"""


def _eval_tables(model: PretradeModel, ev: dict[str, dict[str, object]]) -> str:
    """Point error against naive challengers and coverage with a binomial interval (0.10.0 X4, R-24)."""
    err = "\n".join(
        f"| {f} | {r['n']} | " + " | ".join(f"{r['mae'][k]:.2f} / {r['bias'][k]:+.2f}"  # type: ignore[index]
                                       for k in ("model", "spread_only", "constant")) + " |"
        for f, r in ev.items()
    )
    cov = "\n".join(
        f"| {f} | {r['coverage']:.1%} | [{r['coverage_ci95'][0]:.1%}, {r['coverage_ci95'][1]:.1%}] | "  # type: ignore[index]
        f"{'yes' if r['nominal_inside_ci'] else '**no**'} |"
        for f, r in ev.items()
    )
    failed = [f for f, r in ev.items() if not r["nominal_inside_ci"]]
    verdict = (
        f"**Fails** in {', '.join(failed)}: the 95 % interval excludes the nominal {model.nominal:.0%}, "
        "so the interval is too narrow under shift there. `scripts/train_pretrade.py` exits 1 until this is fixed."
        if failed else "Nominal coverage is inside every interval."
    )
    return f"""
## Point error against naive challengers (shifted families, MAE / bias in bps)

The challengers are fitted on the same rows: a constant (the mean cost) and an intercept plus slope
on the arrival spread. Bias is prediction minus realised.

| family | n | model | spread only | constant |
|---|---|---|---|---|
{err}

Reading: the model's MAE is within a few hundredths of a bps of the challengers in most families;
its gain is in bias where impact is large (T08, T10). Most of the cost variance is price risk that no
arrival-time feature predicts.

## Coverage against nominal with a Wilson 95 % interval

| family | coverage | 95 % interval | nominal inside |
|---|---|---|---|
{cov}

{verdict}

Still open (R-24): every family is drawn from the same generator, which plants the square-root form
the model fits; a test on a different functional form needs a generator family with another impact
law and is not done in this release.
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
    fit = [r for f in TRAIN_FAMILIES for s in FIT_SEEDS for r in rows_for(f, s)]
    ev = evaluate(model, fit)
    CARD_PATH.write_text(model_card(model, ev), encoding="utf-8")
    print(
        json.dumps({"coverage_shifted": cov, "q": model.conformal_q, "b": model.impact_coefficient}, indent=2)
    )
    bad = [f for f, v in cov.items() if not 0.85 <= v <= 0.95]
    excluded = [f for f, r in ev.items() if not r["nominal_inside_ci"]]
    if excluded:
        print(f"coverage interval excludes nominal in {excluded} (R-24)", file=sys.stderr)
    return 1 if bad or excluded else 0


if __name__ == "__main__":
    sys.exit(main())
