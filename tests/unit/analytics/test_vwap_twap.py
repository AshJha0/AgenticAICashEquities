"""VWAP / TWAP: known-answer tests."""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pytest

from ceap.analytics.twap import twap
from ceap.analytics.vwap import vwap, vwap_by_bucket

from .conftest import T0


def test_vwap_weights_by_quantity():
    assert vwap([10.0, 20.0], [1, 3]) == pytest.approx(17.5)


def test_vwap_empty_and_zero_quantity_are_nan():
    assert math.isnan(vwap([], []))
    assert math.isnan(vwap([1.0], [0]))


def test_vwap_shape_mismatch_raises():
    with pytest.raises(ValueError):
        vwap([1.0, 2.0], [1])


def test_vwap_by_bucket_groups_by_minute():
    ts = np.array(
        [(T0 + timedelta(seconds=s)).replace(tzinfo=None) for s in (0, 30, 60, 90)], dtype="datetime64[s]"
    )
    out = vwap_by_bucket(ts, np.array([1.0, 3.0, 5.0, 7.0]), np.array([1, 1, 1, 1]), 60)
    assert len(out) == 2
    assert out[0][1] == pytest.approx(2.0) and out[1][1] == pytest.approx(6.0)


def test_twap_simple_mean_without_timestamps():
    assert twap([1.0, 2.0, 3.0]) == pytest.approx(2.0)


def test_twap_time_weighted():
    prices = [10.0, 20.0, 30.0]
    stamps = [T0, T0 + timedelta(seconds=10), T0 + timedelta(seconds=40)]
    # weights: 10s, 30s, median gap 20s  -> (100 + 600 + 600) / 60
    assert twap(prices, stamps) == pytest.approx(1300 / 60)
