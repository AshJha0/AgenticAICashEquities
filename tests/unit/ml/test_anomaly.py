"""2B.1: robust z + CUSUM + PELT change-point detection."""

from __future__ import annotations

import itertools
import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from ceap.analytics.null_sweep import clopper_pearson
from ceap.ml.anomaly import (
    MIN_ARL0,
    TECHNOLOGY_FEATURES,
    ChangeDetector,
    FeatureSpec,
    MinuteFeatures,
    anomaly_score,
    attribute_deployment,
    cusum,
    load_detector,
    minute_features,
    pelt,
    robust_reference,
    synthetic_session,
    tune_threshold,
)

T0 = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)


def _mf(values: dict[str, np.ndarray]) -> MinuteFeatures:
    n = len(next(iter(values.values())))
    return MinuteFeatures(tuple(T0 + timedelta(minutes=i) for i in range(n)), values)


def test_cusum_matches_hand_recursion():
    z = np.array([0.0, 2.0, 2.0, -3.0, np.nan, 1.5])
    up, dn = cusum(z, k=1.0)
    assert up.tolist() == [0.0, 1.0, 2.0, 0.0, 0.0, 0.5]
    assert dn.tolist() == [0.0, 0.0, 0.0, 2.0, 2.0, 0.0]


def _optimal_partition(z: np.ndarray, beta: float, min_size: int) -> float:
    """Brute force over every segmentation: min sum(cost) + beta * (#segments - 1)."""
    n = z.size
    best = math.inf
    for k in range(0, n):
        for cuts in itertools.combinations(range(min_size, n - min_size + 1), k):
            b = (0, *cuts, n)
            if any(e - s < min_size for s, e in itertools.pairwise(b)):
                continue
            cost = sum(float(((z[s:e] - z[s:e].mean()) ** 2).sum()) for s, e in itertools.pairwise(b))
            best = min(best, cost + beta * k)
    return best


@pytest.mark.parametrize("seed", range(6))
def test_pelt_equals_brute_force_optimal_partition(seed):
    rng = np.random.default_rng(seed)
    z = np.concatenate([rng.normal(0, 1, 5), rng.normal(3, 1, 4), rng.normal(-1, 1, 4)])
    beta = 2 * math.log(z.size)
    segs = pelt(z, beta, min_size=2)
    cost = sum(float(((z[s:e] - z[s:e].mean()) ** 2).sum()) for s, e, _ in segs) + beta * (len(segs) - 1)
    assert cost == pytest.approx(_optimal_partition(z, beta, 2), abs=1e-9)
    assert segs[0][0] == 0 and segs[-1][1] == z.size


def test_robust_reference_floors_a_constant_feature():
    med, scale = robust_reference(np.zeros(60), min_scale=1.0)
    assert med == 0.0 and scale == 1.0


def test_planted_step_is_timed_exactly_and_described():
    rng = np.random.default_rng(1)
    x = np.log(200.0) + rng.normal(0, 0.1, 240)
    x[150:] += math.log(3.0)
    det = ChangeDetector(h=10.0, features=(FeatureSpec("ack_latency_p50", "order-gateway", "m", log=True, min_scale=0.03),))
    rep = det.detect(_mf({"ack_latency_p50": x}))
    a = rep.first
    assert a is not None and a.direction == "up"
    assert a.onset_index + det.reference_minutes == 150
    assert a.alarm_index - a.onset_index <= 1
    assert "ack_latency_p50 shifted up" in a.describe() and "14:30" in a.describe()
    assert rep.to_dict()["alarms"][0]["delay_minutes"] == a.alarm_index - a.onset_index


def test_clean_series_gives_no_alarm_and_score_below_one():
    rng = np.random.default_rng(2)
    det = ChangeDetector(h=10.0, features=(FeatureSpec("f", "s", "m", min_scale=0.0),))
    rep = det.detect(_mf({"f": rng.normal(0, 1, 240)}))
    assert rep.alarms == () and rep.score.max() < 1.0


