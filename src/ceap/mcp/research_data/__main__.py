"""Run the Research Data MCP server over stdio: ``python -m ceap.mcp.research_data``."""

from ceap.data.historical import HistoricalStore
from ceap.mcp.research_data.server import build_server

if __name__ == "__main__":
    build_server(HistoricalStore()).run_stdio()
