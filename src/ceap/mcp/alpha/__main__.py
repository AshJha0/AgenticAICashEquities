"""Run the Alpha MCP server over stdio: ``python -m ceap.mcp.alpha``."""

from ceap.data.historical import HistoricalStore
from ceap.mcp.alpha.server import build_server

if __name__ == "__main__":
    build_server(HistoricalStore()).run_stdio()
