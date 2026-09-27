"""Market impact decomposition: known-answer tests."""

from __future__ import annotations

import pytest

from ceap.analytics.market_impact import (
    market_impact_bps,
    square_root_impact_estimate_bps,
    temporary_impact_bps,
)
from ceap.domain.execution import Side


def test_market_impact_decomposition():
    assert market_impact_bps(Side.BUY, 100.0, 100.5) == pytest.approx(50.0)
    assert temporary_impact_bps(Side.BUY, 100.6, 100.5) == pytest.approx(9.95, rel=1e-2)
    assert square_root_impact_estimate_bps(0.04, 100.0, 0.6) == pytest.approx(12.0)
