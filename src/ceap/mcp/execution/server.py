"""Execution MCP server: parent/child orders, executions, TCA metrics, venue statistics, paper order staging."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from ceap.analytics.execution_metrics import StandardExecutionAnalytics
from ceap.analytics.portfolio_risk import target_portfolio
from ceap.data.historical import HistoricalStore
from ceap.data.repositories import DatasetStore, InMemoryExecutionRepository, InMemoryMarketDataRepository
from ceap.domain.common import to_jsonable
from ceap.domain.research import DEFAULT_GROSS_NOTIONAL
from ceap.domain.tools import RiskLevel
from ceap.mcp.common import DEFAULT_RESEARCH_DATASET, paginate, select_dataset, select_history, window_of
from ceap.mcp.execution.staging import StagedOrderBook, StagedOrders, StagingRefusedError, report_hash
from ceap.mcp.server import MCPServerDefinition
from ceap.paper.pretrade import RiskLimits, check_staged_orders

TRADING_EXECUTE = "trading:execute"


def _staged_view(staged: StagedOrders, already: bool) -> dict[str, Any]:
    return {
        "staging_id": staged.staging_id,
        "already_staged": already,
        "status": staged.status,
        "signal": staged.signal,
        "dataset": staged.dataset,
        "as_of": staged.as_of,
        "gross_notional": staged.gross_notional,
        "long_short": staged.long_short,
        "created_at": staged.created_at.isoformat(),
        "count": len(staged.orders),
        "buy_notional": staged.buy_notional,
        "sell_notional": staged.sell_notional,
        "risk_blocks": [dict(b) for b in staged.risk_blocks],
        "orders": [to_jsonable(o) for o in staged.orders],
    }


def build_server(
    store: DatasetStore,
    analytics: StandardExecutionAnalytics | None = None,
    history: HistoricalStore | None = None,
    staged: StagedOrderBook | None = None,
    limits: RiskLimits | None = None,
) -> MCPServerDefinition:
    analytics = analytics or StandardExecutionAnalytics()
    history = history or HistoricalStore()
    book = staged or StagedOrderBook()
    risk = limits or RiskLimits()
    server = MCPServerDefinition(
        "execution",
        "Order management and execution data with deterministic TCA: parent/child orders, fills, execution metrics, "
        "venue statistics; paper order staging for approved research proposals.",
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
        quotes = await mr.quotes(symbol, s, e + timedelta(seconds=analytics.post_trade_horizon_seconds))
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
            await mr.quotes(symbol, s, e + timedelta(seconds=analytics.post_trade_horizon_seconds)),
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

    @server.tool(
        "Stage a PAPER order list that moves the book to the signal's recomputed target portfolio at as_of. "
        "Nothing is routed to a venue. Idempotent per (signal, dataset, as_of, gross_notional, long_short). "
        "Pre-trade checks (restricted, hard to borrow, per-name and gross limits) run first; refused while "
        "the kill switch is set. Requires the trading:execute capability and the approved portfolio hash; a "
        "standalone server also requires a signed approval_grant.",
        read_only=False,
        risk_level=RiskLevel.HIGH,
        required_capabilities={TRADING_EXECUTE},
    )
    async def stage_orders(
        signal: str,
        dataset: str = DEFAULT_RESEARCH_DATASET,
        as_of: str | None = None,
        long_short: bool = True,
        gross_notional: float = DEFAULT_GROSS_NOTIONAL,
        approved_portfolio_hash: str | None = None,
    ) -> dict[str, Any]:
        # R-12: the hash is required at call time. It stays optional in the schema only because the
        # harness binds it after the approval gate, later than plan validation checks the schema.
        if not approved_portfolio_hash:
            raise StagingRefusedError(
                "staging refused: approved_portfolio_hash is required (stage only an approved portfolio)"
            )
        ds = select_history(history, dataset)
        report = target_portfolio(ds, signal, as_of, long_short, gross_notional)
        if report_hash(report) != approved_portfolio_hash:
            raise StagingRefusedError(
                "staging refused: the recomputed target portfolio differs from the approved one "
                "(the data changed between approval and staging); request a new approval"
            )
        halt = book.halt_reason()
        if halt:
            raise StagingRefusedError(f"staging refused: the kill switch is set ({halt})")
        chk = check_staged_orders(
            [(p.symbol, float(p.quantity), float(p.price), "") for p in report.positions if p.quantity != 0],
            float(report.gross_notional),
            risk,
        )
        staged_set, already = book.stage(report, chk.deltas, chk.blocks)
        return _staged_view(staged_set, already)

    @server.tool("List staged paper order sets, or return one by staging id.")
    async def get_staged_orders(staging_id: str | None = None) -> dict[str, Any]:
        if staging_id:
            found = book.get(staging_id)
            if found is None:
                return {"staging_id": staging_id, "found": False}
            return {"found": True, **_staged_view(found, True)}
        items = book.list()
        return {
            "count": len(items),
            "items": [
                {
                    "staging_id": s.staging_id,
                    "signal": s.signal,
                    "dataset": s.dataset,
                    "as_of": s.as_of,
                    "gross_notional": s.gross_notional,
                    "count": len(s.orders),
                    "status": s.status,
                    "created_at": s.created_at.isoformat(),
                }
                for s in items
            ],
        }

    return server
