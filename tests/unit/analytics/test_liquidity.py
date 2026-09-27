"""Liquidity / spread / participation: known-answer tests."""

from __future__ import annotations

import math

import pytest

from ceap.analytics.liquidity import (
    average_spread_bps,
    crossed_quote_count,
    participation_rate,
    stale_quote_fraction,
)

from .conftest import make_quote


def test_spread_bps_and_crossed_quotes_excluded():
    quotes = [make_quote(99.99, 100.01), make_quote(100.02, 100.00)]  # second is crossed
    assert average_spread_bps(quotes) == pytest.approx(2.0, rel=1e-3)
    assert crossed_quote_count(quotes) == 1


def test_stale_quote_fraction():
    quotes = [make_quote(99.99, 100.01, s=i) for i in range(5)]  # identical
    assert stale_quote_fraction(quotes) == pytest.approx(1.0)
    quotes = [make_quote(99.99, 100.01, bs=100 + i, s=i) for i in range(5)]
    assert stale_quote_fraction(quotes) == pytest.approx(0.0)
    assert stale_quote_fraction([]) == 0.0


def test_participation_rate():
    assert participation_rate(50, 1000) == pytest.approx(0.05)
    assert math.isnan(participation_rate(50, 0))


def test_average_spread_empty_quotes_nan():
    assert math.isnan(average_spread_bps([]))
