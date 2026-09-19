"""MCP client abstraction with two implementations.

* :class:`InProcessMCPClient` - calls server definitions directly (no
  transport). Used by tests, the API and the CLI.
* :class:`StdioMCPClient` - spawns each server as a subprocess and talks
  real MCP over stdio using the official ``mcp`` SDK.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from abc import ABC, abstractmethod
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from ceap.mcp.server import MCPServerDefinition


class MCPError(RuntimeError):
    pass


class MCPClient(ABC):
    @abstractmethod
    async def discover_tools(self, server: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def invoke(self, server: str, tool: str, arguments: dict[str, Any]) -> Any: ...

    @abstractmethod
    def servers(self) -> list[str]: ...

    async def close(self) -> None:  # noqa: B027 - optional hook
        return None


class InProcessMCPClient(MCPClient):
    def __init__(self, servers: dict[str, MCPServerDefinition] | None = None) -> None:
        self._servers: dict[str, MCPServerDefinition] = dict(servers or {})

    def register(self, server: MCPServerDefinition) -> None:
        self._servers[server.name] = server

    def servers(self) -> list[str]:
        return sorted(self._servers)

    async def discover_tools(self, server: str) -> list[dict[str, Any]]:
        return self._server(server).list_tools()

    async def invoke(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        try:
            return await self._server(server).call(tool, arguments)
        except KeyError as exc:
            raise MCPError(str(exc)) from exc
        except TypeError as exc:  # bad arguments
            raise MCPError(f"{server}.{tool}: invalid arguments: {exc}") from exc

    def _server(self, name: str) -> MCPServerDefinition:
        try:
            return self._servers[name]
        except KeyError as exc:
            raise MCPError(f"unknown MCP server: {name}") from exc


@dataclass
class StdioServerSpec:
    """How to spawn one server. ``command`` defaults to the current interpreter so a virtual
    environment is honoured; ``PYTHONPATH`` is forwarded so ``src``-layout checkouts work."""

    name: str
    command: str = sys.executable
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None

    def environment(self) -> dict[str, str]:
        env = dict(self.env or {})
        for key in ("PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONUTF8"):
            if key in os.environ and key not in env:
                env[key] = os.environ[key]
        return env


class StdioMCPClient(MCPClient):
    """Real MCP transport: one subprocess + ``ClientSession`` per server."""

    def __init__(self, specs: list[StdioServerSpec]) -> None:
        self._specs = {s.name: s for s in specs}
        self._sessions: dict[str, Any] = {}
        self._stack = AsyncExitStack()
        self._lock = asyncio.Lock()

    def servers(self) -> list[str]:
        return sorted(self._specs)

    async def _session(self, name: str) -> Any:
        if name in self._sessions:
            return self._sessions[name]
        async with self._lock:  # one subprocess per server even under concurrent first calls
            if name in self._sessions:
                return self._sessions[name]
            from mcp.client.stdio import stdio_client

            from mcp import ClientSession, StdioServerParameters

            spec = self._specs.get(name)
            if spec is None:
                raise MCPError(f"unknown MCP server: {name}")
            params = StdioServerParameters(command=spec.command, args=spec.args, env=spec.environment())
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._sessions[name] = session
            return session

    async def discover_tools(self, server: str) -> list[dict[str, Any]]:
        session = await self._session(server)
        result = await session.list_tools()
        out: list[dict[str, Any]] = []
        for t in result.tools:
            ann = t.annotations
            meta = getattr(t, "meta", None) or {}
            out.append(
                {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.inputSchema,
                    "annotations": {
                        "readOnlyHint": _read_only_hint(ann),
                        "riskLevel": meta.get("riskLevel", "LOW"),
                        "requiredCapabilities": meta.get("requiredCapabilities", []),
                    },
                }
            )
        return out

    async def invoke(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        session = await self._session(server)
        result = await session.call_tool(tool, arguments)
        if getattr(result, "isError", False):
            raise MCPError(f"{server}.{tool} failed: {result.content}")
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            # FastMCP wraps non-object returns as {"result": ...}; only unwrap that exact shape
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return structured["result"]
            return structured
        texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except json.JSONDecodeError:
                return texts[0]
        return texts

    async def close(self) -> None:
        await self._stack.aclose()
        self._sessions.clear()


def _read_only_hint(annotations: Any) -> bool:
    """A missing/None hint means "unknown" - treat as read-only only when explicitly true or absent."""
    if annotations is None:
        return True
    hint = getattr(annotations, "readOnlyHint", None)
    return True if hint is None else bool(hint)
