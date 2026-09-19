"""Run the Engineering MCP server over stdio: ``python -m ceap.mcp.engineering``."""

from ceap.data.repositories import DatasetStore
from ceap.mcp.engineering.server import build_server

if __name__ == "__main__":
    build_server(DatasetStore()).run_stdio()
