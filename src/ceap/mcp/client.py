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
from collections.abc import Awaitable, Callable
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
        for key in ("PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONUTF8", "CEAP_MCP_PRINCIPAL_ROLES"):
            if key in os.environ and key not in env:
                env[key] = os.environ[key]
        return env


def _is_disconnect(exc: BaseException) -> bool:
    """True when ``exc`` says the transport is gone (the child exited), not that the tool failed."""
    import anyio

    if isinstance(
        exc,
        (ConnectionError, EOFError, anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream),
    ):
        return True
    from mcp.shared.exceptions import McpError

    return isinstance(exc, McpError) and "closed" in str(exc).lower()


class _StdioConnection:
    """One server subprocess and its ``ClientSession``, owned by a dedicated task (PLAN 3B.9).

    The ``AsyncExitStack`` holding ``stdio_client`` and ``ClientSession`` is entered and exited inside
    that one task, because the SDK's anyio cancel scopes must be closed by the task that opened them.
    Callers in any task only use the session; ``stop()`` asks the owner task to unwind.
    """

    def __init__(self, spec: StdioServerSpec) -> None:
        self.spec = spec
        self.session: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def alive(self) -> bool:
        return self.session is not None and self._task is not None and not self._task.done()

    async def start(self) -> Any:
        ready: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._own(ready), name=f"mcp-stdio:{self.spec.name}")
        return await ready

    async def _own(self, ready: asyncio.Future[Any]) -> None:
        from mcp.client.stdio import stdio_client

        from mcp import ClientSession, StdioServerParameters

        params = StdioServerParameters(
            command=self.spec.command, args=self.spec.args, env=self.spec.environment()
        )
        try:
            async with AsyncExitStack() as stack:
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self.session = session
                ready.set_result(session)
                await self._stop.wait()
        except asyncio.CancelledError:
            if not ready.done():
                ready.cancel()
            raise
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller of start()
            if not ready.done():
                ready.set_exception(exc)
        finally:
            self.session = None

    async def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except TimeoutError:
            task.cancel()
        except Exception:  # noqa: BLE001, S110 - a child that already died may fail its own teardown
            pass


class StdioMCPClient(MCPClient):
    """Real MCP transport: one subprocess + ``ClientSession`` per server.

    Lifecycle (PLAN 3B.9): each connection is owned by its own task; a call that fails because the
    child exited reconnects exactly once and retries; a second disconnect is an ``MCPError``.
    """

    MAX_RECONNECTS_PER_CALL = 1

    def __init__(self, specs: list[StdioServerSpec]) -> None:
        self._specs = {s.name: s for s in specs}
        self._connections: dict[str, _StdioConnection] = {}
        self._lock = asyncio.Lock()
        self.reconnects = 0

    def servers(self) -> list[str]:
        return sorted(self._specs)

    async def _session(self, name: str) -> Any:
        conn = self._connections.get(name)
        if conn is not None and conn.alive:
            return conn.session
        async with self._lock:  # one subprocess per server even under concurrent first calls
            conn = self._connections.get(name)
            if conn is not None and conn.alive:
                return conn.session
            spec = self._specs.get(name)
            if spec is None:
                raise MCPError(f"unknown MCP server: {name}")
            if conn is not None:
                await conn.stop()
            conn = _StdioConnection(spec)
            self._connections[name] = conn
            return await conn.start()

    async def _reconnect(self, name: str) -> Any:
        async with self._lock:
            conn = self._connections.pop(name, None)
            if conn is not None:
                await conn.stop()
        self.reconnects += 1
        return await self._session(name)

    async def _call(self, name: str, op: Callable[[Any], Awaitable[Any]]) -> Any:
        session = await self._session(name)
        try:
            return await op(session)
        except Exception as exc:
            if not _is_disconnect(exc):
                raise
            first = exc
        session = await self._reconnect(name)  # exactly one reconnect per call
        try:
            return await op(session)
        except Exception as exc:
            if _is_disconnect(exc):
                raise MCPError(f"{name}: server exited again after one reconnect ({exc})") from first
            raise

    async def discover_tools(self, server: str) -> list[dict[str, Any]]:
        result = await self._call(server, lambda session: session.list_tools())
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
                        "riskLevel": meta.get("riskLevel"),  # missing: the adapter treats it as HIGH
                        "requiredCapabilities": meta.get("requiredCapabilities", []),
                    },
                }
            )
        return out

    async def invoke(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        result = await self._call(server, lambda session: session.call_tool(tool, arguments))
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
        connections, self._connections = list(self._connections.values()), {}
        for conn in connections:
            await conn.stop()


def _read_only_hint(annotations: Any) -> bool:
    """Read-only only when the server says so explicitly; a missing hint means "may write" (fail closed)."""
    if annotations is None:
        return False
    return getattr(annotations, "readOnlyHint", None) is True
