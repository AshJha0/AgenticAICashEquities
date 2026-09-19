"""MCP layer: servers, client abstraction, tool adapter and registry.

    Tool  ->  MCPToolAdapter  ->  MCPClient  ->  MCP Server

The harness only ever sees ``Tool`` objects; MCP is an infrastructure
concern hidden behind :class:`MCPClient`.
"""

from ceap.mcp.adapter import MCPToolAdapter
from ceap.mcp.client import InProcessMCPClient, MCPClient, StdioMCPClient
from ceap.mcp.registry import build_default_servers, build_tool_registry
from ceap.mcp.server import MCPServerDefinition, ToolSpec

__all__ = [
    "InProcessMCPClient",
    "MCPClient",
    "MCPServerDefinition",
    "MCPToolAdapter",
    "StdioMCPClient",
    "ToolSpec",
    "build_default_servers",
    "build_tool_registry",
]
