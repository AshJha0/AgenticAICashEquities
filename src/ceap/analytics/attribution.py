"""Deterministic root-cause attribution.

Compares the investigation window against a baseline window and scores a
fixed catalogue of candidate causes. The scores are *evidence-derived*;
the LLM only narrates them. Thresholds are documented in
``docs/evaluation/evaluation.md`` and can be tuned via :class:`Thresholds`.

F8 (0.8.0) adds :func:`regression_attribution`, a cross-sectional regression of the IS residual on
standardised regressors that gives each cause a bps share with a bootstrap CI. It sits next to the
heuristic scores and does not replace them yet: it runs only when asked for
(``attribute_causes(..., method="regression")`` or ``CEAP_ATTRIBUTION=regression`` in the
environment), and its output goes in the extra ``regression`` key. See ``docs/execution/execution.md``.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

from ceap.analytics.numerics import negligible_variation
from ceap.domain.execution import ExecutionMetrics
from ceap.domain.market import MarketStatistics

MATERIAL_SCORE = 0.35  # a cause with score >= this is reported as material
ATTRIBUTION_ENV = "CEAP_ATTRIBUTION"  # "regression" adds the F8 regression next to the scores


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
    # legacy fallback only (no pre-trade estimate available): min IS increase vs the baseline
    deterioration_bps: float = 1.5
    # 2T.4: deteriorated when the IS residual against the pre-trade estimate exceeds this many
    # standard errors (one-sided; 1.96 = 2.5 % false-flag rate if the SE is right)
    deterioration_z: float = 1.96
    # prior weight (in pseudo-observations) of the model's execution noise when pooling it with
    # the dispersion of matched peers
    peer_noise_prior: float = 4.0


@dataclass(frozen=True)
class CauseScore:
    cause: Cause
    score: float  # 0..1
    rationale: str
    metrics: dict[str, Any] = field(default_factory=dict)
    #: peer-relative effect of the cause's defining metric (2T.6): effect, CI, standardised effect
    effect: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PeerWindow:
    """One matched-baseline observation: the same clock window on a previous session (2T.2)."""

    metrics: ExecutionMetrics
    market: MarketStatistics


@dataclass(frozen=True)
class AttributionResult:
    deteriorated: bool
    is_delta_bps: float
    vwap_slippage_delta_bps: float
    ranked: tuple[CauseScore, ...]
    primary: Cause
    secondary: tuple[Cause, ...]
    basis: str = "legacy_delta"  # "pretrade_residual" since 2T.4 when an estimate exists
    residual_bps: float = float("nan")
    residual_se_bps: float = float("nan")
    residual_z: float = float("nan")
    baseline_kind: str = "same_day"  # "time_of_day_matched" with peer windows (2T.2)
    decomposition: dict[str, Any] = field(default_factory=dict)
    peer_note: str = ""
    #: F8 regression attribution; empty unless requested (keeps v0.7 outputs byte-identical)
    regression: dict[str, Any] = field(default_factory=dict)

    @property
    def material(self) -> tuple[CauseScore, ...]:
        return tuple(c for c in self.ranked if c.score >= MATERIAL_SCORE)

    def to_dict(self) -> dict[str, Any]:
        out = self._base_dict()
        if self.regression:
            out["regression"] = self.regression
        return out

    def _base_dict(self) -> dict[str, Any]:
        return {
            "deteriorated": self.deteriorated,
            "is_delta_bps": self.is_delta_bps,
            "vwap_slippage_delta_bps": self.vwap_slippage_delta_bps,
            "basis": self.basis,
            "residual_bps": self.residual_bps,
            "residual_se_bps": self.residual_se_bps,
            "residual_z": self.residual_z,
            "baseline_kind": self.baseline_kind,
            "decomposition": self.decomposition,
            "peer_note": self.peer_note,
            "primary": self.primary.value,
            "secondary": [c.value for c in self.secondary],
            "ranked": [
                {
                    "cause": c.cause.value,
                    "score": c.score,
                    "rationale": c.rationale,
                    "metrics": c.metrics,
                    "effect": c.effect,
                }
                for c in self.ranked
            ],
        }


#: IS component each cause works through, for the bps decomposition next to the scores
CAUSE_COMPONENTS: dict[Cause, tuple[str, ...]] = {
    Cause.MARKET_VOLATILITY: ("timing_cost_bps",),
    Cause.WIDE_SPREADS: ("spread_cost_bps",),
    Cause.LOW_LIQUIDITY: ("spread_cost_bps",),
    Cause.VENUE_DEGRADATION: ("spread_cost_bps", "opportunity_cost_bps"),
    Cause.TECHNOLOGY_LATENCY: ("spread_cost_bps", "opportunity_cost_bps"),
    Cause.MARKET_DATA_ANOMALY: (),
    Cause.LARGE_ORDER_IMPACT: ("timing_cost_bps",),
    Cause.PRICE_MOVEMENT: ("timing_cost_bps",),
}
COMPONENT_FIELDS = (
    "delay_cost_bps",
    "spread_cost_bps",
    "timing_cost_bps",
    "opportunity_cost_bps",
    "fees_bps",
)


def _cause_metric(cause: Cause, m: ExecutionMetrics, mkt: MarketStatistics) -> float:
    """The defining metric of each cause, used for the peer-relative effect size."""
    if cause is Cause.MARKET_VOLATILITY:
        return _f(mkt.realised_volatility_bps)
    if cause is Cause.WIDE_SPREADS:
        return _f(mkt.average_spread_bps)
    if cause is Cause.LOW_LIQUIDITY:
        return (
            _f(mkt.average_displayed_depth)
            if not math.isnan(_f(mkt.average_displayed_depth))
            else _f(mkt.average_top_of_book_size)
        )
    if cause is Cause.VENUE_DEGRADATION:
        rates = [
            v.fill_rate
            for v in m.venue_statistics.values()
            if v.child_orders >= 3 and not math.isnan(v.fill_rate)
        ]
        return min(rates) if rates else float("nan")
    if cause is Cause.TECHNOLOGY_LATENCY:
        return _f(m.average_latency_us)
    if cause is Cause.MARKET_DATA_ANOMALY:
        return _f(mkt.stale_quote_fraction) + _f(mkt.crossed_quote_count) / max(
            1.0, _f(mkt.quote_count) or 1.0
        )
    if cause is Cause.LARGE_ORDER_IMPACT:
        return _f(m.participation_rate)
    return m.side.sign * _f(mkt.price_drift_bps)


def pool_metrics(items: list[ExecutionMetrics]) -> ExecutionMetrics | None:
    """Mean of the numeric fields over matched windows (the pooled time-of-day baseline)."""
    if not items:
        return None
    from dataclasses import fields, replace

    out: dict[str, Any] = {}
    for f in fields(items[0]):
        vals = [getattr(x, f.name) for x in items]
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            finite = [float(v) for v in vals if not math.isnan(float(v))]
            mean = sum(finite) / len(finite) if finite else float("nan")
            out[f.name] = int(round(mean)) if isinstance(vals[0], int) and finite else mean
    return replace(items[0], **out)


def pool_market(items: list[MarketStatistics]) -> MarketStatistics | None:
    if not items:
        return None
    from dataclasses import fields, replace

    out: dict[str, Any] = {}
    for f in fields(items[0]):
        vals = [getattr(x, f.name) for x in items]
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            finite = [float(v) for v in vals if not math.isnan(float(v))]
            mean = sum(finite) / len(finite) if finite else float("nan")
            out[f.name] = int(round(mean)) if isinstance(vals[0], int) and finite else mean
    return replace(items[0], **out)


def residual_standard_error(window: ExecutionMetrics, peers: list[PeerWindow], prior_weight: float) -> float:
    """SE of the window's IS residual: analytic price risk plus pooled execution noise (2T.4).

    ``SE^2 = sigma^2 T/3 + s_exec^2``. The model supplies ``s_exec`` (its calibrated non-price
    dispersion); matched peers update it: ``s_exec^2 = (k s_model^2 + (n-1) s_peer^2) / (k + n - 1)``
    where ``s_peer^2`` is the peer residual variance net of each peer's own price risk.
    """
    se_model = _f(window.is_standard_error_bps)
    pr = _f(window.is_price_risk_bps)
    if math.isnan(se_model) or math.isnan(pr):
        return se_model
    s_model2 = max(se_model**2 - pr**2, 0.0)
    res = [(_f(p.metrics.is_residual_bps), _f(p.metrics.is_price_risk_bps)) for p in peers]
    res = [(r, q) for r, q in res if not (math.isnan(r) or math.isnan(q))]
    n = len(res)
    if n >= 2:
        mean = sum(r for r, _ in res) / n
        var = sum((r - mean) ** 2 for r, _ in res) / (n - 1)
        s_peer2 = max(var - sum(q * q for _, q in res) / n, 0.0)
        s_exec2 = (prior_weight * s_model2 + (n - 1) * s_peer2) / (prior_weight + n - 1)
    else:
        s_exec2 = s_model2
    return math.sqrt(pr**2 + s_exec2)


def decompose(window: ExecutionMetrics, peers: list[PeerWindow]) -> dict[str, Any]:
    """Window IS in bps by Perold component, each next to its matched-peer mean and effect size."""
    from ceap.analytics.peers import peer_effect

    comps: dict[str, Any] = {}
    for k, name in enumerate(COMPONENT_FIELDS):
        v = _f(getattr(window, name))
        eff = peer_effect(v, [_f(getattr(p.metrics, name)) for p in peers], seed=k)
        comps[name] = {"bps": v, **eff.as_dict()}
    comps["total_bps"] = _f(window.implementation_shortfall_bps)
    comps["pretrade_estimate_bps"] = _f(window.pretrade_estimate_bps)
    comps["arrival_shortfall_bps"] = _f(window.arrival_shortfall_bps)
    return comps


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
    peers: list[PeerWindow] | None = None,
    method: str | None = None,
) -> AttributionResult:
    """Score candidate causes for the execution-quality change in ``window``.

    With ``peers`` (the same clock window on previous sessions, 2T.2) the baseline is their pooled
    mean, so intraday seasonality cannot masquerade as a cause; every cause also gets a
    peer-relative effect with a bootstrap CI (2T.6). Deterioration is the IS residual against the
    pre-trade estimate in standard errors (2T.4); the v0.4 1.5 bps delta remains only as the
    fallback when no estimate is available.

    ``method="regression"`` (or ``CEAP_ATTRIBUTION=regression``) also runs the F8 regression over
    the peers and returns it in ``regression``; the heuristic scores and verdict are unchanged.
    """
    method = (method or os.environ.get(ATTRIBUTION_ENV, "heuristic")).strip().lower()
    t = thresholds
    eng = engineering or {}
    peers = [p for p in (peers or []) if p.metrics.execution_count > 0]
    if peers:
        baseline = pool_metrics([p.metrics for p in peers]) or baseline
        baseline_market = pool_market([p.market for p in peers]) or baseline_market
    scores: list[CauseScore] = []

    base_is = baseline.implementation_shortfall_bps if baseline else 0.0
    is_delta = window.implementation_shortfall_bps - (base_is if not math.isnan(base_is) else 0.0)
    base_vs = baseline.slippage_vs_vwap_bps if baseline else 0.0
    vwap_delta = window.slippage_vs_vwap_bps - (base_vs if not math.isnan(base_vs) else 0.0)
    residual = _f(window.is_residual_bps)
    se = residual_standard_error(window, peers, t.peer_noise_prior)
    z = residual / se if not (math.isnan(residual) or math.isnan(se)) and se > 0 else float("nan")
    if not math.isnan(z):
        basis = "pretrade_residual"
        deteriorated = z >= t.deterioration_z
    else:
        # legacy: a VWAP algo judged against interval VWAP (drift-neutral) as well as arrival (IS)
        basis = "legacy_delta"
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

    if peers:
        from dataclasses import replace as _replace

        from ceap.analytics.peers import peer_effect

        with_effects = []
        for k, cs in enumerate(scores):
            value = _cause_metric(cs.cause, window, window_market)
            eff = peer_effect(
                value, [_cause_metric(cs.cause, p.metrics, p.market) for p in peers], seed=100 + k
            )
            comp_bps = sum(_f(getattr(window, c)) for c in CAUSE_COMPONENTS.get(cs.cause, ()))
            with_effects.append(
                _replace(
                    cs,
                    effect={
                        **eff.as_dict(),
                        "components": list(CAUSE_COMPONENTS.get(cs.cause, ())),
                        "component_bps": comp_bps,
                    },
                )
            )
        scores = with_effects

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
        basis=basis,
        residual_bps=residual,
        residual_se_bps=se,
        residual_z=z,
        baseline_kind="time_of_day_matched" if peers else "same_day",
        decomposition=decompose(window, peers) if not math.isnan(_f(window.delay_cost_bps)) else {},
        peer_note=_peer_note() if peers else "",
        regression=regression_attribution(window, window_market, peers).to_dict()
        if method == "regression" and peers
        else {},
    )


def _peer_note() -> str:
    from ceap.analytics.peers import NO_CAUSAL_CLAIM

    return NO_CAUSAL_CLAIM


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


# --------------------------------------------------------------------------- F8 regression
def _neg_log_depth(m: ExecutionMetrics, mkt: MarketStatistics) -> float:
    d = _cause_metric(Cause.LOW_LIQUIDITY, m, mkt)
    return -math.log(d) if d > 0 and math.isfinite(d) else float("nan")


def _metric_of(cause: Cause) -> Callable[[ExecutionMetrics, MarketStatistics], float]:
    return lambda m, k: _cause_metric(cause, m, k)


#: one regressor per cause, signed so that a larger value is expected to raise the cost
REGRESSORS: dict[Cause, Callable[[ExecutionMetrics, MarketStatistics], float]] = {
    Cause.MARKET_VOLATILITY: _metric_of(Cause.MARKET_VOLATILITY),
    Cause.WIDE_SPREADS: _metric_of(Cause.WIDE_SPREADS),
    Cause.LOW_LIQUIDITY: _neg_log_depth,
    Cause.TECHNOLOGY_LATENCY: _metric_of(Cause.TECHNOLOGY_LATENCY),
    Cause.LARGE_ORDER_IMPACT: _metric_of(Cause.LARGE_ORDER_IMPACT),
    Cause.PRICE_MOVEMENT: _metric_of(Cause.PRICE_MOVEMENT),
}
#: the cost explained, in order of preference (the first one finite for the window is used)
TARGETS = ("is_residual_bps", "arrival_shortfall_bps", "implementation_shortfall_bps")


@dataclass(frozen=True)
class RegressionAttribution:
    target: str
    excess_bps: float  # window value minus the observations' mean: what is being explained
    explained_bps: float
    unexplained_bps: float
    n_obs: int
    ridge: float
    shares: dict[str, dict[str, float]]
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": "ridge_regression",
            "target": self.target,
            "excess_bps": self.excess_bps,
            "explained_bps": self.explained_bps,
            "unexplained_bps": self.unexplained_bps,
            "n_obs": self.n_obs,
            "ridge": self.ridge,
            "shares": self.shares,
            "note": self.note,
        }


def _ridge_contributions(
    x: np.ndarray, y: np.ndarray, xw: np.ndarray, ridge: float
) -> tuple[np.ndarray, np.ndarray, float]:
    mu, sd = x.mean(axis=0), x.std(axis=0, ddof=1)
    flat = np.array([negligible_variation(x[:, k]) for k in range(x.shape[1])], dtype=bool)
    sd = np.where(flat, 1.0, sd)
    z = np.where(flat, 0.0, (x - mu) / sd)
    yc = y - y.mean()
    beta = np.linalg.solve(z.T @ z + ridge * np.eye(z.shape[1]), z.T @ yc)
    return beta, beta * (xw - mu) / sd, float(y.mean())


def regression_attribution(
    window: ExecutionMetrics,
    window_market: MarketStatistics,
    observations: list[PeerWindow],
    ridge: float = 1.0,
    n_boot: int = 500,
    confidence: float = 0.90,
    seed: int = 0,
) -> RegressionAttribution:
    """Each cause's bps share of the window's excess cost, from a cross-sectional ridge regression.

    Across ``observations`` (matched peer windows, or any comparable set of parent windows) the
    cost ``y`` - the IS residual against the pre-trade estimate when available, else arrival or
    total shortfall - is regressed on one standardised regressor per cause (:data:`REGRESSORS`).
    The window's share for cause ``k`` is ``beta_k * z_k``: the fitted effect, in bps, of how far
    the window sits from the observations' mean on that regressor. The shares plus the unexplained
    part sum to the window's excess over the mean. CIs resample the observations (percentile
    bootstrap, deterministic in ``seed``). The ridge penalty, in standardised units, keeps a small
    cross-section stable; with fewer than four observations no shares are given. Evidence class:
    standard linear cost attribution (Kissell (2013), *The Science of Algorithmic Trading and
    Portfolio Management*, ch. 6), not validated on real desk data here.
    """
    obs = [o for o in observations if o.metrics.execution_count > 0]
    target = next((t for t in TARGETS if math.isfinite(_f(getattr(window, t)))), TARGETS[-1])
    causes = list(REGRESSORS)
    rows: list[tuple[float, list[float]]] = []
    for o in obs:
        yv = _f(getattr(o.metrics, target))
        if math.isfinite(yv):
            rows.append((yv, [_f(REGRESSORS[c](o.metrics, o.market)) for c in causes]))
    xw = np.array([_f(REGRESSORS[c](window, window_market)) for c in causes])
    nan = float("nan")
    yw = _f(getattr(window, target))
    if len(rows) < 4 or not math.isfinite(yw):
        return RegressionAttribution(
            target, nan, nan, nan, len(rows), ridge, {}, "too few comparable observations"
        )
    y = np.array([r[0] for r in rows])
    x = np.array([r[1] for r in rows], dtype=float)
    keep = [
        j
        for j in range(len(causes))
        if np.isfinite(x[:, j]).all() and math.isfinite(xw[j]) and x[:, j].std() > 0
    ]
    if not keep:
        return RegressionAttribution(
            target, yw - float(y.mean()), nan, nan, len(rows), ridge, {}, "no regressor varies"
        )
    x, xk = x[:, keep], xw[keep]
    beta, contrib, ybar = _ridge_contributions(x, y, xk, ridge)
    rng = np.random.default_rng(seed)
    boots = [
        _ridge_contributions(x[idx], y[idx], xk, ridge)[1]
        for idx in rng.integers(0, len(rows), size=(n_boot, len(rows)))
    ]
    b = np.array(boots)
    a = (1.0 - confidence) / 2.0
    lo, hi = np.quantile(b, a, axis=0), np.quantile(b, 1.0 - a, axis=0)
    excess = yw - ybar
    shares = {
        causes[j].value: {
            "bps": float(contrib[i]),
            "ci_low_bps": float(lo[i]),
            "ci_high_bps": float(hi[i]),
            "beta_bps_per_sd": float(beta[i]),
        }
        for i, j in enumerate(keep)
    }
    explained = float(contrib.sum())
    note = (
        ""
        if len(rows) > len(keep) + 2
        else "fewer observations than regressors + 3: shares lean on the ridge prior"
    )
    return RegressionAttribution(
        target, float(excess), explained, float(excess - explained), len(rows), ridge, shares, note
    )
