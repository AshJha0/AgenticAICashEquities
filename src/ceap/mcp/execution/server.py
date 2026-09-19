"""Execution MCP server: parent/child orders, executions, TCA metrics, venue statistics."""

from __future__ import annotations

from typing import Any

from ceap.analytics.execution_metrics import StandardExecutionAnalytics
from ceap.data.repositories import DatasetStore, InMemoryExecutionRepository, InMemoryMarketDataRepository
from ceap.domain.common import to_jsonable
from ceap.mcp.common import paginate, select_dataset, window_of
from ceap.mcp.server import MCPServerDefinition


def build_server(
    store: DatasetStore, analytics: StandardExecutionAnalytics | None = None
) -> MCPServerDefinition:
    analytics = analytics or StandardExecutionAnalytics()
    server = MCPServerDefinition(
        "execution",
        "Order management and execution data with deterministic TCA: parent/child orders, fills, execution metrics, venue statistics.",
    )

    def repos(dataset: str | None):
        ds = select_dataset(store, dataset)
        return ds, InMemoryExecutionRepository(ds), InMemoryMarketDataRepository(ds)

    @server.tool("Return parent (algorithmic) orders for a symbol whose start time falls in the window.")
    async def get_parent_orders(
        symbol: str, start: str, end: str, dataset: str | None = None
    ) -> dict[str, Any]:
        ds, er, _ = repos(dataset)
        s, e = window_of(ds, start, end)
        orders = [o for o in await er.orders(symbol, s, e) if o.is_parent]
        return {
            "symbol": symbol,
            "start": s.isoformat(),
            "end": e.isoformat(),
            "count": len(orders),
            "items": [to_jsonable(o) for o in orders],
        }

    @server.tool(
        "Return child (slice) orders in the window, optionally filtered by parent order id; paginated."
    )
    async def get_child_orders(
        symbol: str,
        start: str,
        end: str,
        parent_order_id: str | None = None,
        limit: int = 500,
        offset: int = 0,
        dataset: str | None = None,
    ) -> dict[str, Any]:
        ds, er, _ = repos(dataset)
        s, e = window_of(ds, start, end)
        orders = [
            o
            for o in await er.orders(symbol, s, e)
            if not o.is_parent and (parent_order_id is None or o.parent_order_id == parent_order_id)
        ]
        page = paginate([to_jsonable(o) for o in orders], limit, offset)
        page.update({"symbol": symbol, "start": s.isoformat(), "end": e.isoformat()})
        status_counts: dict[str, int] = {}
        for o in orders:
            status_counts[o.status.value] = status_counts.get(o.status.value, 0) + 1
        page["status_counts"] = status_counts
        return page

    @server.tool(
        "Return executions (fills) in the window, optionally filtered by parent order id; paginated."
    )
    async def get_executions(
        symbol: str,
        start: str,
        end: str,
        parent_order_id: str | None = None,
        limit: int = 500,
        offset: int = 0,
        dataset: str | None = None,
    ) -> dict[str, Any]:
        ds, er, _ = repos(dataset)
        s, e = window_of(ds, start, end)
        fills = [
            x
            for x in await er.executions(symbol, s, e)
            if parent_order_id is None or x.parent_order_id == parent_order_id
        ]
        page = paginate([to_jsonable(x) for x in fills], limit, offset)
        page.update(
            {
                "symbol": symbol,
                "start": s.isoformat(),
                "end": e.isoformat(),
                "executed_quantity": sum(x.quantity for x in fills),
                "notional": round(sum(x.notional for x in fills), 2),
            }
        )
        return page

    @server.tool(
        "Compute deterministic execution-quality metrics (TCA) for the window: fill rate, participation, VWAP, "
        "arrival price, implementation shortfall, slippage, spreads, market impact, latency, reject rate, per-venue stats."
    )
    async def get_execution_metrics(
        symbol: str, start: str, end: str, dataset: str | None = None
    ) -> dict[str, Any]:
        ds, er, mr = repos(dataset)
        s, e = window_of(ds, start, end)
        orders = await er.orders(symbol, s, e)
        fills = await er.executions(symbol, s, e)
        quotes = await mr.quotes(symbol, s, e)
        trades = await mr.trades(symbol, s, e)
        metrics = analytics.calculate(orders, fills, quotes, trades, s, e)
        return to_jsonable(metrics)

    @server.tool(
        "Return per-venue fill rate, slippage, latency, reject rate and volume share for the window."
    )
    async def get_venue_statistics(
        symbol: str, start: str, end: str, dataset: str | None = None
    ) -> dict[str, Any]:
        ds, er, mr = repos(dataset)
        s, e = window_of(ds, start, end)
        metrics = analytics.calculate(
            await er.orders(symbol, s, e),
            await er.executions(symbol, s, e),
            await mr.quotes(symbol, s, e),
            await mr.trades(symbol, s, e),
            s,
            e,
        )
        return {
            "symbol": symbol,
            "start": s.isoformat(),
            "end": e.isoformat(),
            "venues": {k: to_jsonable(v) for k, v in metrics.venue_statistics.items()},
        }

    @server.tool("Describe the execution strategy configuration in force for the symbol (algo parameters).")
    async def get_strategy_configuration(symbol: str, strategy: str = "VWAP") -> dict[str, Any]:
        return {
            "symbol": symbol,
            "strategy": strategy,
            "version": "vwap-3.4.0",
            "slice_interval_seconds": 30,
            "target_participation_rate": 0.08,
            "max_participation_rate": 0.20,
            "limit_offset_ticks": 2,
            "venue_selection": "smart-order-router",
            "dark_pool_enabled": True,
            "urgency": "NORMAL",
        }

    return server
