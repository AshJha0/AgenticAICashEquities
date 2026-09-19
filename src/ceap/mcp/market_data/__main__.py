"""Run the Market Data MCP server over stdio: ``python -m ceap.mcp.market_data``."""

from ceap.data.repositories import DatasetStore
from ceap.mcp.market_data.server import build_server

if __name__ == "__main__":
    build_server(DatasetStore()).run_stdio()
