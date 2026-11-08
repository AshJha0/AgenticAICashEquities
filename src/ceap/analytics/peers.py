"""Peer-relative residual attribution (2T.6; replaces the v1 "causal DiD").

A parent's measured quantity (its IS residual against the pre-trade estimate, a spread, a fill
rate) is compared with the same quantity on *matched peers*: same symbol, side and size bucket,
other sessions. The output is an effect size with a bootstrap confidence interval over peers. It
is a comparison, not a causal claim: peers share the observable regime, nothing more is
controlled for, and the report says so.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

import numpy as np

T = TypeVar("T")

NO_CAUSAL_CLAIM = (
    "Peer-relative comparison against matched orders (same symbol, side and size bucket, other "
    "sessions). It measures how unusual this order is; it does not identify a cause."
)


@dataclass(frozen=True)
class PeerEffect:
    """``effect = value - mean(peers)`` with a percentile bootstrap CI over peers."""

    value: float
    peer_mean: float
    effect: float
    ci_low: float
    ci_high: float
    standardised: float  # effect / peer SD (NaN with fewer than two peers)
    n_peers: int
    confidence: float = 0.90

    @property
    def excludes_zero(self) -> bool:
        return self.n_peers >= 2 and (self.ci_low > 0 or self.ci_high < 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "peer_mean": self.peer_mean,
            "effect": self.effect,
            "ci": [self.ci_low, self.ci_high],
            "confidence": self.confidence,
            "standardised_effect": self.standardised,
            "n_peers": self.n_peers,
            "excludes_zero": self.excludes_zero,
        }


def peer_effect(
    value: float,
    peer_values: Sequence[float],
    n_boot: int = 2000,
    confidence: float = 0.90,
    seed: int = 0,
) -> PeerEffect:
    """Effect of ``value`` against ``peer_values`` with a bootstrap CI of the peer mean.

    The CI resamples peers with replacement (the peer mean is the uncertain quantity; the target
    is one observation), so ``effect`` CI = ``value - CI(peer mean)``. Deterministic in ``seed``.
    """
    v = float(value)
    peers = np.asarray([p for p in peer_values if p is not None and math.isfinite(p)], dtype=float)
    n = peers.size
    nan = float("nan")
    if n == 0 or not math.isfinite(v):
        return PeerEffect(v, nan, nan, nan, nan, nan, int(n), confidence)
    mean = float(peers.mean())
    sd = float(peers.std(ddof=1)) if n >= 2 else nan
    if n == 1:
        return PeerEffect(v, mean, v - mean, nan, nan, nan, 1, confidence)
    rng = np.random.default_rng(seed)
    boot = peers[rng.integers(0, n, size=(n_boot, n))].mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    lo_mean, hi_mean = np.quantile(boot, [alpha, 1.0 - alpha])
    std_effect = (v - mean) / sd if sd and sd > 0 else nan
    return PeerEffect(
        v, mean, v - mean, v - float(hi_mean), v - float(lo_mean), std_effect, int(n), confidence
    )


def match_peers(
    target: T,
    candidates: Iterable[T],
    key: Callable[[T], tuple[Any, ...]],
    session: Callable[[T], Any],
) -> list[T]:
    """Candidates with the same match ``key`` as ``target`` from *other* sessions."""
    k, s = key(target), session(target)
    return [c for c in candidates if c is not target and key(c) == k and session(c) != s]
