"""Run the Risk MCP server over stdio: ``python -m ceap.mcp.risk``."""

from ceap.data.repositories import DatasetStore
from ceap.mcp.risk.server import build_server

if __name__ == "__main__":
    build_server(DatasetStore()).run_stdio()
