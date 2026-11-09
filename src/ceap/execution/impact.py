"""Endogenous price impact: a propagator kernel driven by the bot's own signed flow (F2).

The v0.7 generator *plants* impact as a drift fixed in advance. Here impact is caused by the
trades themselves. A signed trade of ``q`` shares at time ``s`` moves the efficient price at
``t >= s`` by

    I(t - s) = (q / unit) * (permanent_bps + temporary_bps * exp(-(t - s) / decay_s))   [bps]

This is the transient-impact (propagator) model of Bouchaud, Gefen, Potters and Wyart (2004),
"Fluctuations and response in financial markets", Quant. Finance 4(2), with an exponential rather
than power-law kernel and a permanent floor - the discrete form of Obizhaeva and Wang (2013),
J. Financial Markets 16(1), whose resilience is exponential. Evidence class: published model,
simplified here; linear in volume, so it does not reproduce the concave square-root law in size
(that remains the pre-trade model in :mod:`ceap.analytics.market_impact`).

:func:`fit_propagator` recovers ``(permanent, temporary, decay)`` from a mid-price series and the
signed flow. It is a deconvolved multi-horizon markout: differencing the model gives

    dy_t = permanent * v_t + temporary * dE_t + noise,   E_t = a E_{t-1} + v_t,  a = exp(-dt/decay)

which is linear in the two amplitudes for a fixed decay, so the fit is OLS on a grid of decays.
:func:`response_curve` gives the plain (not deconvolved) markout curve for comparison.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PropagatorKernel:
    permanent_bps: float = 0.4
    temporary_bps: float = 1.2
    decay_s: float = 30.0
    unit_shares: int = 1_000

    def response(self, lag_s: float | np.ndarray) -> np.ndarray:
        """Impact in bps of one ``unit_shares`` buy, ``lag_s`` seconds later."""
        lag = np.asarray(lag_s, dtype=float)
        return np.where(lag >= 0, self.permanent_bps + self.temporary_bps * np.exp(-lag / self.decay_s), 0.0)


class PropagatorState:
    """Running impact of a flow, updated in O(1) per trade (exponential kernel)."""

    def __init__(self, kernel: PropagatorKernel) -> None:
        self.kernel = kernel
        self.cum_units = 0.0
        self.transient_units = 0.0
        self.t_s = 0.0

    def advance(self, t_s: float) -> None:
        if t_s > self.t_s:
            self.transient_units *= math.exp(-(t_s - self.t_s) / self.kernel.decay_s)
            self.t_s = t_s

    def trade(self, t_s: float, signed_shares: float) -> None:
        self.advance(t_s)
        u = signed_shares / self.kernel.unit_shares
        self.cum_units += u
        self.transient_units += u

    def impact_bps(self, t_s: float) -> float:
        self.advance(t_s)
        k = self.kernel
        return k.permanent_bps * self.cum_units + k.temporary_bps * self.transient_units


@dataclass(frozen=True)
class ImpactFit:
    permanent_bps: float
    temporary_bps: float
    decay_s: float
    permanent_se: float
    temporary_se: float
    n: int
    r2: float

    def compare(self, truth: PropagatorKernel) -> dict[str, dict[str, float]]:
        """Recovered vs true parameters, with z-scores for the two amplitudes."""

        def row(est: float, se: float, true: float) -> dict[str, float]:
            z = (est - true) / se if se > 0 else float("nan")
            return {"estimate": est, "se": se, "truth": true, "z": z}

        return {
            "permanent_bps": row(self.permanent_bps, self.permanent_se, truth.permanent_bps),
            "temporary_bps": row(self.temporary_bps, self.temporary_se, truth.temporary_bps),
            "decay_s": {
                "estimate": self.decay_s,
                "se": float("nan"),
                "truth": truth.decay_s,
                "z": float("nan"),
            },
        }


def _design(v: np.ndarray, a: float) -> np.ndarray:
    e = np.empty_like(v)
    acc = 0.0
    for i, x in enumerate(v.tolist()):
        acc = a * acc + x
        e[i] = acc
    de = np.diff(np.concatenate([[0.0], e]))
    return np.column_stack([v, de])


def fit_propagator(
    mid: Sequence[float] | np.ndarray,
    signed_flow: Sequence[float] | np.ndarray,
    dt_s: float,
    unit_shares: int = 1_000,
    decay_grid_s: Sequence[float] | None = None,
) -> ImpactFit:
    """Fit the exponential propagator to a regular grid of mids and signed flow per bucket.

    ``mid[t]`` is the mid at the *end* of bucket ``t`` and ``signed_flow[t]`` the bot's signed
    shares traded in bucket ``t``. Returns the OLS amplitudes at the best decay on the grid,
    with homoskedastic standard errors (price noise is i.i.d. after differencing a random walk).
    """
    m = np.asarray(mid, dtype=float)
    v = np.asarray(signed_flow, dtype=float) / unit_shares
    if m.shape != v.shape or m.size < 10:
        raise ValueError("mid and signed_flow must have the same length (at least 10)")
    y = np.diff(m, prepend=m[0]) / m[0] * 10_000.0
    grid = list(decay_grid_s) if decay_grid_s is not None else list(np.geomspace(dt_s, 600.0 * dt_s, 60))
    best: tuple[float, float, np.ndarray, np.ndarray] | None = None
    for decay in grid:
        x = _design(v, math.exp(-dt_s / decay))
        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
        sse = float(((y - x @ beta) ** 2).sum())
        if best is None or sse < best[0]:
            best = (sse, float(decay), beta, x)
    assert best is not None
    sse, decay, beta, x = best
    n, p = x.shape
    sigma2 = sse / max(n - p - 1, 1)
    xtx = x.T @ x
    cov = sigma2 * np.linalg.pinv(xtx)
    sst = float(((y - y.mean()) ** 2).sum())
    return ImpactFit(
        permanent_bps=float(beta[0]),
        temporary_bps=float(beta[1]),
        decay_s=decay,
        permanent_se=float(math.sqrt(max(cov[0, 0], 0.0))),
        temporary_se=float(math.sqrt(max(cov[1, 1], 0.0))),
        n=int(n),
        r2=1.0 - sse / sst if sst > 0 else float("nan"),
    )


def response_curve(
    mid: Sequence[float] | np.ndarray,
    signed_flow: Sequence[float] | np.ndarray,
    horizons: Sequence[int],
    unit_shares: int = 1_000,
) -> dict[int, float]:
    """Plain markout curve: flow-weighted mid change (bps per unit) ``h`` buckets after each trade.

    Not deconvolved: when the bot keeps trading the same way, later trades add to the response, so
    this curve overstates permanent impact; :func:`fit_propagator` removes that overlap.
    """
    m = np.asarray(mid, dtype=float)
    v = np.asarray(signed_flow, dtype=float) / unit_shares
    out: dict[int, float] = {}
    idx = np.flatnonzero(v)
    for h in horizons:
        ok = idx[idx + h < m.size]
        if ok.size == 0:
            out[int(h)] = float("nan")
            continue
        start = m[np.maximum(ok - 1, 0)]
        dm = (m[ok + h] - start) / m[0] * 10_000.0
        out[int(h)] = float((dm * np.sign(v[ok])).sum() / np.abs(v[ok]).sum())
    return out


def fit_markout_curve(curve: Mapping[int, float], dt_s: float = 1.0) -> ImpactFit:
    """Fit ``permanent + temporary * exp(-h dt / decay)`` to a markout curve (bps per unit by horizon).

    Use it on :func:`response_curve` output when the bot's flow is not autocorrelated (otherwise
    deconvolve with :func:`fit_propagator`). SEs are from the curve residuals, so they are rough:
    the points of a markout curve share trades and are not independent.
    """
    keys = sorted(curve)
    h_all = np.array(keys, dtype=np.float64)
    y_all = np.array([curve[k] for k in keys], dtype=np.float64)
    ok = np.isfinite(y_all)
    h, y = h_all[ok], y_all[ok]
    if h.size < 3:
        raise ValueError("need at least three finite horizons")
    best: tuple[float, float, np.ndarray, np.ndarray] | None = None
    for d in np.geomspace(dt_s, 600.0 * dt_s, 120).tolist():
        x = np.column_stack([np.ones_like(h), np.exp(-h * dt_s / d)])
        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
        sse = float(((y - x @ beta) ** 2).sum())
        if best is None or sse < best[0]:
            best = (sse, d, beta, x)
    assert best is not None
    sse, decay, beta, x = best
    dof = max(h.size - 3, 1)
    cov = sse / dof * np.linalg.pinv(x.T @ x)
    sst = float(((y - y.mean()) ** 2).sum())
    return ImpactFit(
        float(beta[0]),
        float(beta[1]),
        decay,
        float(math.sqrt(max(cov[0, 0], 0.0))),
        float(math.sqrt(max(cov[1, 1], 0.0))),
        int(h.size),
        1.0 - sse / sst if sst > 0 else float("nan"),
    )
