"""Run the Execution MCP server over stdio: ``python -m ceap.mcp.execution``."""

from ceap.data.repositories import DatasetStore
from ceap.mcp.execution.server import build_server

if __name__ == "__main__":
    build_server(DatasetStore()).run_stdio()
