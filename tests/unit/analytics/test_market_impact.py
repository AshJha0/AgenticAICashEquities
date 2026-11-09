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
    with pytest.warns(DeprecationWarning, match="market_impact_bps is deprecated"):
        assert market_impact_bps(Side.BUY, 100.0, 100.5) == pytest.approx(50.0)
    with pytest.warns(DeprecationWarning, match="temporary_impact_bps is deprecated"):
        assert temporary_impact_bps(Side.BUY, 100.6, 100.5) == pytest.approx(9.95, rel=1e-2)
    # 2T.5 units: c * sigma_daily * sqrt(Q / ADV); Q/ADV = 4 % of a day, sigma 100 bps
    assert square_root_impact_estimate_bps(40_000, 1_000_000, 100.0, 0.6) == pytest.approx(12.0)
