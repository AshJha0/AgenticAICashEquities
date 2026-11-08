"""Anomaly and change-point detection on per-minute technology and feed features (PLAN 2B.1).

In-house, no new dependency:

* **robust z** of every feature against a reference period (median and 1.4826 x MAD, with a
  per-feature scale floor so a feature that is constant in the reference, such as a reject
  count of zero, does not divide by zero);
* **two-sided CUSUM** on the robust z (Page 1954): ``S+_t = max(0, S+_{t-1} + z_t - k)`` and
  ``S-_t = max(0, S-_{t-1} - z_t - k)``; an alarm when either exceeds ``h``. The onset estimate is
  start of the PELT segment that contains the alarm when that segment moved in the alarm's
  direction, else the minute after the CUSUM last sat at zero before the alarm;
* **PELT** (Killick, Fearnhead and Eckley 2012) on the z series of an alarmed feature, L2 cost and a
  BIC-style penalty, for the segment picture: which feature changed, when, and to what level.

The alarm threshold ``h`` is the one fitted parameter. :func:`tune_threshold` picks it on
calibration seeds by minimising a stated expected cost (``FALSE_ALARM_COST`` analyst minutes per
false alarm against ``DELAY_COST_PER_MIN`` per minute of detection delay on an incident, with an
incident prior), subject to the Clopper-Pearson upper bound of the per-session false-alarm rate
being at most ``1 / MIN_ARL0``. The fitted detector is registered in :mod:`ceap.ml.registry` with
the label "fitted to synthetic generator <hash>"; held-out seeds measure ARL0 and onset accuracy.

ARL0 here is counted in sessions: the expected number of clean sessions until the first false
alarm, ``1 / p`` for a per-session false-alarm probability ``p`` over a 180-minute monitored session.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

import numpy as np

MAD_TO_SD = 1.4826
DEFAULT_K = 1.0  # CUSUM allowance: optimal for a shift of two robust sd; tames reference-estimation noise
DEFAULT_H = 10.0  # used only when no registered detector is available
REFERENCE_MINUTES = 60
MIN_ARL0 = 20.0  # sessions
FALSE_ALARM_COST = 30.0  # analyst minutes spent clearing a false alarm
DELAY_COST_PER_MIN = 10.0  # analyst-equivalent minutes lost per minute an incident goes unflagged
INCIDENT_PRIOR = 0.05  # share of sessions with a real technology incident
MISSED_DELAY_MIN = 60.0  # delay charged when an incident is never flagged


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    service: str
    metric: str
    log: bool = False
    min_scale: float = 0.0  # floor on the robust scale, in feature units (after log)


FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec("ack_latency_p50", "order-gateway", "ack_latency_p50_us", log=True, min_scale=0.03),
    FeatureSpec("ack_latency_p99", "order-gateway", "ack_latency_p99_us", log=True, min_scale=0.03),
    FeatureSpec("reject_count", "order-gateway", "reject_count", min_scale=1.0),
    FeatureSpec("route_latency", "smart-order-router", "route_latency_us", log=True, min_scale=0.03),
    FeatureSpec("router_cpu", "smart-order-router", "cpu_percent", min_scale=1.0),
    FeatureSpec("feed_gaps", "market-data-adapter", "feed_gap_count", min_scale=1.0),
    FeatureSpec("quote_rate", "market-data-adapter", "quote_rate_per_s", min_scale=1.0),
)
FEATURE_NAMES = tuple(f.name for f in FEATURES)
MARKET_DATA_FEATURES = ("feed_gaps", "quote_rate")
TECHNOLOGY_FEATURES = ("ack_latency_p50", "ack_latency_p99", "reject_count", "route_latency", "router_cpu")


# ------------------------------------------------------------------ features
@dataclass(frozen=True)
class MinuteFeatures:
    minutes: tuple[datetime, ...]
    values: dict[str, np.ndarray]  # feature name -> (n_minutes,), NaN where missing

    def __len__(self) -> int:
        return len(self.minutes)


def _point_fields(p: Any) -> tuple[datetime, str, str, float]:
    if isinstance(p, dict):
        ts = p["timestamp"]
        ts = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
        return ts, str(p.get("service", "")), str(p["metric"]), float(p["value"])
    return p.timestamp, p.service, p.metric, float(p.value)


def minute_features(points: Iterable[Any], features: Sequence[FeatureSpec] = FEATURES) -> MinuteFeatures:
    """Pivot metric points (objects with ``timestamp/service/metric/value`` or dicts) to a minute grid."""
    by_key = {(f.service, f.metric): f for f in features}
    rows: dict[datetime, dict[str, float]] = {}
    for p in points:
        ts, svc, metric, value = _point_fields(p)
        spec = by_key.get((svc, metric))
        if spec is None:
            continue
        minute = ts.replace(second=0, microsecond=0)
        rows.setdefault(minute, {})[spec.name] = math.log(max(value, 1e-9)) if spec.log else value
    minutes = tuple(sorted(rows))
    values = {f.name: np.array([rows[m].get(f.name, np.nan) for m in minutes], dtype=float) for f in features}
    return MinuteFeatures(minutes, values)


def series_features(series: dict[str, list[dict[str, Any]]], service: str) -> list[dict[str, Any]]:
    """Turn an ``engineering.get_service_metrics`` ``series`` block into metric-point dicts."""
    return [
        {"timestamp": v["timestamp"], "service": service, "metric": metric, "value": v["value"]}
        for metric, vals in series.items()
        for v in vals
    ]


# ------------------------------------------------------------------ detection primitives
def robust_reference(x: np.ndarray, min_scale: float) -> tuple[float, float]:
    r = x[np.isfinite(x)]
    if r.size == 0:
        return float("nan"), float("nan")
    med = float(np.median(r))
    scale = MAD_TO_SD * float(np.median(np.abs(r - med)))
    return med, max(scale, min_scale, 1e-12)


def cusum(z: np.ndarray, k: float = DEFAULT_K) -> tuple[np.ndarray, np.ndarray]:
    """Two-sided CUSUM paths; NaN inputs contribute nothing (the statistic carries over)."""
    up = np.zeros(z.size)
    dn = np.zeros(z.size)
    su = sd = 0.0
    for i, v in enumerate(z):
        if math.isfinite(v):
            su = max(0.0, su + v - k)
            sd = max(0.0, sd - v - k)
        up[i], dn[i] = su, sd
    return up, dn


def pelt(z: np.ndarray, penalty: float | None = None, min_size: int = 2) -> list[tuple[int, int, float]]:
    """PELT change points for a piecewise-constant mean with unit variance: ``[(start, end, mean)]``.

    Cost of a segment is ``sum (z - mean)^2``; ``penalty`` defaults to ``2 ln n`` (BIC for one mean
    parameter plus the change point). Pruning keeps candidate ``s`` only while
    ``F(s) + C(s, t) <= F(t)``, which is exact for this cost (Killick et al. 2012, condition with K = 0).
    """
    x = np.where(np.isfinite(z), z, 0.0)
    n = x.size
    if n == 0:
        return []
    beta = 2.0 * math.log(max(n, 2)) if penalty is None else penalty
    c1 = np.concatenate([[0.0], np.cumsum(x)])
    c2 = np.concatenate([[0.0], np.cumsum(x * x)])

    f = np.full(n + 1, np.inf)
    f[0] = -beta
    last = np.zeros(n + 1, dtype=int)
    cands = np.array([0])
    for t in range(min_size, n + 1):
        m = t - cands
        ok = m >= min_size
        cost = np.full(cands.size, np.inf)
        cost[ok] = c2[t] - c2[cands[ok]] - (c1[t] - c1[cands[ok]]) ** 2 / m[ok]
        total = f[cands] + cost
        j = int(np.argmin(total + beta))
        f[t], last[t] = total[j] + beta, int(cands[j])
        keep = ~ok | (total <= f[t])  # PELT pruning, exact for this cost with K = 0
        cands = np.append(cands[keep], t - min_size + 1)  # a valid segment start from t + 1
    bounds = []
    t = n
    while t > 0:
        s = int(last[t])
        bounds.append((s, t, float((c1[t] - c1[s]) / (t - s))))
        t = s
    return bounds[::-1]


# ------------------------------------------------------------------ the detector
@dataclass(frozen=True)
class Alarm:
    feature: str
    service: str
    direction: str  # "up" or "down"
    alarm_index: int
    onset_index: int
    alarm_at: datetime
    onset_at: datetime
    peak_z: float
    reference_median: float
    post_median: float
    log_scale: bool
    segments: tuple[tuple[str, str, float], ...] = ()

    def describe(self) -> str:
        def lvl(v: float) -> str:
            return f"{math.exp(v):.0f}" if self.log_scale else f"{v:.1f}"

        return (
            f"{self.service} {self.feature} shifted {self.direction} from about {lvl(self.reference_median)} to "
            f"{lvl(self.post_median)} (peak robust z {self.peak_z:.1f}); change onset "
            f"{self.onset_at:%H:%M}, alarm {self.alarm_at:%H:%M}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "service": self.service,
            "direction": self.direction,
            "onset_at": self.onset_at.isoformat(),
            "alarm_at": self.alarm_at.isoformat(),
            "delay_minutes": self.alarm_index - self.onset_index,
            "peak_z": round(self.peak_z, 4),
            "reference_median": self.reference_median,
            "post_median": self.post_median,
            "segments": [list(s) for s in self.segments],
        }


@dataclass(frozen=True)
class DetectionReport:
    alarms: tuple[Alarm, ...]
    threshold: float
    k: float
    reference_minutes: int
    monitored_minutes: int
    peak_statistic: float  # max CUSUM over every feature and side, in the monitored period
    score: np.ndarray = field(repr=False)  # per-minute max CUSUM / h (>= 1 means alarm)
    model_hash: str | None = None
    label: str | None = None

    @property
    def first(self) -> Alarm | None:
        return min(self.alarms, key=lambda a: (a.alarm_index, a.onset_index)) if self.alarms else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "alarms": [a.to_dict() for a in sorted(self.alarms, key=lambda a: a.alarm_index)],
            "threshold": self.threshold,
            "k": self.k,
            "reference_minutes": self.reference_minutes,
            "monitored_minutes": self.monitored_minutes,
            "peak_statistic": round(self.peak_statistic, 6),
            "model_hash": self.model_hash,
            "label": self.label,
        }


@dataclass(frozen=True)
class ChangeDetector:
    h: float = DEFAULT_H
    k: float = DEFAULT_K
    reference_minutes: int = REFERENCE_MINUTES
    features: tuple[FeatureSpec, ...] = FEATURES
    model_hash: str | None = None
    label: str | None = None

    def params(self) -> dict[str, Any]:
        return {
            "h": self.h,
            "k": self.k,
            "reference_minutes": self.reference_minutes,
            "features": [[f.name, f.service, f.metric, f.log, f.min_scale] for f in self.features],
        }

    @classmethod
    def from_params(cls, params: dict[str, Any], model_hash: str | None = None, label: str | None = None) -> ChangeDetector:
        feats = tuple(FeatureSpec(n, s, m, bool(lg), float(ms)) for n, s, m, lg, ms in params["features"])
        return cls(float(params["h"]), float(params["k"]), int(params["reference_minutes"]), feats, model_hash, label)

    def statistics(self, mf: MinuteFeatures) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, float, float]]:
        """Per feature: ``(z, cusum_up, cusum_down, reference_median, scale)`` over the monitored minutes."""
        r = self.reference_minutes
        out = {}
        for f in self.features:
            x = mf.values.get(f.name)
            if x is None or x.size <= r or not np.isfinite(x[:r]).any():
                continue
            med, scale = robust_reference(x[:r], f.min_scale)
            z = (x[r:] - med) / scale
            up, dn = cusum(z, self.k)
            out[f.name] = (z, up, dn, med, scale)
        return out

    def detect(self, mf: MinuteFeatures, only: Sequence[str] | None = None) -> DetectionReport:
        r = self.reference_minutes
        n_mon = max(0, len(mf) - r)
        stats = self.statistics(mf)
        specs = {f.name: f for f in self.features}
        alarms: list[Alarm] = []
        score = np.zeros(n_mon)
        peak = 0.0
        for name, (z, up, dn, med, _scale) in stats.items():
            if only is not None and name not in only:
                continue
            score = np.maximum(score, np.maximum(up, dn) / self.h)
            peak = max(peak, float(up.max(initial=0.0)), float(dn.max(initial=0.0)))
            segs: list[tuple[int, int, float]] | None = None
            for direction, path in (("up", up), ("down", dn)):
                hits = np.flatnonzero(path > self.h)
                if hits.size == 0:
                    continue
                if segs is None:
                    segs = pelt(z)
                a = int(hits[0])
                zeros = np.flatnonzero(path[: a + 1] == 0.0)
                onset = int(zeros[-1]) + 1 if zeros.size else 0
                sign = 1.0 if direction == "up" else -1.0
                for s0, e0, mu in segs:
                    if s0 <= a < e0 and sign * mu > self.k:
                        onset = s0
                        break
                post = mf.values[name][r + onset : r + a + 1]
                alarms.append(
                    Alarm(
                        feature=name,
                        service=specs[name].service,
                        direction=direction,
                        alarm_index=a,
                        onset_index=onset,
                        alarm_at=mf.minutes[r + a],
                        onset_at=mf.minutes[r + onset],
                        peak_z=float(np.nanmax(np.abs(z[onset : a + 1]))),
                        reference_median=med,
                        post_median=float(np.nanmedian(post)) if np.isfinite(post).any() else float("nan"),
                        log_scale=specs[name].log,
                        segments=tuple(
                            (mf.minutes[r + s].isoformat(), mf.minutes[r + e - 1].isoformat(), round(mu, 3))
                            for s, e, mu in segs
                        ),
                    )
                )
        return DetectionReport(
            tuple(alarms), self.h, self.k, r, n_mon, peak, score, self.model_hash, self.label
        )


def anomaly_score(points: Iterable[Any], detector: ChangeDetector | None = None) -> float:
    """Peak normalised CUSUM over the monitored minutes (``>= 1`` means an alarm). For TCA consumers."""
    det = detector or load_detector()
    rep = det.detect(minute_features(points, det.features))
    return float(rep.score.max(initial=0.0))


def alarm_confidence(report: DetectionReport, floor: float = 0.55, cap: float = 0.95) -> float:
    """Raw (uncalibrated) confidence of an alarm from its CUSUM excursion beyond ``h``.

    Same shape as ``agents.base.confidence_from_excess`` with excess ``ln(max score)`` (the CUSUM
    peak in multiples of ``h``, on a log scale because a sustained shift grows the CUSUM without
    bound) and scale 1: ``floor + (cap - floor)(1 - exp(-excess))``. Calibrated against outcomes in 2B.4.
    """
    peak = float(report.score.max(initial=0.0))
    excess = math.log(peak) if peak > 0 else float("nan")
    if not math.isfinite(excess) or excess <= 0:
        return floor
    return min(cap, floor + (cap - floor) * (1 - math.exp(-excess)))


def attribute_deployment(
    alarm: Alarm, deployments: Iterable[dict[str, Any]], lookback: timedelta = timedelta(minutes=30)
) -> dict[str, Any] | None:
    """The most recent deployment within ``lookback`` before the change onset, if any."""
    best = None
    for d in deployments:
        ts = d["timestamp"]
        ts = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
        if alarm.onset_at - lookback <= ts <= alarm.onset_at and (best is None or ts > best[0]):
            best = (ts, d)
    return None if best is None else best[1]


# ------------------------------------------------------------------ synthetic sessions and fitting
def synthetic_session(
    seed: int,
    latency_multiplier: float = 1.0,
    reject_rate: float = 0.0,
    stale_quote_fraction: float = 0.0,
    deployment: bool = False,
) -> tuple[MinuteFeatures, int]:
    """Per-minute features of one synthetic session, and the true change minute index.

    Uses the scenario generator's own engineering-metric law (``SyntheticMarketGenerator._engineering``)
    without simulating quotes and orders, which is 100x faster and draws the same per-minute metric
    distributions; order rejects are therefore absent. The planted regime starts at the window start
    (14:00, minute 120); the T06 deployment record sits at 13:52.
    """
    from ceap.data.scenarios import get_scenario
    from ceap.data.synthetic import SyntheticMarketGenerator, london

    spec = replace(
        get_scenario("T06"),
        seed=seed,
        latency_multiplier=latency_multiplier,
        reject_rate=reject_rate,
        stale_quote_fraction=stale_quote_fraction,
        deployment_in_window=deployment,
    )
    gen = SyntheticMarketGenerator()
    sim_start = london(gen.session_date, gen.sim_hours[0])
    n = (gen.sim_hours[1] - gen.sim_hours[0]) * 3600
    ws = london(gen.session_date, 14).timestamp() - sim_start.timestamp()
    sec = np.arange(n)
    in_window = (sec >= ws) & (sec < ws + 3600)
    rng = np.random.default_rng(seed)
    metrics, _, _ = gen._engineering(rng, spec, spec.symbol, sim_start, n, in_window, [])
    return minute_features(metrics), int(ws // 60)


@dataclass(frozen=True)
class ThresholdChoice:
    h: float
    false_alarms: int
    sessions: int
    false_alarm_rate: float
    false_alarm_upper: float
    mean_delay_min: float
    expected_cost: float
    table: list[dict[str, float]]


def tune_threshold(
    clean_peaks: Sequence[float],
    incident_delays: dict[float, Sequence[float]],
    grid: Sequence[float],
) -> ThresholdChoice:
    """Lowest expected cost per session over ``grid`` subject to CP upper bound ``p_fa <= 1 / MIN_ARL0``.

    ``clean_peaks``: peak CUSUM per clean calibration session. ``incident_delays[h]``: detection
    delay in minutes per incident session at threshold ``h`` (``inf`` when never flagged).
    """
    from ceap.analytics.null_sweep import clopper_pearson

    n = len(clean_peaks)
    peaks = np.asarray(clean_peaks, dtype=float)
    rows = []
    best: ThresholdChoice | None = None
    for h in grid:
        k = int((peaks > h).sum())
        _, upper = clopper_pearson(k, n)
        d = np.asarray(incident_delays[h], dtype=float)
        d = np.where(np.isfinite(d), d, MISSED_DELAY_MIN)
        cost = (1 - INCIDENT_PRIOR) * (k / n) * FALSE_ALARM_COST + INCIDENT_PRIOR * float(d.mean()) * DELAY_COST_PER_MIN
        row = {"h": float(h), "false_alarms": k, "rate": k / n, "upper": upper, "mean_delay": float(d.mean()), "cost": cost}
        rows.append(row)
        if upper <= 1.0 / MIN_ARL0 and (best is None or cost < best.expected_cost):
            best = ThresholdChoice(float(h), k, n, k / n, upper, float(d.mean()), cost, rows)
    if best is None:
        raise ValueError("no threshold on the grid meets the ARL0 constraint")
    return replace(best, table=rows)


DETECTOR_NAME = "change_detector"


def load_detector(registry: Any = None) -> ChangeDetector:
    """The registered detector (by its only stored version), else the unfitted default."""
    from ceap.ml.registry import ModelRegistry

    reg = registry or ModelRegistry()
    try:
        m = reg.load(DETECTOR_NAME)
    except (LookupError, OSError):
        return ChangeDetector()
    return ChangeDetector.from_params(m.params, m.model_hash, m.card.label)
