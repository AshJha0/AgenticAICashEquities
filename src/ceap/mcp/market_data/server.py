"""Market Data MCP server: quotes, trades, order book, reference data, statistics."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from ceap.analytics.execution_metrics import QuoteIndex
from ceap.analytics.market_statistics import calculate_market_statistics
from ceap.data.repositories import DatasetStore, InMemoryMarketDataRepository
from ceap.data.scenarios import BASE_PRICES, SYMBOLS, VENUES
from ceap.domain.common import to_jsonable
from ceap.mcp.common import downsample, paginate, parse_ts, select_dataset, window_of
from ceap.mcp.server import MCPServerDefinition


def build_server(store: DatasetStore) -> MCPServerDefinition:
    server = MCPServerDefinition(
        "market_data",
        "Consolidated cash-equity market data: NBBO quotes, trades, order-book snapshots, reference data and window statistics.",
    )

    def repo(dataset: str | None) -> tuple[Any, InMemoryMarketDataRepository]:
        ds = select_dataset(store, dataset)
        return ds, InMemoryMarketDataRepository(ds)

    @server.tool(
        "Return the prevailing NBBO quote for a symbol at a timestamp (ISO-8601, London local if naive)."
    )
    async def get_quote(symbol: str, timestamp: str, dataset: str | None = None) -> dict[str, Any]:
        ds, r = repo(dataset)
        ts = parse_ts(timestamp)
        quotes = await r.quotes(symbol, ds.sim_start, ds.sim_end)
        q = QuoteIndex(quotes).at(ts)
        if q is None:
            return {"symbol": symbol, "timestamp": ts.isoformat(), "quote": None}
        return {
            "symbol": symbol,
            "timestamp": ts.isoformat(),
            "quote": to_jsonable(q),
            "mid": q.mid,
            "spread_bps": q.spread_bps,
        }

    @server.tool("Return NBBO quotes between start and end (downsampled to max_points, full count reported).")
    async def get_quotes(
        symbol: str, start: str, end: str, max_points: int = 600, dataset: str | None = None
    ) -> dict[str, Any]:
        ds, r = repo(dataset)
        s, e = window_of(ds, start, end)
        quotes = await r.quotes(symbol, s, e)
        sample = downsample(quotes, max_points)
        return {
            "symbol": symbol,
            "start": s.isoformat(),
            "end": e.isoformat(),
            "count": len(quotes),
            "returned": len(sample),
            "items": [to_jsonable(q) for q in sample],
        }

    @server.tool(
        "Return the order-book snapshot (5 levels each side) nearest to and not after the timestamp."
    )
    async def get_order_book(symbol: str, timestamp: str, dataset: str | None = None) -> dict[str, Any]:
        ds, r = repo(dataset)
        ts = parse_ts(timestamp)
        books = await r.order_books(
            symbol, ds.sim_start, ts + timedelta(microseconds=1)
        )  # "not after" is inclusive
        if not books:
            return {"symbol": symbol, "timestamp": ts.isoformat(), "book": None}
        b = books[-1]
        return {
            "symbol": symbol,
            "timestamp": b.timestamp.isoformat(),
            "bids": [to_jsonable(lv) for lv in b.bids()],
            "asks": [to_jsonable(lv) for lv in b.asks()],
            "displayed_depth": b.displayed_depth(),
            "imbalance": b.imbalance(),
        }

    @server.tool("Return order-book depth statistics (mean displayed depth / imbalance) over a window.")
    async def get_order_book_statistics(
        symbol: str, start: str, end: str, dataset: str | None = None
    ) -> dict[str, Any]:
        ds, r = repo(dataset)
        s, e = window_of(ds, start, end)
        books = await r.order_books(symbol, s, e)
        if not books:
            return {"symbol": symbol, "start": s.isoformat(), "end": e.isoformat(), "snapshots": 0}
        depth = [b.displayed_depth() for b in books]
        return {
            "symbol": symbol,
            "start": s.isoformat(),
            "end": e.isoformat(),
            "snapshots": len(books),
            "average_displayed_depth": sum(depth) / len(depth),
            "min_displayed_depth": min(depth),
            "average_imbalance": sum(b.imbalance() for b in books) / len(books),
        }

    @server.tool("Return trades (prints) between start and end, paginated.")
    async def get_trades(
        symbol: str, start: str, end: str, limit: int = 500, offset: int = 0, dataset: str | None = None
    ) -> dict[str, Any]:
        ds, r = repo(dataset)
        s, e = window_of(ds, start, end)
        trades = await r.trades(symbol, s, e)
        page = paginate([to_jsonable(t) for t in trades], limit, offset)
        page.update(
            {
                "symbol": symbol,
                "start": s.isoformat(),
                "end": e.isoformat(),
                "total_volume": sum(t.quantity for t in trades),
            }
        )
        return page

    @server.tool(
        "Return deterministic market statistics for a window: VWAP, volume, spread, realised volatility, depth, drift, "
        "stale/crossed quote diagnostics and venue volume share."
    )
    async def get_market_statistics(
        symbol: str, start: str, end: str, dataset: str | None = None
    ) -> dict[str, Any]:
        ds, r = repo(dataset)
        s, e = window_of(ds, start, end)
        quotes = await r.quotes(symbol, s, e)
        trades = await r.trades(symbol, s, e)
        books = await r.order_books(symbol, s, e)
        stats = calculate_market_statistics(symbol, s, e, quotes, trades, books)
        return to_jsonable(stats)

    @server.tool(
        "Return reference data for a symbol (listing venue, tick size, lot size, sector, reference price)."
    )
    async def get_reference_data(symbol: str) -> dict[str, Any]:
        if symbol not in SYMBOLS:
            return {"symbol": symbol, "found": False}
        return {
            "symbol": symbol,
            "found": True,
            "name": {
                "AAPL": "Apple Inc",
                "MSFT": "Microsoft Corp",
                "NVDA": "NVIDIA Corp",
                "AMZN": "Amazon.com Inc",
                "META": "Meta Platforms",
                "GOOGL": "Alphabet Inc",
            }[symbol],
            "primary_listing": "XNAS",
            "currency": "USD",
            "tick_size": 0.01,
            "lot_size": 100,
            "sector": "Information Technology"
            if symbol in ("AAPL", "MSFT", "NVDA")
            else "Communication Services"
            if symbol in ("META", "GOOGL")
            else "Consumer Discretionary",
            "reference_price": BASE_PRICES[symbol],
            "eligible_venues": list(VENUES),
        }

    @server.tool("List symbols and the time span covered by the selected dataset.")
    async def get_coverage(dataset: str | None = None) -> dict[str, Any]:
        ds = select_dataset(store, dataset)
        return {
            "dataset": ds.scenario.id,
            "symbols": [ds.symbol],
            "session_date": ds.session_date.isoformat(),
            "start": ds.sim_start.isoformat(),
            "end": ds.sim_end.isoformat(),
            "quote_interval_seconds": 1,
            "order_book_interval_seconds": 10,
        }

    return server
