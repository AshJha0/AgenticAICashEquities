"""Markout curves by venue and liquidity flag, with bootstrap CIs, and a routing table (F7).

A markout asks: after we traded, where did the mid go? For a fill at price ``p`` and the mid
``h`` seconds later, ``m_{t+h}``, the markout is ``s (m_{t+h} - p) / p`` in bps, positive when the
price moved our way (the convention of
:func:`ceap.analytics.slippage.markout_bps`, extended to groups that mix buys and sells). A passive fill with a strongly negative markout was
adversely selected: we were filled because the price was about to move through us.

Curves are size-weighted means at each horizon from 1 s to the close, grouped by venue and by
liquidity flag (``ADD``/``REMOVE``/``AUCTION``), each with a percentile bootstrap CI over fills.
The routing table turns the curve at one horizon into per-venue evidence instead of a single cause
score: a venue is ``adverse`` when its whole CI is below zero, ``favourable`` when above, else
``indistinguishable``; the suggested weight tilts the current fill share by ``exp(markout / scale)``
and halves venues flagged adverse. That weighting is a design choice (evidence class: heuristic),
meant to be read next to the CIs, not followed blindly.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from ceap.analytics.execution_metrics import QuoteIndex
from ceap.domain.execution import Execution
from ceap.domain.market import Quote

CLOSE = "close"
DEFAULT_HORIZONS: tuple[int | str, ...] = (1, 5, 30, 60, 300, CLOSE)


@dataclass(frozen=True)
class MarkoutPoint:
    group: tuple[str, ...]
    horizon: int | str
    n: int
    mean_bps: float
    ci_low_bps: float
    ci_high_bps: float


def _bootstrap_ci(
    values: np.ndarray, weights: np.ndarray, n_boot: int, confidence: float, rng: np.random.Generator
) -> tuple[float, float]:
    n = values.size
    if n < 2:
        return float("nan"), float("nan")
    idx = rng.integers(0, n, size=(n_boot, n))
    w = weights[idx]
    means = (values[idx] * w).sum(axis=1) / w.sum(axis=1)
    a = (1.0 - confidence) / 2.0
    lo, hi = np.quantile(means, [a, 1.0 - a])
    return float(lo), float(hi)


def markout_curves(
    executions: Sequence[Execution],
    quotes: Sequence[Quote] | QuoteIndex,
    horizons: Sequence[int | str] = DEFAULT_HORIZONS,
    close_time: datetime | None = None,
    by: Sequence[str] = ("venue", "liquidity_flag"),
    n_boot: int = 1000,
    confidence: float = 0.90,
    seed: int = 0,
) -> list[MarkoutPoint]:
    """Size-weighted markout curves per group with bootstrap CIs. Deterministic in ``seed``.

    ``by`` names :class:`~ceap.domain.execution.Execution` attributes; ``()`` gives one overall curve.
    ``close`` marks each fill to the last quote of its own session (calendar day in the fill's time
    zone) unless ``close_time`` is given.
    A fill whose later mid is missing (after the last quote) is left out of that horizon.
    """
    index = quotes if isinstance(quotes, QuoteIndex) else QuoteIndex(quotes)
    rng = np.random.default_rng(seed)
    groups: dict[tuple[str, ...], list[Execution]] = defaultdict(list)
    for e in executions:
        groups[tuple(str(getattr(e, a) or "UNKNOWN") for a in by)].append(e)
    last = index.last_timestamp
    out: list[MarkoutPoint] = []
    for g in sorted(groups):
        fills = groups[g]
        for h in horizons:
            px, mids, qty, signs = [], [], [], []
            for e in fills:
                if h == CLOSE:  # each fill to its own session's last quote unless a close is given
                    t = close_time if close_time is not None else index.last_timestamp_on(e.timestamp)
                else:
                    t = e.timestamp + timedelta(seconds=float(h))
                if t is None or last is None or t > last:
                    continue
                m = index.mid_at_or_nan(t)
                if not math.isfinite(m):
                    continue
                px.append(e.price)
                mids.append(m)
                qty.append(e.quantity)
                signs.append(e.side.sign)
            if not px:
                out.append(MarkoutPoint(g, h, 0, float("nan"), float("nan"), float("nan")))
                continue
            pa, ma, qa, sa = (np.asarray(x, dtype=np.float64) for x in (px, mids, qty, signs))
            per_fill = sa * (ma - pa) / pa * 10_000.0
            mean = float((per_fill * qa).sum() / qa.sum())
            lo, hi = _bootstrap_ci(per_fill, qa, n_boot, confidence, rng)
            out.append(MarkoutPoint(g, h, int(pa.size), mean, lo, hi))
    return out


@dataclass(frozen=True)
class VenueRoute:
    venue: str
    n: int
    fill_share: float
    markout_bps: float
    ci_low_bps: float
    ci_high_bps: float
    verdict: str
    suggested_weight: float


def routing_table(
    executions: Sequence[Execution],
    quotes: Sequence[Quote] | QuoteIndex,
    horizon: int | str = 60,
    scale_bps: float = 2.0,
    n_boot: int = 1000,
    seed: int = 0,
) -> list[VenueRoute]:
    """Per-venue markout at ``horizon`` with a CI, a verdict and a suggested routing weight."""
    pts = markout_curves(executions, quotes, (horizon,), by=("venue",), n_boot=n_boot, seed=seed)
    volume: dict[str, float] = defaultdict(float)
    for e in executions:
        volume[str(e.venue)] += e.quantity
    total = sum(volume.values()) or 1.0
    raw: list[tuple[MarkoutPoint, float, str, float]] = []
    for pt in pts:
        v = pt.group[0]
        share = volume[v] / total
        if math.isfinite(pt.ci_high_bps) and pt.ci_high_bps < 0:
            verdict = "adverse"
        elif math.isfinite(pt.ci_low_bps) and pt.ci_low_bps > 0:
            verdict = "favourable"
        else:
            verdict = "indistinguishable"
        tilt = math.exp(max(min(pt.mean_bps / scale_bps, 5.0), -5.0)) if math.isfinite(pt.mean_bps) else 1.0
        w = share * tilt * (0.5 if verdict == "adverse" else 1.0)
        raw.append((pt, share, verdict, w))
    wsum = sum(w for *_, w in raw) or 1.0
    rows = [
        VenueRoute(pt.group[0], pt.n, share, pt.mean_bps, pt.ci_low_bps, pt.ci_high_bps, verdict, w / wsum)
        for pt, share, verdict, w in raw
    ]
    return sorted(rows, key=lambda r: -(r.markout_bps if math.isfinite(r.markout_bps) else -math.inf))
