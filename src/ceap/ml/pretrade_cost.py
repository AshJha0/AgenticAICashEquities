"""Parametric pre-trade cost model with conformal intervals (2T.8, decision D11).

Expected shortfall from arrival, in bps, for a parent with arrival-time features only::

    E[cost] = c0[algo] + c1[algo] * spread_bps + b * sigma_daily_bps * sqrt(Q / ADV)

``spread_bps`` is the quoted spread at arrival, ``sigma_daily_bps`` the reference daily volatility
and ``Q / ADV`` the parent's fraction of average daily volume. The algo terms carry the
passive/aggressive mix and fees; ``b`` is the square-root impact coefficient. No gradient boosting:
on synthetic data a flexible learner memorises the generator, while this form is interpretable and
its coverage can be tested under shift (fit on five scenario families, test on the other five).

Intervals are split-conformal on *normalised* residuals::

    score = |y - E[cost]| / scale,   scale = sqrt((1 + kappa) * sigma_daily^2 * V_schedule + s_exec^2)

where ``V_schedule`` is the price-risk multiplier of the parent's schedule (``T/3`` for a linear
schedule, see :func:`ceap.analytics.implementation_shortfall.schedule_price_variance`), ``kappa``
the fitted excess of realised over analytic price variance (concurrent flow's impact moves the
price too) and ``s_exec`` the fitted non-price dispersion; both are median-matched, so rare large
events are left to the conformal quantile. The interval is ``E[cost] +/- q * scale`` with ``q``
the conformal quantile of the calibration scores at the nominal level.

Every model carries ``label = "fitted to synthetic generator <hash>"``: it describes the synthetic
generator, not any real market.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from ceap.analytics.implementation_shortfall import schedule_price_variance

MODEL_PATH = Path(__file__).with_name("pretrade_model.json")
ALGOS = ("VWAP", "POV", "IS", "MOC")
NOMINAL_COVERAGE = 0.90


@dataclass(frozen=True)
class PretradeFeatures:
    spread_bps: float
    sigma_daily_bps: float
    q_over_adv: float
    algo: str = "VWAP"
    #: price-risk schedule segments ``(duration_days, a, b)``; default one linear hour
    schedule: tuple[tuple[float, float, float], ...] = ((1.0 / 6.5, 1.0, 0.0),)

    @property
    def impact_term(self) -> float:
        return float(self.sigma_daily_bps * math.sqrt(max(self.q_over_adv, 0.0)))

    @property
    def price_risk_bps(self) -> float:
        return float(self.sigma_daily_bps * math.sqrt(schedule_price_variance(self.schedule)))


@dataclass(frozen=True)
class PretradeEstimate:
    estimate_bps: float
    impact_bps: float
    scale_bps: float
    price_risk_bps: float
    lower_bps: float
    upper_bps: float

    def as_dict(self) -> dict[str, float]:
        return {
            "estimate_bps": self.estimate_bps,
            "impact_bps": self.impact_bps,
            "scale_bps": self.scale_bps,
            "price_risk_bps": self.price_risk_bps,
            "lower_bps": self.lower_bps,
            "upper_bps": self.upper_bps,
        }


@dataclass(frozen=True)
class PretradeModel:
    impact_coefficient: float
    algo_terms: dict[str, tuple[float, float]]  # algo -> (intercept, spread coefficient)
    exec_noise_bps: float
    conformal_q: float
    nominal: float = NOMINAL_COVERAGE
    generator_hash: str = "unfitted"
    meta: dict[str, Any] = field(default_factory=dict)
    price_risk_inflation: float = 0.0  # kappa

    @property
    def label(self) -> str:
        return f"fitted to synthetic generator {self.generator_hash}"

    def predict(self, x: PretradeFeatures) -> PretradeEstimate:
        c0, c1 = self.algo_terms.get(x.algo, self.algo_terms.get("VWAP", (0.0, 0.5)))
        impact = self.impact_coefficient * x.impact_term
        est = c0 + c1 * x.spread_bps + impact
        price_risk = x.price_risk_bps
        scale = math.sqrt((1.0 + self.price_risk_inflation) * price_risk**2 + self.exec_noise_bps**2)
        half = self.conformal_q * scale
        return PretradeEstimate(est, impact, scale, price_risk, est - half, est + half)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": "parametric_sqrt_impact_conformal_v1",
            "label": self.label,
            "generator_hash": self.generator_hash,
            "impact_coefficient": self.impact_coefficient,
            "algo_terms": {k: list(v) for k, v in sorted(self.algo_terms.items())},
            "exec_noise_bps": self.exec_noise_bps,
            "price_risk_inflation": self.price_risk_inflation,
            "conformal_q": self.conformal_q,
            "nominal": self.nominal,
            "meta": self.meta,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> PretradeModel:
        return PretradeModel(
            impact_coefficient=float(d["impact_coefficient"]),
            algo_terms={k: (float(v[0]), float(v[1])) for k, v in d["algo_terms"].items()},
            exec_noise_bps=float(d["exec_noise_bps"]),
            conformal_q=float(d["conformal_q"]),
            nominal=float(d.get("nominal", NOMINAL_COVERAGE)),
            price_risk_inflation=float(d.get("price_risk_inflation", 0.0)),
            generator_hash=str(d.get("generator_hash", "unfitted")),
            meta=dict(d.get("meta") or {}),
        )

    def save(self, path: Path = MODEL_PATH) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _design(rows: Sequence[PretradeFeatures]) -> np.ndarray:
    """Columns: impact term, then (intercept, spread) for each algo."""
    cols = [np.array([r.impact_term for r in rows], dtype=float)]
    for a in ALGOS:
        ind = np.array([1.0 if r.algo == a else 0.0 for r in rows])
        cols.append(ind)
        cols.append(ind * np.array([r.spread_bps for r in rows], dtype=float))
    return np.column_stack(cols)


def fit_pretrade(
    train: Sequence[tuple[PretradeFeatures, float]],
    calibration: Sequence[tuple[PretradeFeatures, float]],
    generator_hash: str,
    nominal: float = NOMINAL_COVERAGE,
    meta: dict[str, Any] | None = None,
) -> PretradeModel:
    """Weighted least squares on ``train`` (weights 1/price-risk^2), conformal ``q`` on ``calibration``.

    The weights matter: residual variance is dominated by price risk, which grows with duration,
    so unweighted OLS lets a few long orders set the coefficients.
    """
    xs = [x for x, _ in train]
    y = np.array([v for _, v in train], dtype=float)
    design = _design(xs)
    pr2 = np.array([x.price_risk_bps**2 for x in xs]) + 1.0
    w = 1.0 / pr2
    sw = np.sqrt(w)
    present = design.any(axis=0)
    beta = np.zeros(design.shape[1])
    sol, *_ = np.linalg.lstsq(design[:, present] * sw[:, None], y * sw, rcond=None)
    beta[present] = sol
    resid = y - design @ beta
    kappa, exec_noise = robust_scale(resid, np.sqrt(pr2 - 1.0))
    terms = {a: (float(beta[1 + 2 * k]), float(beta[2 + 2 * k])) for k, a in enumerate(ALGOS)}
    model = PretradeModel(
        float(beta[0]), terms, exec_noise, 1.0, nominal, generator_hash, dict(meta or {}), kappa
    )
    preds = [model.predict(x) for x, _ in calibration]
    scores = np.array(
        [abs(v - e.estimate_bps) / e.scale_bps for (_, v), e in zip(calibration, preds, strict=True)]
    )
    q = conformal_quantile(scores, nominal)
    return PretradeModel(
        model.impact_coefficient, terms, exec_noise, q, nominal, generator_hash, dict(meta or {}), kappa
    )


MEDIAN_ABS_NORMAL = 0.6744897501960817  # median of |N(0, 1)|


def robust_exec_noise(resid: np.ndarray, price_risk: np.ndarray, floor: float = 0.5) -> float:
    """Non-price dispersion ``s`` such that ``median(|r| / sqrt(pr^2 + s^2)) = 0.6745``.

    A median-matching estimator: rare large events (a halt, an overnight gap, an unexpected move)
    must not inflate the noise of every ordinary order. The tails are left to the conformal
    quantile, which is where they belong. Bisection on ``s`` in ``[floor, 500]`` bps.
    """
    r = np.abs(np.asarray(resid, dtype=float))
    pr = np.asarray(price_risk, dtype=float)

    def med(s: float) -> float:
        return float(np.median(r / np.sqrt(pr**2 + s * s)))

    if med(floor) <= MEDIAN_ABS_NORMAL:
        return floor
    lo, hi = floor, 500.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if med(mid) > MEDIAN_ABS_NORMAL:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def robust_scale(resid: np.ndarray, price_risk: np.ndarray, iterations: int = 20) -> tuple[float, float]:
    """``(kappa, s_exec)`` by median matching on two strata of price risk.

    Orders with below-median price risk pin ``s_exec`` (non-price noise dominates them), orders
    above the median pin ``kappa`` (price variance dominates); alternate until stable.
    """
    r = np.abs(np.asarray(resid, dtype=float))
    pr = np.asarray(price_risk, dtype=float)
    low = pr <= np.median(pr)
    kappa, s = 0.0, 1.0
    for _ in range(iterations):
        s = robust_exec_noise(r[low], np.sqrt(1.0 + kappa) * pr[low])
        lo, hi = -0.5, 5.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            z = r[~low] / np.sqrt((1.0 + mid) * pr[~low] ** 2 + s * s)
            if float(np.median(z)) > MEDIAN_ABS_NORMAL:
                lo = mid
            else:
                hi = mid
        kappa = max(0.0, 0.5 * (lo + hi))
    return kappa, s


def conformal_quantile(scores: np.ndarray, nominal: float) -> float:
    """Split-conformal quantile: the ceil((n+1) * nominal)-th smallest score."""
    s = np.sort(np.asarray(scores, dtype=float))
    n = s.size
    if n == 0:
        return float("inf")
    k = math.ceil((n + 1) * nominal)
    if k > n:  # too few calibration points for this level: the interval is unbounded
        return float("inf")
    return float(s[k - 1])


def group_conformal_quantile(scores_by_group: Sequence[np.ndarray], nominal: float) -> float:
    """Two-layer (group-level) conformal quantile for coverage in a *new* group (0.10.0 X4, R-24).

    Each group is one scenario regime; its own split-conformal quantile ``q_g`` is the score level
    that gives nominal coverage inside that regime. If a new regime is exchangeable with the K
    calibration regimes, its ``q_new`` is exchangeable with the ``q_g``, so the
    ``ceil((K + 1) * nominal)``-th smallest ``q_g`` bounds it with probability at least ``nominal``
    (the same rank argument as split conformal, one level up; Dunn, Wasserman and Ramdas 2022,
    "Distribution-free prediction sets for two-layer hierarchical models"). For K < 9 at 90 % the
    rank exceeds K and the largest ``q_g`` is used, which is the conservative end of that bound.
    """
    qs = sorted(conformal_quantile(np.asarray(g, dtype=float), nominal) for g in scores_by_group)
    if not qs:
        return float("inf")
    k = min(math.ceil((len(qs) + 1) * nominal), len(qs))
    return float(qs[k - 1])


def normalised_scores(model: PretradeModel, rows: Sequence[tuple[PretradeFeatures, float]]) -> np.ndarray:
    """``|y - E[cost]| / scale`` for each row: the conformal score."""
    out = []
    for x, v in rows:
        e = model.predict(x)
        out.append(abs(v - e.estimate_bps) / e.scale_bps)
    return np.array(out, dtype=float)


def coverage(model: PretradeModel, rows: Sequence[tuple[PretradeFeatures, float]]) -> float:
    if not rows:
        return float("nan")
    hits = 0
    for x, v in rows:
        e = model.predict(x)
        hits += int(e.lower_bps <= v <= e.upper_bps)
    return hits / len(rows)


@lru_cache(maxsize=1)
def default_model() -> PretradeModel:
    """The committed model (``scripts/train_pretrade.py`` writes it; see its model card)."""
    return PretradeModel.from_dict(json.loads(MODEL_PATH.read_text(encoding="utf-8")))
