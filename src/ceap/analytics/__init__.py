"""Deterministic execution and market analytics.

Every function here is pure, vectorised where sensible, and unit-tested.
The LLM consumes these outputs; it never computes them.
"""

from ceap.analytics.attribution import AttributionResult, CauseScore, attribute_causes
from ceap.analytics.execution_metrics import StandardExecutionAnalytics
from ceap.analytics.implementation_shortfall import implementation_shortfall_bps
from ceap.analytics.liquidity import average_displayed_depth, average_top_of_book_size
from ceap.analytics.market_impact import market_impact_bps, temporary_impact_bps
from ceap.analytics.market_statistics import calculate_market_statistics
from ceap.analytics.slippage import slippage_bps
from ceap.analytics.twap import twap
from ceap.analytics.volatility import annualise_volatility, realised_volatility_bps
from ceap.analytics.vwap import vwap

__all__ = [
    "AttributionResult",
    "CauseScore",
    "StandardExecutionAnalytics",
    "annualise_volatility",
    "attribute_causes",
    "average_displayed_depth",
    "average_top_of_book_size",
    "calculate_market_statistics",
    "implementation_shortfall_bps",
    "market_impact_bps",
    "realised_volatility_bps",
    "slippage_bps",
    "temporary_impact_bps",
    "twap",
    "vwap",
]
