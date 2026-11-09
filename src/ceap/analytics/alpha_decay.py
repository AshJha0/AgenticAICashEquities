"""Alpha decay: the IC term structure and its half-life (0.10 D15).

Plain idea: a signal that predicts tomorrow's return may say nothing about next quarter's, and the speed
at which its forecasting power fades sets how often it must be traded. For horizons ``h = 1 .. H`` the
mean cross-sectional rank IC of the signal against the *marginal* return of day ``h`` (the return from
``t + h - 1`` to ``t + h`` after the execution lag) is measured; a geometric decay ``IC_h = IC_1 *
phi^(h - 1)`` is fitted by least squares on ``log |IC|`` over the leading horizons that keep the sign of
``IC_1`` and a fifth of its size, giving the half-life ``log(0.5) / log(phi)``. On a synthetic
dataset the measured curve is compared with the generator's closed form (:mod:`ceap.data.ground_truth`), so the decay estimate itself
is checked against a known truth.

Worked example: a latent drift with a 252-day half-life gives a slowly decaying IC (half-life of the
marginal IC of hundreds of days); a transient AR(1) component with ``rho = 0.6`` gives a reversal IC that
halves in about 1.4 days.

Evidence class: method (term-structure measurement) plus simulation (the truth comparison).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ceap.analytics.signal_statistics import rowwise_rank_corr
from ceap.analytics.signals import dataset_signal

FLOOR = 0.2


def ic_term_structure(ds: Any, signal: str, horizons: int = 60, lag: int = 1, step: int = 5) -> dict[str, Any]:
    """Mean rank IC of ``signal`` against the marginal day-``h`` return, ``h = 1..horizons`` (in-sample)."""
    sig = dataset_signal(ds, signal)
    close = np.asarray(ds.close, dtype=float)
    n = close.shape[0]
    with np.errstate(divide="ignore", invalid="ignore"):
        daily = np.full(close.shape, np.nan)
        daily[1:] = close[1:] / close[:-1] - 1.0
    i0, i1 = ds.start_index, ds.in_sample_end_index - horizons - lag
    rows = np.arange(i0, max(i0, i1), step)
    curve = []
    for h in range(1, horizons + 1):
        k = lag + h
        idx = rows[rows + k < n]
        ic = rowwise_rank_corr(sig[idx], daily[idx + k])
        ic = ic[np.isfinite(ic)]
        curve.append({"h": h, "ic": float(ic.mean()) if ic.size else float("nan"), "n": int(ic.size)})
    fit = fit_half_life([c["ic"] for c in curve])
    return {"signal": signal, "lag": lag, "step": step, "curve": curve, **fit}


def fit_half_life(ic: list[float]) -> dict[str, float]:
    """Least-squares geometric decay on ``log |IC_h|`` over horizons with the sign of ``IC_1``."""
    x = np.asarray(ic, dtype=float)
    if x.size < 3 or not math.isfinite(x[0]) or x[0] == 0:
        return {"phi": float("nan"), "half_life_days": float("nan")}
    sign = np.sign(x[0])
    h = np.arange(x.size, dtype=float)
    # the leading run of horizons whose IC keeps the sign and at least FLOOR of |IC_1|: past it the curve is
    # noise around zero and would flatten the fitted decay
    keep = np.isfinite(x) & (np.sign(x) == sign) & (np.abs(x) >= FLOOR * abs(x[0]))
    run = int(np.argmin(keep)) if not keep.all() else x.size
    ok = np.zeros(x.size, dtype=bool)
    ok[: max(run, 3)] = True
    ok &= np.isfinite(x) & (np.sign(x) == sign) & (np.abs(x) > 0)
    if ok.sum() < 3:
        return {"phi": float("nan"), "half_life_days": float("nan")}
    slope, _ = np.polyfit(h[ok], np.log(np.abs(x[ok])), 1)
    phi = float(math.exp(slope))
    hl = float(math.log(0.5) / math.log(phi)) if 0 < phi < 1 else float("inf")
    return {"phi": phi, "half_life_days": hl}


def expected_term_structure(spec: Any, signal: str, horizons: int = 60, lag: int = 1) -> list[float]:
    """Generator-truth Pearson correlation of the signal with the marginal day-``h`` return."""
    from ceap.data.ground_truth import _universe, signal_correlations

    sigma2 = _universe(spec)["sigma2"]
    out = []
    for h in range(1, horizons + 1):
        rho, _ = signal_correlations(spec, signal, 1, lag + h - 1, 1, sigma2)
        out.append(rho)
    return out
