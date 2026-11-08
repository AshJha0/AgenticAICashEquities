"""A tiny stdio MCP server for the lifecycle test: ``pid`` returns the child's process id."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

server = FastMCP("pid")


@server.tool()
def pid() -> int:
    return os.getpid()


if __name__ == "__main__":
    server.run("stdio")
