"""Risk MCP server: positions, exposure, limit checks and stress - for the execution book and for research portfolios."""

from __future__ import annotations

from typing import Any

from ceap.analytics.execution_metrics import QuoteIndex
from ceap.analytics.portfolio_risk import check_limits, stress, target_portfolio
from ceap.data.historical import HistoricalStore
from ceap.data.repositories import DatasetStore
from ceap.domain.common import to_jsonable
from ceap.domain.research import DEFAULT_GROSS_NOTIONAL
from ceap.domain.risk import LimitCheck, StressResult
from ceap.domain.tools import RiskLevel
from ceap.mcp.common import DEFAULT_RESEARCH_DATASET, parse_ts, select_dataset, select_history
from ceap.mcp.server import MCPServerDefinition


def build_server(store: DatasetStore, history: HistoricalStore | None = None) -> MCPServerDefinition:
    history = history or HistoricalStore()
    server = MCPServerDefinition(
        "risk",
        "Position, exposure, trading-limit and stress information for the cash-equities book, and portfolio-level "
        "exposure, limit and stress checks for a research signal's recomputed target portfolio.",
    )

    @server.tool(
        "Return the start-of-day position for a symbol and the position after the window's executions."
    )
    async def get_position(
        symbol: str, as_of: str | None = None, dataset: str | None = None
    ) -> dict[str, Any]:
        ds = select_dataset(store, dataset)
        pos = next((p for p in ds.positions if p.symbol == symbol), None)
        if pos is None:
            return {"symbol": symbol, "found": False}
        ts = parse_ts(as_of) if as_of else ds.window_end
        bought = sum(
            x.quantity
            for x in ds.executions
            if x.symbol == symbol and x.timestamp < ts and x.side.value == "BUY"
        )
        sold = sum(
            x.quantity
            for x in ds.executions
            if x.symbol == symbol and x.timestamp < ts and x.side.value == "SELL"
        )
        return {
            "symbol": symbol,
            "found": True,
            "book": pos.book,
            "start_of_day_quantity": pos.quantity,
            "average_price": pos.average_price,
            "as_of": ts.isoformat(),
            "quantity_as_of": pos.quantity + bought - sold,
            "bought_today": bought,
            "sold_today": sold,
        }

    @server.tool("Return notional exposure for a symbol at a timestamp using the prevailing mid.")
    async def get_exposure(
        symbol: str, as_of: str | None = None, dataset: str | None = None
    ) -> dict[str, Any]:
        ds = select_dataset(store, dataset)
        position = await get_position(symbol, as_of, dataset)
        if not position.get("found"):
            return position
        ts = parse_ts(as_of) if as_of else ds.window_end
        mid = QuoteIndex(ds.quotes).mid_at(ts)
        qty = position["quantity_as_of"]
        limit = next((lm for lm in ds.limits if lm.symbol == symbol), None)
        return {
            "symbol": symbol,
            "as_of": ts.isoformat(),
            "quantity": qty,
            "reference_price": mid,
            "gross_exposure": round(qty * mid, 2),
            "max_notional": limit.max_notional if limit else None,
            "notional_utilisation": round(qty * mid / limit.max_notional, 4) if limit else None,
        }

    @server.tool("Check an order quantity / participation rate against configured trading limits.")
    async def check_limit(
        symbol: str,
        order_quantity: int,
        participation_rate: float | None = None,
        side: str = "BUY",
        dataset: str | None = None,
    ) -> dict[str, Any]:
        ds = select_dataset(store, dataset)
        limit = next((lm for lm in ds.limits if lm.symbol == symbol), None)
        if limit is None:
            return {"symbol": symbol, "found": False, "checks": []}
        position = await get_position(symbol, None, dataset)
        signed = int(order_quantity) if side.upper() == "BUY" else -int(order_quantity)
        projected = int(position["quantity_as_of"]) + signed

        def check(name: str, limit_value: float, observed: float) -> LimitCheck:
            util = abs(observed) / limit_value if limit_value else float("inf")
            return LimitCheck(symbol, name, limit_value, observed, abs(observed) > limit_value, util)

        checks = [
            check("max_order_quantity", limit.max_order_quantity, order_quantity),
            check("max_position", limit.max_position, projected),
        ]
        if participation_rate is not None:
            checks.append(check("max_participation_rate", limit.max_participation_rate, participation_rate))
        return {
            "symbol": symbol,
            "found": True,
            "side": side.upper(),
            "projected_position": projected,
            "any_breached": any(c.breached for c in checks),
            "checks": [to_jsonable(c) for c in checks],
        }

    @server.tool(
        "Compute the P&L impact of an instantaneous price shock (bps) on the current position.",
        risk_level=RiskLevel.MEDIUM,
    )
    async def calculate_stress(
        symbol: str, shock_bps: float = -200.0, dataset: str | None = None
    ) -> dict[str, Any]:
        ds = select_dataset(store, dataset)
        position = await get_position(symbol, None, dataset)
        if not position.get("found"):
            return position
        mid = QuoteIndex(ds.quotes).mid_at(ds.window_end)
        qty = position["quantity_as_of"]
        result = StressResult(symbol, shock_bps, qty, mid, round(qty * mid * shock_bps / 10_000.0, 2))
        return to_jsonable(result)

    # ------------------------------------------------------ research portfolios
    @server.tool(
        "Recompute a signal's target portfolio at as_of (never trusting a backtest) and return its positions, "
        "gross/net exposure, single-name concentration (HHI), market beta and ADV participation."
    )
    async def get_portfolio_exposure(
        signal: str,
        dataset: str = DEFAULT_RESEARCH_DATASET,
        as_of: str | None = None,
        long_short: bool = True,
        gross_notional: float = DEFAULT_GROSS_NOTIONAL,
    ) -> dict[str, Any]:
        ds = select_history(history, dataset)
        report = target_portfolio(ds, signal, as_of, long_short, gross_notional)
        return to_jsonable(report)

    @server.tool(
        "Check the recomputed target portfolio against portfolio limits: gross, net, single-name weight, beta, "
        "concentration and per-name ADV participation."
    )
    async def check_portfolio_limits(
        signal: str,
        dataset: str = DEFAULT_RESEARCH_DATASET,
        as_of: str | None = None,
        long_short: bool = True,
        gross_notional: float = DEFAULT_GROSS_NOTIONAL,
    ) -> dict[str, Any]:
        ds = select_history(history, dataset)
        report = target_portfolio(ds, signal, as_of, long_short, gross_notional)
        checks = check_limits(report)
        return {
            "signal": signal,
            "dataset": ds.scenario.id,
            "as_of": report.as_of,
            "any_breached": any(c.breached for c in checks),
            "breached_symbols": sorted({c.symbol for c in checks if c.breached}),
            "checks": [to_jsonable(c) for c in checks],
        }

    @server.tool(
        "P&L impact of an instantaneous market shock (bps) on the recomputed target portfolio: through its "
        "beta, through its net exposure, and for the largest single name.",
        risk_level=RiskLevel.MEDIUM,
    )
    async def calculate_portfolio_stress(
        signal: str,
        dataset: str = DEFAULT_RESEARCH_DATASET,
        as_of: str | None = None,
        long_short: bool = True,
        gross_notional: float = DEFAULT_GROSS_NOTIONAL,
        shock_bps: float = -500.0,
    ) -> dict[str, Any]:
        ds = select_history(history, dataset)
        report = target_portfolio(ds, signal, as_of, long_short, gross_notional)
        return {
            "signal": signal,
            "dataset": ds.scenario.id,
            "as_of": report.as_of,
            "gross_notional": report.gross_notional,
            "beta": report.beta,
            "net": report.net,
            "max_abs_weight": report.max_abs_weight,
            **to_jsonable(stress(report, shock_bps)),
        }

    return server
