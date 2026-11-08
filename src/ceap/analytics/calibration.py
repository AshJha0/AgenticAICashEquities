"""Held-out confidence calibration (PLAN 2B.4).

The platform's finding confidences are heuristics (``agents.base.confidence_from_excess``). This
module fits an isotonic map from a raw confidence to the observed frequency of the finding being
correct, on calibration seeds 0 to 99, and reports Brier score and expected calibration error on
held-out seeds 100 to 199. The map is shipped (registered and applied by the agents) only if the
held-out calibrated Brier is no worse than the raw Brier; otherwise the item is recorded as not
shipped with the numbers, and raw confidences pass through unchanged.

Definitions, for predictions ``p_i`` in [0, 1] and outcomes ``y_i`` in {0, 1}, ``N`` of them:

* Brier ``= (1/N) sum (p_i - y_i)^2``;
* ECE over ``B`` equal-width bins ``I_b = [b/B, (b+1)/B)`` (the last bin closed):
  ``ECE = sum_b (n_b / N) |mean_{i in b} p_i - mean_{i in b} y_i|``;
* isotonic regression: the non-decreasing step function ``g`` minimising ``sum (g(p_i) - y_i)^2``
  (pool-adjacent-violators; scikit-learn ``IsotonicRegression``), applied between its knots by
  linear interpolation and clipped to the end knots outside them, exactly what scikit-learn's
  ``predict`` does.

The first calibrated quantity is the engineering agent's change-point finding: raw confidence from
the size of the CUSUM excursion; outcome = the session really had a technology regime change and
the reported onset is within :data:`ONSET_TOLERANCE_MIN` minutes of it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np

ECE_BINS = 10
FIT_SEEDS = range(0, 100)
EVAL_SEEDS = range(100, 200)
ONSET_TOLERANCE_MIN = 5
CHANGE_POINT_KEY = "engineering.change_point"
#: latency multipliers of the sessions generated per seed (1.0 = clean): mild to severe incidents
SESSION_MULTIPLIERS: tuple[float, ...] = (1.0, 1.0, 1.1, 1.2, 1.3, 1.5, 2.0, 10.0)


def brier(p: Sequence[float] | np.ndarray, y: Sequence[float] | np.ndarray) -> float:
    pa, ya = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    return float(np.mean((pa - ya) ** 2)) if pa.size else float("nan")


def reliability_bins(p: Sequence[float] | np.ndarray, y: Sequence[float] | np.ndarray, bins: int = ECE_BINS) -> list[dict[str, float]]:
    pa, ya = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    idx = np.minimum((pa * bins).astype(int), bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        if m.any():
            rows.append(
                {"lo": b / bins, "hi": (b + 1) / bins, "n": int(m.sum()), "mean_p": float(pa[m].mean()), "freq": float(ya[m].mean())}
            )
    return rows


def ece(p: Sequence[float] | np.ndarray, y: Sequence[float] | np.ndarray, bins: int = ECE_BINS) -> float:
    n = len(p)
    if n == 0:
        return float("nan")
    return float(sum(r["n"] / n * abs(r["mean_p"] - r["freq"]) for r in reliability_bins(p, y, bins)))


@dataclass(frozen=True)
class IsotonicMap:
    x: tuple[float, ...]
    y: tuple[float, ...]

    def __call__(self, p: float | np.ndarray) -> Any:
        out = np.interp(p, self.x, self.y)
        return float(out) if np.ndim(out) == 0 else out

    def to_params(self) -> dict[str, list[float]]:
        return {"x": list(self.x), "y": list(self.y)}


def fit_isotonic(p: Sequence[float] | np.ndarray, y: Sequence[float] | np.ndarray) -> IsotonicMap:
    from sklearn.isotonic import IsotonicRegression

    ir = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    ir.fit(np.asarray(p, dtype=float), np.asarray(y, dtype=float))
    return IsotonicMap(tuple(float(v) for v in ir.X_thresholds_), tuple(float(v) for v in ir.y_thresholds_))


@dataclass(frozen=True)
class CalibrationReport:
    key: str
    n_fit: int
    n_eval: int
    brier_raw: float
    brier_calibrated: float
    ece_raw: float
    ece_calibrated: float
    shipped: bool
    mapping: IsotonicMap
    bins_raw: list[dict[str, float]]
    bins_calibrated: list[dict[str, float]]

    def metrics(self) -> dict[str, Any]:
        return {
            "n_fit": self.n_fit,
            "n_eval": self.n_eval,
            "brier_raw_heldout": self.brier_raw,
            "brier_calibrated_heldout": self.brier_calibrated,
            "ece_raw_heldout": self.ece_raw,
            "ece_calibrated_heldout": self.ece_calibrated,
            "shipped": self.shipped,
        }


def calibrate(
    key: str,
    p_fit: Sequence[float],
    y_fit: Sequence[float],
    p_eval: Sequence[float],
    y_eval: Sequence[float],
) -> CalibrationReport:
    mapping = fit_isotonic(p_fit, y_fit)
    pc = mapping(np.asarray(p_eval, dtype=float))
    b_raw, b_cal = brier(p_eval, y_eval), brier(pc, y_eval)
    return CalibrationReport(
        key=key,
        n_fit=len(p_fit),
        n_eval=len(p_eval),
        brier_raw=b_raw,
        brier_calibrated=b_cal,
        ece_raw=ece(p_eval, y_eval),
        ece_calibrated=ece(pc, y_eval),
        shipped=bool(b_cal <= b_raw),
        mapping=mapping,
        bins_raw=reliability_bins(p_eval, y_eval),
        bins_calibrated=reliability_bins(pc, y_eval),
    )


# ------------------------------------------------------------------ the change-point dataset
def change_point_samples(seeds: Sequence[int] | range, detector: Any = None) -> tuple[list[float], list[float]]:
    """``(raw confidence, correct)`` for every technology change-point finding over ``seeds``.

    Per seed one session per entry of :data:`SESSION_MULTIPLIERS` (session seed ``seed * 100 + j``).
    A finding exists when the detector alarms on a technology feature; it is correct when the
    session had a planted regime change and the onset is within :data:`ONSET_TOLERANCE_MIN` minutes.
    """
    from ceap.ml.anomaly import TECHNOLOGY_FEATURES, alarm_confidence, load_detector, synthetic_session

    det = detector or load_detector()
    p, y = [], []
    for seed in seeds:
        for j, mult in enumerate(SESSION_MULTIPLIERS):
            mf, cp = synthetic_session(seed * 100 + j, latency_multiplier=mult)
            rep = det.detect(mf, only=TECHNOLOGY_FEATURES)
            first = rep.first
            if first is None:
                continue
            onset = first.onset_index + det.reference_minutes
            p.append(alarm_confidence(rep))
            y.append(float(mult > 1.0 and abs(onset - cp) <= ONSET_TOLERANCE_MIN))
    return p, y


# ------------------------------------------------------------------ runtime application
@lru_cache(maxsize=16)
def _registered(key: str) -> tuple[IsotonicMap | None, str | None, str | None]:
    from ceap.ml.registry import ModelRegistry

    try:
        m = ModelRegistry().load(calibration_model_name(key))
    except (LookupError, OSError):
        return None, None, None
    if not m.card.metrics.get("shipped"):
        return None, m.model_hash, m.card.label
    return IsotonicMap(tuple(m.params["x"]), tuple(m.params["y"])), m.model_hash, m.card.label


def calibration_model_name(key: str) -> str:
    return "calibration_" + key.replace(".", "_")


def apply_calibration(key: str, raw: float) -> float:
    """Calibrated confidence for ``key`` when a shipped map is registered, else ``raw`` unchanged."""
    mapping, _, _ = _registered(key)
    if mapping is None or not (isinstance(raw, int | float) and math.isfinite(raw)):
        return raw
    return float(mapping(float(raw)))


def calibration_info(key: str) -> dict[str, Any]:
    mapping, mhash, label = _registered(key)
    return {"key": key, "applied": mapping is not None, "model_hash": mhash, "label": label}
