"""Parent-order schedulers behind one ``Schedule`` interface (F3).

A schedule answers one question: of a parent of ``X`` shares cut into ``n`` slices, how many
shares should be done by the end of each slice? Every scheduler returns slice *weights* that sum
to one; :func:`allocate` turns weights into whole shares that sum exactly to the parent, so no
scheduler can lose or invent a share through rounding.

* :class:`TWAP` - equal slices.
* :class:`VWAP` - slices in proportion to the expected intraday volume profile (the generator's
  U-shape by default, so the schedule matches the synthetic market it trades in).
* :class:`POV` - a target fraction of realised market volume, kept inside a band. Its weights are
  only the forecast; :meth:`POV.adjust` re-targets each slice from the volume actually printed.
* :class:`AlmgrenChriss` - the risk-averse implementation-shortfall trajectory of Almgren and
  Chriss (2000), "Optimal execution of portfolio transactions", J. Risk 3(2), section 3 (closed
  form, evidence class: published derivation, re-derived in the known-answer tests).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class Schedule(Protocol):
    """What every scheduler offers. ``weights(n)`` is non-negative and sums to one."""

    name: str

    def weights(self, n_slices: int) -> np.ndarray: ...

    def adjust(self, slice_index: int, planned_cumulative: int, executed: int, market_volume: float) -> int:
        """Cumulative target for the slice given what has happened; static schedules keep the plan."""
        ...


def allocate(total: int, weights: Sequence[float] | np.ndarray) -> np.ndarray:
    """Whole-share slice sizes summing exactly to ``total`` (largest-remainder rounding).

    Ties in the remainders go to the earlier slice, so the result is deterministic.
    """
    w = np.asarray(weights, dtype=float)
    if total < 0:
        raise ValueError("total must be non-negative")
    if w.ndim != 1 or w.size == 0 or np.any(w < 0) or not np.isfinite(w).all() or w.sum() <= 0:
        raise ValueError("weights must be a non-empty, finite, non-negative vector with a positive sum")
    raw = total * w / w.sum()
    base = np.floor(raw).astype(np.int64)
    short = int(total - base.sum())
    if short:
        order = np.argsort(-(raw - base), kind="stable")
        base[order[:short]] += 1
    return base


def cumulative_targets(schedule: Schedule, total: int, n_slices: int) -> np.ndarray:
    """Shares that should be done by the end of each slice (last element == ``total``)."""
    return np.cumsum(allocate(total, schedule.weights(n_slices)))


def _check_n(n_slices: int) -> None:
    if n_slices < 1:
        raise ValueError("n_slices must be at least 1")


@dataclass(frozen=True)
class _Static:
    def adjust(self, slice_index: int, planned_cumulative: int, executed: int, market_volume: float) -> int:  # noqa: ARG002
        return planned_cumulative


@dataclass(frozen=True)
class TWAP(_Static):
    name: str = "TWAP"

    def weights(self, n_slices: int) -> np.ndarray:
        _check_n(n_slices)
        return np.full(n_slices, 1.0 / n_slices)


@dataclass(frozen=True)
class VWAP(_Static):
    """Weights proportional to expected volume in each slice.

    ``profile`` is the expected volume per slice when given; otherwise the U-shaped profile
    ``a + b (2x - 1)^2`` of :func:`ceap.data.synthetic.intraday_profile` is integrated over the
    part of the session ``[start_fraction, end_fraction]`` the order trades in.
    """

    start_fraction: float = 0.0
    end_fraction: float = 1.0
    profile: tuple[float, ...] | None = None
    name: str = "VWAP"

    def weights(self, n_slices: int) -> np.ndarray:
        _check_n(n_slices)
        if self.profile is not None:
            if len(self.profile) != n_slices:
                raise ValueError("profile length must equal n_slices")
            w = np.asarray(self.profile, dtype=float)
        else:
            from ceap.data.synthetic import U_SHAPE_A, U_SHAPE_B

            if not 0.0 <= self.start_fraction < self.end_fraction <= 1.0:
                raise ValueError("need 0 <= start_fraction < end_fraction <= 1")
            edges = np.linspace(self.start_fraction, self.end_fraction, n_slices + 1)

            def antiderivative(x: np.ndarray) -> np.ndarray:  # of a + b (2x-1)^2
                return U_SHAPE_A * x + U_SHAPE_B * (2.0 * x - 1.0) ** 3 / 6.0

            w = np.diff(antiderivative(edges))
        return w / w.sum()


@dataclass(frozen=True)
class POV:
    """Participate at ``rate`` of market volume, held inside ``[rate - band, rate + band]``.

    ``forecast`` (expected market volume per slice) sets the static weights used for planning;
    in execution :meth:`adjust` returns ``clip(rate * V, lo, hi)`` where ``V`` is the market volume
    printed so far, so a quiet market slows the order and a busy one speeds it up.
    """

    rate: float = 0.10
    band: float = 0.02
    total: int | None = None
    forecast: tuple[float, ...] | None = None
    name: str = "POV"

    def __post_init__(self) -> None:
        if not 0.0 < self.rate < 1.0 or not 0.0 <= self.band < self.rate:
            raise ValueError("need 0 < rate < 1 and 0 <= band < rate")

    def weights(self, n_slices: int) -> np.ndarray:
        _check_n(n_slices)
        if self.forecast is None:
            return VWAP().weights(n_slices)
        return VWAP(profile=self.forecast).weights(n_slices)

    def bounds(self, market_volume: float) -> tuple[float, float]:
        return (self.rate - self.band) * market_volume, (self.rate + self.band) * market_volume

    def adjust(self, slice_index: int, planned_cumulative: int, executed: int, market_volume: float) -> int:  # noqa: ARG002
        lo, hi = self.bounds(max(market_volume, 0.0))
        cap = self.total if self.total is not None else math.inf
        target = min(max(self.rate * market_volume, lo), hi, cap)
        return int(max(round(target), 0))


@dataclass(frozen=True)
class AlmgrenChriss(_Static):
    """Almgren-Chriss (2000) optimal trajectory with linear temporary and permanent impact.

    Units are the caller's but must agree: ``sigma`` in price per sqrt(time unit), ``eta`` in
    price per (share per time unit), ``gamma`` in price per share, ``epsilon`` in price per share
    (half spread plus fees), ``horizon`` in time units, ``risk_aversion`` (lambda) per price^2.

    With slice length ``tau = T/N`` and ``eta_tilde = eta - gamma tau / 2`` the holdings are

        x_j = X sinh(kappa (T - t_j)) / sinh(kappa T),   cosh(kappa tau) = 1 + tau^2 kappa_tilde^2 / 2,

    with ``kappa_tilde^2 = lambda sigma^2 / eta_tilde`` (their eq. 18-20). ``lambda -> 0`` gives the
    straight line (TWAP); larger ``lambda`` front-loads.
    """

    sigma: float = 0.02
    eta: float = 2.5e-6
    gamma: float = 2.5e-7
    epsilon: float = 0.0
    horizon: float = 1.0
    risk_aversion: float = 1e-6
    name: str = "AlmgrenChriss"

    def eta_tilde(self, n_slices: int) -> float:
        et = self.eta - self.gamma * (self.horizon / n_slices) / 2.0
        if et <= 0:
            raise ValueError("eta - gamma*tau/2 must be positive (use more slices or a larger eta)")
        return et

    def kappa(self, n_slices: int) -> float:
        _check_n(n_slices)
        tau = self.horizon / n_slices
        kt2 = self.risk_aversion * self.sigma**2 / self.eta_tilde(n_slices)
        return float(np.arccosh(1.0 + 0.5 * tau * tau * kt2) / tau)

    def holdings(self, total: float, n_slices: int) -> np.ndarray:
        """``x_0 .. x_N``: shares still held at each slice boundary (``x_0 = X``, ``x_N = 0``)."""
        k = self.kappa(n_slices)
        t = np.linspace(0.0, self.horizon, n_slices + 1)
        if k * self.horizon < 1e-9:  # lambda -> 0: the linear limit, avoiding 0/0
            return total * (1.0 - t / self.horizon)
        return total * np.sinh(k * (self.horizon - t)) / np.sinh(k * self.horizon)

    def weights(self, n_slices: int) -> np.ndarray:
        x = self.holdings(1.0, n_slices)
        return -np.diff(x)

    def expected_cost(self, total: float, n_slices: int) -> float:
        """``E = gamma X^2 / 2 + epsilon X + (eta_tilde / tau) sum n_k^2`` (their eq. 9), price x shares."""
        n = total * self.weights(n_slices)
        tau = self.horizon / n_slices
        return float(
            0.5 * self.gamma * total**2
            + self.epsilon * np.abs(n).sum()
            + self.eta_tilde(n_slices) / tau * (n**2).sum()
        )

    def variance(self, total: float, n_slices: int) -> float:
        """``V = sigma^2 tau sum_{k=1..N} x_k^2`` (their eq. 10), price^2 x shares^2."""
        x = self.holdings(total, n_slices)[1:]
        tau = self.horizon / n_slices
        return float(self.sigma**2 * tau * (x**2).sum())

    def frontier(
        self, total: float, n_slices: int, lambdas: Sequence[float]
    ) -> list[tuple[float, float, float]]:
        """``[(lambda, E, sqrt V), ...]``: the efficient frontier traced by the risk aversion."""
        from dataclasses import replace

        out = []
        for lam in lambdas:
            s = replace(self, risk_aversion=float(lam))
            out.append((float(lam), s.expected_cost(total, n_slices), math.sqrt(s.variance(total, n_slices))))
        return out
