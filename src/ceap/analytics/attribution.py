"""Deterministic root-cause attribution.

Compares the investigation window against a baseline window and scores a
fixed catalogue of candidate causes. The scores are *evidence-derived*;
the LLM only narrates them. Thresholds are documented in
``docs/evaluation/evaluation.md`` and can be tuned via :class:`Thresholds`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ceap.domain.execution import ExecutionMetrics
from ceap.domain.market import MarketStatistics

MATERIAL_SCORE = 0.35  # a cause with score >= this is reported as material


class Cause(str, Enum):
    MARKET_VOLATILITY = "MARKET_VOLATILITY"
    WIDE_SPREADS = "WIDE_SPREADS"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    VENUE_DEGRADATION = "VENUE_DEGRADATION"
    TECHNOLOGY_LATENCY = "TECHNOLOGY_LATENCY"
    MARKET_DATA_ANOMALY = "MARKET_DATA_ANOMALY"
    LARGE_ORDER_IMPACT = "LARGE_ORDER_IMPACT"
    PRICE_MOVEMENT = "PRICE_MOVEMENT"
    NORMAL = "NORMAL"


@dataclass(frozen=True)
class Thresholds:
    volatility_ratio: float = 1.6
    spread_ratio: float = 1.4
    depth_ratio: float = 0.65
    venue_fill_rate_gap: float = 0.25  # venue fill rate below (peer mean - gap)
    venue_slippage_excess_bps: float = 2.0
    latency_ratio: float = 3.0
    reject_rate: float = 0.05
    stale_quote_fraction: float = 0.03
    crossed_quotes: int = 5
    participation_rate: float = 0.20
    order_size_ratio: float = 4.0
    price_move_bps: float = 60.0
    deterioration_bps: float = 1.5  # min IS increase before we look for causes


@dataclass(frozen=True)
class CauseScore:
    cause: Cause
    score: float  # 0..1
    rationale: str
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AttributionResult:
    deteriorated: bool
    is_delta_bps: float
    vwap_slippage_delta_bps: float
    ranked: tuple[CauseScore, ...]
    primary: Cause
    secondary: tuple[Cause, ...]

    @property
    def material(self) -> tuple[CauseScore, ...]:
        return tuple(c for c in self.ranked if c.score >= MATERIAL_SCORE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deteriorated": self.deteriorated,
            "is_delta_bps": self.is_delta_bps,
            "vwap_slippage_delta_bps": self.vwap_slippage_delta_bps,
            "primary": self.primary.value,
            "secondary": [c.value for c in self.secondary],
            "ranked": [
                {"cause": c.cause.value, "score": c.score, "rationale": c.rationale, "metrics": c.metrics}
                for c in self.ranked
            ],
        }


def _f(value: Any) -> float:
    """Coerce tool output (which may carry None for NaN) to a float."""
    try:
        return float(value) if value is not None else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _finite_or(value: Any, default: float) -> float:
    x = _f(value)
    return default if math.isnan(x) else x


def _ratio(a: float | None, b: float | None) -> float:
    a, b = _f(a), _f(b)
    if math.isnan(a) or math.isnan(b) or b == 0:
        return float("nan")
    return a / b


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _sig(excess: float, scale: float) -> float:
    """Map an excess-over-threshold (>=0) to a 0..1 score with saturation."""
    if math.isnan(excess) or excess <= 0:
        return 0.0
    return _clamp(1.0 - math.exp(-excess / scale))


def attribute_causes(
    window: ExecutionMetrics,
    baseline: ExecutionMetrics | None,
    window_market: MarketStatistics,
    baseline_market: MarketStatistics | None,
    engineering: dict[str, Any] | None = None,
    thresholds: Thresholds = Thresholds(),
) -> AttributionResult:
    """Score candidate causes for the execution-quality change in ``window``."""
    t = thresholds
    eng = engineering or {}
    scores: list[CauseScore] = []

    base_is = baseline.implementation_shortfall_bps if baseline else 0.0
    is_delta = window.implementation_shortfall_bps - (base_is if not math.isnan(base_is) else 0.0)
    base_vs = baseline.slippage_vs_vwap_bps if baseline else 0.0
    vwap_delta = window.slippage_vs_vwap_bps - (base_vs if not math.isnan(base_vs) else 0.0)
    # A VWAP algo is judged against interval VWAP (drift-neutral) as well as arrival (IS).
    deltas = [d for d in (is_delta, vwap_delta) if not math.isnan(d)]
    deteriorated = bool(deltas) and max(deltas) >= t.deterioration_bps

    # --- market volatility --------------------------------------------------
    vol_ratio = _ratio(
        window_market.realised_volatility_bps,
        baseline_market.realised_volatility_bps if baseline_market else float("nan"),
    )
    scores.append(
        CauseScore(
            Cause.MARKET_VOLATILITY,
            _sig(vol_ratio - t.volatility_ratio, 0.8),
            f"realised volatility ratio vs baseline = {vol_ratio:.2f} (threshold {t.volatility_ratio})",
            {"volatility_ratio": vol_ratio, "window_vol_bps": window_market.realised_volatility_bps},
        )
    )

    # --- wide spreads -------------------------------------------------------
    spread_ratio = _ratio(
        window_market.average_spread_bps,
        baseline_market.average_spread_bps if baseline_market else float("nan"),
    )
    scores.append(
        CauseScore(
            Cause.WIDE_SPREADS,
            _sig(spread_ratio - t.spread_ratio, 0.5),
            f"average spread ratio vs baseline = {spread_ratio:.2f} (threshold {t.spread_ratio})",
            {"spread_ratio": spread_ratio, "window_spread_bps": window_market.average_spread_bps},
        )
    )

    # --- low liquidity ------------------------------------------------------
    depth_ratio = _ratio(
        window_market.average_displayed_depth,
        baseline_market.average_displayed_depth if baseline_market else float("nan"),
    )
    tob_ratio = _ratio(
        window_market.average_top_of_book_size,
        baseline_market.average_top_of_book_size if baseline_market else float("nan"),
    )
    liq_ratio = min(depth_ratio, tob_ratio) if not math.isnan(depth_ratio) else tob_ratio
    scores.append(
        CauseScore(
            Cause.LOW_LIQUIDITY,
            _sig(t.depth_ratio - liq_ratio, 0.2),
            f"displayed depth ratio vs baseline = {liq_ratio:.2f} (threshold {t.depth_ratio})",
            {"depth_ratio": depth_ratio, "top_of_book_ratio": tob_ratio},
        )
    )

    # --- venue degradation --------------------------------------------------
    venue_score, venue_rationale, venue_metrics = _venue_degradation(window, t)
    scores.append(CauseScore(Cause.VENUE_DEGRADATION, venue_score, venue_rationale, venue_metrics))

    # --- technology latency -------------------------------------------------
    lat_ratio = _ratio(window.average_latency_us, baseline.average_latency_us if baseline else float("nan"))
    eng_lat_ratio = _f(eng.get("latency_ratio"))
    lat_ratio_eff = (
        max(x for x in (lat_ratio, eng_lat_ratio) if not math.isnan(x))
        if not (math.isnan(lat_ratio) and math.isnan(eng_lat_ratio))
        else float("nan")
    )
    reject = max(_finite_or(window.reject_rate, 0.0), _finite_or(eng.get("reject_rate"), 0.0))
    tech_score = max(_sig(lat_ratio_eff - t.latency_ratio, 2.0), _sig(reject - t.reject_rate, 0.05))
    scores.append(
        CauseScore(
            Cause.TECHNOLOGY_LATENCY,
            tech_score,
            f"execution latency ratio vs baseline = {lat_ratio_eff:.2f} (threshold {t.latency_ratio}); reject rate = {reject:.1%}",
            {
                "latency_ratio": lat_ratio_eff,
                "reject_rate": reject,
                "window_latency_us": window.average_latency_us,
            },
        )
    )

    # --- market data anomaly ------------------------------------------------
    stale = window_market.stale_quote_fraction
    base_stale = baseline_market.stale_quote_fraction if baseline_market else 0.0
    crossed = window_market.crossed_quote_count
    md_score = max(
        _sig(stale - max(t.stale_quote_fraction, 3 * base_stale), 0.05), _sig(crossed - t.crossed_quotes, 10)
    )
    scores.append(
        CauseScore(
            Cause.MARKET_DATA_ANOMALY,
            md_score,
            f"stale quote fraction = {stale:.1%} (baseline {base_stale:.1%}); crossed quotes = {crossed}",
            {"stale_quote_fraction": stale, "crossed_quotes": float(crossed)},
        )
    )

    # --- large order / impact -----------------------------------------------
    size_ratio = _ratio(window.target_quantity, baseline.target_quantity if baseline else float("nan"))
    part = window.participation_rate
    large_score = 0.0
    if not math.isnan(part) and part > t.participation_rate:
        large_score = _sig(part - t.participation_rate, 0.1)
        if not math.isnan(size_ratio) and size_ratio >= t.order_size_ratio:
            large_score = _clamp(large_score + 0.3)
    scores.append(
        CauseScore(
            Cause.LARGE_ORDER_IMPACT,
            large_score,
            f"participation rate = {part:.1%} (threshold {t.participation_rate:.0%}); order size ratio vs baseline = {size_ratio:.1f}",
            {
                "participation_rate": part,
                "order_size_ratio": size_ratio,
                "market_impact_bps": window.market_impact_bps,
            },
        )
    )

    # --- unexpected price movement ------------------------------------------
    adverse_drift = window.side.sign * window_market.price_drift_bps
    jump = _f(eng.get("max_abs_move_bps"))
    move = max(adverse_drift, jump) if not math.isnan(jump) else adverse_drift
    move_score = _sig(move - t.price_move_bps, 40.0)
    if vol_ratio is not None and not math.isnan(vol_ratio) and vol_ratio > t.volatility_ratio:
        move_score *= 0.5  # a sustained high-vol regime already explains the drift
    scores.append(
        CauseScore(
            Cause.PRICE_MOVEMENT,
            move_score,
            f"adverse price drift during window = {adverse_drift:.1f} bps (threshold {t.price_move_bps})",
            {"adverse_drift_bps": adverse_drift, "max_abs_move_bps": jump},
        )
    )

    ranked = tuple(sorted(scores, key=lambda s: s.score, reverse=True))
    material = [s for s in ranked if s.score >= MATERIAL_SCORE]
    if not deteriorated and not material:
        primary = Cause.NORMAL
        secondary: tuple[Cause, ...] = ()
    elif material:
        primary = material[0].cause
        secondary = tuple(s.cause for s in material[1:])
    else:
        primary = Cause.NORMAL
        secondary = ()
    return AttributionResult(
        deteriorated=deteriorated,
        is_delta_bps=is_delta,
        vwap_slippage_delta_bps=vwap_delta,
        ranked=ranked,
        primary=primary,
        secondary=secondary,
    )


def _venue_degradation(window: ExecutionMetrics, t: Thresholds) -> tuple[float, str, dict[str, Any]]:
    stats = [v for v in window.venue_statistics.values() if v.child_orders >= 3]
    if len(stats) < 2:
        return 0.0, "insufficient venue coverage to assess degradation", {}
    worst_score = 0.0
    worst_name = ""
    metrics: dict[str, Any] = {}
    for v in stats:
        peers = [p for p in stats if p.venue != v.venue]
        finite_fill = [p.fill_rate for p in peers if not math.isnan(p.fill_rate)]
        peer_fill = sum(finite_fill) / len(finite_fill) if finite_fill else float("nan")
        peer_slip = [p.average_slippage_bps for p in peers if not math.isnan(p.average_slippage_bps)]
        peer_slip_mean = sum(peer_slip) / len(peer_slip) if peer_slip else float("nan")
        peer_reject = sum(p.reject_rate for p in peers) / len(peers)
        fill_gap = peer_fill - v.fill_rate if not (math.isnan(v.fill_rate) or math.isnan(peer_fill)) else 0.0
        slip_excess = (
            (v.average_slippage_bps - peer_slip_mean)
            if not (math.isnan(v.average_slippage_bps) or math.isnan(peer_slip_mean))
            else 0.0
        )
        reject_excess = v.reject_rate - peer_reject
        # Rejects only count against a venue when the platform as a whole is healthy; a
        # platform-wide reject rate above threshold is a technology signal, not a venue one.
        platform_rejects_elevated = window.reject_rate > t.reject_rate
        reject_score = 0.0 if platform_rejects_elevated else _sig(reject_excess - 2 * t.reject_rate, 0.1)
        score = max(
            _sig(fill_gap - t.venue_fill_rate_gap, 0.15),
            _sig(slip_excess - t.venue_slippage_excess_bps, 2.0),
            reject_score,
        )
        if score > worst_score:
            worst_score, worst_name = score, v.venue
            metrics = {
                "fill_rate_gap": fill_gap,
                "slippage_excess_bps": slip_excess,
                "reject_rate": v.reject_rate,
                "reject_excess_vs_peers": reject_excess,
                "venue_fill_rate": v.fill_rate,
                "peer_fill_rate": peer_fill,
            }
    rationale = (
        f"venue {worst_name}: fill-rate gap vs peers = {metrics.get('fill_rate_gap', 0):.2f}, "
        f"slippage excess = {metrics.get('slippage_excess_bps', 0):.1f} bps"
        if worst_name
        else "no venue materially worse than peers"
    )
    if worst_name:
        metrics["venue"] = worst_name
    return worst_score, rationale, metrics