def test_minute_features_pivots_points_and_dicts():
    pts = [
        {"timestamp": (T0 + timedelta(seconds=5)).isoformat(), "service": "order-gateway", "metric": "ack_latency_p50_us", "value": 100.0},
        {"timestamp": (T0 + timedelta(minutes=1)).isoformat(), "service": "order-gateway", "metric": "reject_count", "value": 2.0},
        {"timestamp": T0.isoformat(), "service": "other", "metric": "ignored", "value": 1.0},
    ]
    mf = minute_features(pts)
    assert len(mf) == 2
    assert mf.values["ack_latency_p50"][0] == pytest.approx(math.log(100.0))
    assert math.isnan(mf.values["ack_latency_p50"][1]) and mf.values["reject_count"][1] == 2.0


def test_attribute_deployment_picks_latest_within_lookback():
    rng = np.random.default_rng(3)
    x = rng.normal(0, 1, 200)
    x[120:] += 8
    rep = ChangeDetector(features=(FeatureSpec("f", "s", "m"),)).detect(_mf({"f": x}))
    a = rep.first
    assert a is not None
    deps = [
        {"timestamp": (a.onset_at - timedelta(minutes=40)).isoformat(), "change_id": "OLD"},
        {"timestamp": (a.onset_at - timedelta(minutes=8)).isoformat(), "change_id": "CHG"},
        {"timestamp": (a.onset_at + timedelta(minutes=1)).isoformat(), "change_id": "AFTER"},
    ]
    assert attribute_deployment(a, deps)["change_id"] == "CHG"


def test_tune_threshold_respects_arl0_constraint_and_minimises_cost():
    clean = [5.0] * 95 + [9.0] * 5  # 5 % exceed h < 9
    delays = {h: [1.0 + 0.1 * h] * 10 for h in (6.0, 9.0, 12.0)}
    choice = tune_threshold(clean, delays, (6.0, 9.0, 12.0))
    _, upper6 = clopper_pearson(5, 100)
    assert upper6 > 1 / MIN_ARL0 and choice.h == 9.0  # 6 fails the bound, 9 is the cheapest that passes
    with pytest.raises(ValueError):
        tune_threshold([50.0] * 10, {1.0: [0.0]}, (1.0,))


def test_registered_detector_is_loaded_with_label():
    det = load_detector()
    assert det.model_hash is not None and det.label.startswith("fitted to synthetic generator ")
    assert 6.0 <= det.h <= 20.0


def test_full_generator_t06_dataset_matches_the_fast_session(dataset_t06):
    det = load_detector()
    rep = det.detect(minute_features(dataset_t06.metrics), only=TECHNOLOGY_FEATURES)
    first = rep.first
    assert first is not None and first.onset_at.strftime("%H:%M") == "14:00"
    assert anomaly_score(dataset_t06.metrics, det) > 1.0


# ------------------------------------------------------------------ acceptance (2B.1), held-out seeds
@pytest.mark.slow
def test_arl0_on_500_clean_held_out_sessions():
    """ARL0 >= 20 sessions: at most 25 false alarms in 500 clean sessions (seeds 30_000..10_499)."""
    det = load_detector()
    alarms = sum(det.detect(synthetic_session(30_000 + s)[0]).first is not None for s in range(500))
    rate = alarms / 500
    assert rate == 0 or 1 / rate >= MIN_ARL0, f"{alarms} false alarms in 500 sessions"


@pytest.mark.slow
def test_t06_onset_within_two_minutes_in_95pct_of_200_seeds():
    """The planted regime starts at 14:00 (window start); onset estimate within 2 minutes in >= 95 %."""
    det = load_detector()
    hits = 0
    for s in range(200):
        mf, cp = synthetic_session(20_000 + s, latency_multiplier=10.0, reject_rate=0.10, deployment=True)
        a = det.detect(mf, only=TECHNOLOGY_FEATURES).first
        hits += a is not None and abs(a.onset_index + det.reference_minutes - cp) <= 2
    assert hits >= 190, f"{hits}/200"
