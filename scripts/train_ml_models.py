"""Fit and register the R2B models on synthetic data (PLAN 2B.1, 2B.2, 2B.4, 2B.5).

    python scripts/train_ml_models.py            # everything
    python scripts/train_ml_models.py detector   # one of: detector, calibration, combination, cards

Deterministic: fixed seeds, no wall-clock input except the card date (which is not hashed).
Writes ``data/models/*.json`` and ``docs/ml/model_cards.md`` plus ``docs/ml/reliability.svg``.
Calibration seeds, held-out seeds and evaluation seeds are disjoint:

* detector threshold: clean sessions 0..299, incidents 300..399 (x10) and 400..499 (x1.3);
* held-out detector checks (tests): clean 30_000..30_499, T06 20_000..20_199;
* confidence calibration: fit seeds 0..99, report seeds 100..199 (session seeds seed * 100 + j, so
  0..9907 and 10_000..19_907). The calibration fit sessions reuse detector-threshold sessions 0..407;
  that is harmless because the threshold is not fitted on calibration outcomes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.pop("ANTHROPIC_API_KEY", None)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from ceap.ml.registry import ModelRegistry, feature_reference, generator_hash  # noqa: E402
from ceap.util.canonical import content_hash  # noqa: E402

DOCS = ROOT / "docs" / "ml"
H_GRID = tuple(float(h) for h in np.round(np.arange(6.0, 20.01, 0.5), 2))


def train_detector(reg: ModelRegistry) -> None:
    from ceap.analytics.null_sweep import clopper_pearson
    from ceap.ml.anomaly import DETECTOR_NAME, ChangeDetector, synthetic_session, tune_threshold

    probe = ChangeDetector(h=float("inf"))
    clean = [probe.detect(synthetic_session(s)[0]).peak_statistic for s in range(300)]
    incidents = [synthetic_session(s, 10.0, deployment=True) for s in range(300, 400)]
    incidents += [synthetic_session(s, 1.3) for s in range(400, 500)]
    delays: dict[float, list[float]] = {}
    for h in H_GRID:
        det = ChangeDetector(h=h)
        out = []
        for mf, cp in incidents:
            first = det.detect(mf).first
            out.append(float("inf") if first is None else float(first.alarm_index + det.reference_minutes - cp))
        delays[h] = out
    choice = tune_threshold(clean, delays, H_GRID)
    det = ChangeDetector(h=choice.h)
    k = int((np.asarray(clean) > choice.h).sum())
    lo, hi = clopper_pearson(k, len(clean))
    ref_mf = synthetic_session(0)[0]
    refs = {n: feature_reference(v[np.isfinite(v)]) for n, v in ref_mf.values.items() if np.nanstd(v) > 0}
    m = reg.register(
        DETECTOR_NAME,
        "robust_z_cusum_pelt",
        det.params(),
        training_data_hash=content_hash({"clean": [0, 300], "incident_x10": [300, 400], "incident_x1.3": [400, 500], "generator": generator_hash()}),
        metrics={
            "h": choice.h,
            "calibration_sessions_clean": len(clean),
            "calibration_false_alarm_rate": choice.false_alarm_rate,
            "calibration_false_alarm_ci95": [lo, hi],
            "calibration_arl0_sessions": (1.0 / choice.false_alarm_rate) if choice.false_alarm_rate > 0 else None,
            "calibration_mean_delay_min": choice.mean_delay_min,
            "expected_cost_per_session_min": choice.expected_cost,
        },
        description=(
            "Robust z (median and MAD over a 60-minute reference) of seven per-minute technology and feed "
            "features, two-sided CUSUM with k = 1 and threshold h, PELT segments for the change picture. "
            "h minimises 30 analyst-minutes per false alarm against 10 per minute of incident delay "
            "(incident prior 5 %), subject to the 95 % Clopper-Pearson upper bound of the per-session "
            "false-alarm rate being at most 1/20 (ARL0 >= 20 sessions)."
        ),
        intended_use="Flag and time-stamp technology or market-data regime changes for the engineering and market agents.",
        limitations="Fitted to the synthetic generator's metric law only; a real feed needs its own reference and threshold.",
        feature_reference=refs,
    )
    print(f"detector h={choice.h} fa={choice.false_alarm_rate:.3f} delay={choice.mean_delay_min:.2f} -> {m.model_hash}")


def train_calibration(reg: ModelRegistry) -> None:
    from ceap.analytics.calibration import (
        CHANGE_POINT_KEY,
        EVAL_SEEDS,
        FIT_SEEDS,
        calibrate,
        calibration_model_name,
        change_point_samples,
    )

    pf, yf = change_point_samples(FIT_SEEDS)
    pe, ye = change_point_samples(EVAL_SEEDS)
    rep = calibrate(CHANGE_POINT_KEY, pf, yf, pe, ye)
    m = reg.register(
        calibration_model_name(CHANGE_POINT_KEY),
        "isotonic",
        rep.mapping.to_params(),
        training_data_hash=content_hash({"fit_seeds": [0, 100], "generator": generator_hash()}),
        metrics=rep.metrics(),
        description="Isotonic map from the change-point finding's raw confidence to the held-out frequency of it being correct.",
        intended_use="Confidence of the engineering agent's change-point finding.",
        limitations="Outcome definition is synthetic (planted regime change, onset within 5 minutes).",
        label_kind="calibrated",
    )
    _reliability_svg(rep)
    print(f"calibration shipped={rep.shipped} brier {rep.brier_raw:.4f}->{rep.brier_calibrated:.4f} -> {m.model_hash}")


def _reliability_svg(rep: object) -> None:
    from ceap.analytics.calibration import CalibrationReport

    assert isinstance(rep, CalibrationReport)
    w, h, pad = 360, 360, 40
    span = w - 2 * pad

    def xy(p: float, f: float) -> tuple[float, float]:
        return pad + p * span, h - pad - f * span

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="sans-serif" font-size="11">',
        f'<rect width="{w}" height="{h}" fill="white"/>',
        f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{pad}" stroke="#999" stroke-dasharray="4 3"/>',
        f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}" stroke="black"/>',
        f'<line x1="{pad}" y1="{h - pad}" x2="{pad}" y2="{pad}" stroke="black"/>',
        f'<text x="{w / 2}" y="{h - 8}" text-anchor="middle">mean predicted confidence (held-out seeds 100-199)</text>',
        f'<text x="12" y="{h / 2}" transform="rotate(-90 12 {h / 2})" text-anchor="middle">observed frequency correct</text>',
    ]
    for bins, colour in ((rep.bins_raw, "#d55e00"), (rep.bins_calibrated, "#0072b2")):
        for b in bins:
            x, y = xy(b["mean_p"], b["freq"])
            r = 2 + 6 * (b["n"] / max(1, rep.n_eval)) ** 0.5
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{colour}" fill-opacity="0.7"/>')
    parts += [
        f'<text x="{pad + 8}" y="{pad + 12}" fill="#d55e00">raw: Brier {rep.brier_raw:.4f}, ECE {rep.ece_raw:.4f}</text>',
        f'<text x="{pad + 8}" y="{pad + 26}" fill="#0072b2">calibrated: Brier {rep.brier_calibrated:.4f}, ECE {rep.ece_calibrated:.4f}</text>',
        "</svg>",
    ]
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "reliability.svg").write_text("\n".join(parts) + "\n", encoding="utf-8")
    rows = ["| set | bin | n | mean confidence | observed |", "|---|---|---|---|---|"]
    for name, bins in (("raw", rep.bins_raw), ("calibrated", rep.bins_calibrated)):
        rows += [f"| {name} | {b['lo']:.1f}-{b['hi']:.1f} | {b['n']} | {b['mean_p']:.4f} | {b['freq']:.4f} |" for b in bins]
    (DOCS / "reliability_table.md").write_text("\n".join(rows) + "\n", encoding="utf-8")


def train_combination(reg: ModelRegistry) -> None:
    from ceap.analytics.ml_signals import MODEL_VERSION, combine_signals
    from ceap.analytics.signals import dataset_signal
    from ceap.analytics.trial_ledger import TrialLedger
    from ceap.data.historical import HistoricalGenerator
    from ceap.data.research_scenarios import research_scenario_for

    ds = HistoricalGenerator().generate(research_scenario_for("momentum_premium"))
    res = combine_signals(ds, ledger=TrialLedger(path=None))
    refs = {}
    for name in res.coef:
        sig = dataset_signal(ds, name)[ds.start_index : ds.in_sample_end_index + 1]
        refs[name] = feature_reference(sig[np.isfinite(sig)])
    d = res.to_dict()
    m = reg.register(
        "ridge_combination",
        MODEL_VERSION,
        {"alpha": res.alpha, "coef": res.coef, "horizon": res.horizon, "folds": res.folds, "embargo": res.embargo},
        training_data_hash=ds.dataset_hash,
        metrics={
            "cv_sharpe_daily": d["cv_sharpe_daily"],
            "n_trials": res.n_trials,
            "oos_sharpe_ann": {k: v["sharpe_ann"] for k, v in d["out_of_sample"].items()},
            "oos_dsr": {k: v["dsr"] for k, v in d["out_of_sample"].items()},
            "promote": res.promote,
        },
        description=(
            "Ridge (no intercept) of the 21-day demeaned forward return on the three cross-sectional signal "
            "z-scores; penalty chosen by purged (21 days) and embargoed (5 days) walk-forward CV over five "
            "folds; every penalty and every single signal is a ledger trial."
        ),
        intended_use="Reference combination for the alpha agent on the momentum-premium synthetic set.",
        limitations="Gross of costs; constant weights between rebalances; synthetic premium only.",
        feature_reference=refs,
    )
    print(f"combination alpha={res.alpha} promote={res.promote} -> {m.model_hash}")


def write_cards(reg: ModelRegistry) -> None:
    cards = reg.list()
    body = [
        "# Model cards",
        "",
        "Generated by `scripts/train_ml_models.py` from `data/models/`. Every model is fitted to synthetic data only;",
        f"the current generator hash is `{generator_hash()}`.",
        "",
    ]
    body += [c.to_markdown() for c in cards]
    from ceap.ml.inventory import MODEL_INVENTORY

    body += [
        "## Model inventory",
        "",
        "Every model whose output reaches a person or an order (`ceap.ml.inventory`). Nothing has been validated",
        "independently yet; the validator column says so rather than naming a reviewer who does not exist.",
        "",
        "| Model | Kind | Tier | Status | Owner | Validator | Review (days) | Challenger |",
        "|---|---|---|---|---|---|---|---|",
    ]
    body += [
        f"| {e.name} | {e.kind} | {e.tier} | {e.status} | {e.owner} | {e.validator} | {e.review_cadence_days} | "
        f"{e.challenger} |"
        for e in MODEL_INVENTORY
    ]
    body.append("")
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "model_cards.md").write_text("\n".join(body), encoding="utf-8")


def main(argv: list[str]) -> None:
    reg = ModelRegistry()
    steps = argv or ["detector", "calibration", "combination", "cards"]
    for name, fn in (
        ("detector", train_detector),
        ("calibration", train_calibration),
        ("combination", train_combination),
        ("cards", write_cards),
    ):
        if name in steps:
            if name != "cards":
                for old in reg.root.glob(f"{_model_prefix(name)}-*.json"):
                    old.unlink()
            fn(reg)


def _model_prefix(step: str) -> str:
    return {"detector": "change_detector", "calibration": "calibration_engineering_change_point", "combination": "ridge_combination"}[step]


if __name__ == "__main__":
    main(sys.argv[1:])
