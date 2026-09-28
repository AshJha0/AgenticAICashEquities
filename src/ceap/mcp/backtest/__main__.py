"""Run the Backtest MCP server over stdio: ``python -m ceap.mcp.backtest``."""

from ceap.data.historical import HistoricalStore
from ceap.mcp.backtest.server import build_server

if __name__ == "__main__":
    build_server(HistoricalStore()).run_stdio()
