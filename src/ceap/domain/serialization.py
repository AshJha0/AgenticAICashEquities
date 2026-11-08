"""Rebuild domain objects from the JSON produced at the MCP boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ceap.domain.execution import ExecutionMetrics, Side, VenueStatistics
from ceap.domain.market import MarketStatistics


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _f(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def venue_statistics_from_dict(d: dict[str, Any]) -> VenueStatistics:
    return VenueStatistics(
        venue=d["venue"],
        child_orders=int(d.get("child_orders", 0)),
        executions=int(d.get("executions", 0)),
        routed_quantity=int(d.get("routed_quantity", 0)),
        executed_quantity=int(d.get("executed_quantity", 0)),
        fill_rate=_f(d.get("fill_rate")),
        average_slippage_bps=_f(d.get("average_slippage_bps")),
        average_latency_us=_f(d.get("average_latency_us")),
        reject_count=int(d.get("reject_count", 0)),
        reject_rate=_f(d.get("reject_rate"), 0.0),
        market_volume_participation=_f(d.get("market_volume_participation", d.get("volume_share"))),
        fill_share=_f(d.get("fill_share")),
    )


def execution_metrics_from_dict(d: dict[str, Any]) -> ExecutionMetrics:
    return ExecutionMetrics(
        symbol=d["symbol"],
        start=_dt(d["start"]),
        end=_dt(d["end"]),
        side=Side(d.get("side", "BUY")),
        target_quantity=int(d.get("target_quantity", 0)),
        executed_quantity=int(d.get("executed_quantity", 0)),
        fill_rate=_f(d.get("fill_rate")),
        participation_rate=_f(d.get("participation_rate")),
        vwap=_f(d.get("vwap")),
        market_vwap=_f(d.get("market_vwap")),
        arrival_price=_f(d.get("arrival_price")),
        implementation_shortfall_bps=_f(d.get("implementation_shortfall_bps")),
        slippage_vs_arrival_bps=_f(d.get("slippage_vs_arrival_bps")),
        slippage_vs_vwap_bps=_f(d.get("slippage_vs_vwap_bps")),
        average_spread_bps=_f(d.get("average_spread_bps")),
        effective_spread_bps=_f(d.get("effective_spread_bps")),
        market_impact_bps=_f(d.get("market_impact_bps")),
        temporary_impact_bps=_f(d.get("temporary_impact_bps")),
        price_drift_bps=_f(d.get("price_drift_bps")),
        realised_volatility_bps=_f(d.get("realised_volatility_bps")),
        average_latency_us=_f(d.get("average_latency_us")),
        reject_rate=_f(d.get("reject_rate"), 0.0),
        execution_count=int(d.get("execution_count", 0)),
        child_order_count=int(d.get("child_order_count", 0)),
        venue_statistics={
            k: venue_statistics_from_dict(v) for k, v in (d.get("venue_statistics") or {}).items()
        },
        delay_cost_bps=_f(d.get("delay_cost_bps")),
        spread_cost_bps=_f(d.get("spread_cost_bps")),
        timing_cost_bps=_f(d.get("timing_cost_bps")),
        opportunity_cost_bps=_f(d.get("opportunity_cost_bps")),
        fees_bps=_f(d.get("fees_bps")),
        arrival_shortfall_bps=_f(d.get("arrival_shortfall_bps")),
        pretrade_estimate_bps=_f(d.get("pretrade_estimate_bps")),
        pretrade_interval_bps=_pair(d.get("pretrade_interval_bps")),
        is_standard_error_bps=_f(d.get("is_standard_error_bps")),
        is_price_risk_bps=_f(d.get("is_price_risk_bps")),
        is_residual_bps=_f(d.get("is_residual_bps")),
        is_residual_z=_f(d.get("is_residual_z")),
        realised_spread_bps=_f(d.get("realised_spread_bps")),
        price_improvement_bps=_f(d.get("price_improvement_bps")),
        markouts_bps={str(k): _f(v) for k, v in (d.get("markouts_bps") or {}).items()},
        venue_statistics_conditioned={
            str(k): {str(a): _f(b) for a, b in (v or {}).items()}
            for k, v in (d.get("venue_statistics_conditioned") or {}).items()
        },
    )


def _pair(value: Any) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (_f(value[0]), _f(value[1]))
    return (float("nan"), float("nan"))


def market_statistics_from_dict(d: dict[str, Any]) -> MarketStatistics:
    return MarketStatistics(
        symbol=d["symbol"],
        start=_dt(d["start"]),
        end=_dt(d["end"]),
        open_mid=_f(d.get("open_mid")),
        close_mid=_f(d.get("close_mid")),
        high=_f(d.get("high")),
        low=_f(d.get("low")),
        vwap=_f(d.get("vwap")),
        traded_volume=int(d.get("traded_volume", 0)),
        trade_count=int(d.get("trade_count", 0)),
        average_spread_bps=_f(d.get("average_spread_bps")),
        realised_volatility_bps=_f(d.get("realised_volatility_bps")),
        annualised_volatility=_f(d.get("annualised_volatility")),
        average_displayed_depth=_f(d.get("average_displayed_depth")),
        average_top_of_book_size=_f(d.get("average_top_of_book_size")),
        price_drift_bps=_f(d.get("price_drift_bps")),
        stale_quote_fraction=_f(d.get("stale_quote_fraction"), 0.0),
        crossed_quote_count=int(d.get("crossed_quote_count", 0)),
        quote_count=int(d.get("quote_count", 0)),
        venue_volume={k: int(v) for k, v in (d.get("venue_volume") or {}).items()},
    )
