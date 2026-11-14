"""MCP client rules without spawning servers (0.10.0 E1 coverage for ``ceap.mcp``).

The stdio connection class is replaced by an in-memory fake (a collaborator of the client), so the
client's own logic is exercised for real: reconnect exactly once on a disconnect, never on a tool
error, result unwrapping, tool discovery that fails closed on a missing read-only hint, and the
environment forwarded to a child. The contract tier drives real subprocesses."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from ceap.mcp import client as mcp_client
from ceap.mcp.client import InProcessMCPClient, MCPError, StdioMCPClient, StdioServerSpec
from ceap.mcp.server import MCPServerDefinition


class _FakeSession:
    def __init__(self, outcomes: list):
        self.outcomes = outcomes
        self.calls: list = []

    async def _next(self, what):
        self.calls.append(what)
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out

    async def call_tool(self, tool, arguments):
        return await self._next(("call", tool, arguments))

    async def list_tools(self):
        return await self._next(("list",))


@pytest.fixture
def fake_connections(monkeypatch):
    """Each new connection hands out the next queued session; ``started``/``stopped`` record the lifecycle."""
    sessions: list[_FakeSession] = []
    log: dict[str, list] = {"started": [], "stopped": []}

    class FakeConnection:
        def __init__(self, spec):
            self.spec = spec
            self.session = None

        @property
        def alive(self):
            return self.session is not None

        async def start(self):
            self.session = sessions.pop(0)
            log["started"].append(self.spec.name)
            return self.session

        async def stop(self, timeout: float = 5.0):
            self.session = None
            log["stopped"].append(self.spec.name)

    monkeypatch.setattr(mcp_client, "_StdioConnection", FakeConnection)
    return sessions, log


def _result(structured=None, texts=(), error=False):
    content = [SimpleNamespace(type="text", text=t) for t in texts]
    return SimpleNamespace(isError=error, structuredContent=structured, content=content)


async def test_one_disconnect_reconnects_once_and_retries(fake_connections):
    sessions, log = fake_connections
    sessions += [_FakeSession([ConnectionError("pipe closed")]), _FakeSession([_result({"result": 42})])]
    c = StdioMCPClient([StdioServerSpec("market")])
    assert await c.invoke("market", "vwap", {"symbol": "AAPL"}) == 42
    assert c.reconnects == 1 and log == {"started": ["market", "market"], "stopped": ["market"]}


async def test_second_disconnect_is_an_mcp_error(fake_connections):
    sessions, _ = fake_connections
    sessions += [_FakeSession([EOFError()]), _FakeSession([ConnectionResetError("again")])]
    c = StdioMCPClient([StdioServerSpec("market")])
    with pytest.raises(MCPError, match="market: server exited again after one reconnect"):
        await c.invoke("market", "vwap", {})


async def test_tool_errors_are_not_retried(fake_connections):
    sessions, log = fake_connections
    sessions.append(_FakeSession([ValueError("bad symbol")]))
    c = StdioMCPClient([StdioServerSpec("market")])
    with pytest.raises(ValueError, match="bad symbol"):
        await c.invoke("market", "vwap", {})
    assert c.reconnects == 0 and log["started"] == ["market"]
    with pytest.raises(MCPError, match="unknown MCP server: nope"):
        await c.invoke("nope", "vwap", {})


async def test_alive_session_is_reused_and_close_stops_every_connection(fake_connections):
    sessions, log = fake_connections
    s = _FakeSession([_result(texts=["1"]), _result(texts=["2"])])
    sessions.append(s)
    c = StdioMCPClient([StdioServerSpec("b"), StdioServerSpec("a")])
    assert c.servers() == ["a", "b"]
    assert [await c.invoke("b", "t", {}), await c.invoke("b", "t", {})] == [1, 2]
    assert log["started"] == ["b"] and len(s.calls) == 2
    await c.close()
    assert log["stopped"] == ["b"] and c._connections == {}


@pytest.mark.parametrize(
    ("result", "want"),
    [
        (_result({"result": [1, 2]}), [1, 2]),  # FastMCP's wrapper for a non-object return
        (_result({"result": 1, "extra": 2}), {"result": 1, "extra": 2}),  # not the exact wrapper shape
        (_result(texts=[json.dumps({"a": 1})]), {"a": 1}),
        (_result(texts=["plain words"]), "plain words"),
        (_result(texts=["a", "b"]), ["a", "b"]),
    ],
)
async def test_invoke_unwraps_results(fake_connections, result, want):
    sessions, _ = fake_connections
    sessions.append(_FakeSession([result]))
    assert await StdioMCPClient([StdioServerSpec("s")]).invoke("s", "t", {}) == want


async def test_tool_error_result_raises(fake_connections):
    sessions, _ = fake_connections
    sessions.append(_FakeSession([_result(texts=["boom"], error=True)]))
    with pytest.raises(MCPError, match=r"s\.t failed"):
        await StdioMCPClient([StdioServerSpec("s")]).invoke("s", "t", {})


async def test_discovery_fails_closed_on_missing_hints(fake_connections):
    sessions, _ = fake_connections
    tools = SimpleNamespace(
        tools=[
            SimpleNamespace(
                name="read",
                description="r",
                inputSchema={},
                annotations=SimpleNamespace(readOnlyHint=True),
                meta={"riskLevel": "LOW", "requiredCapabilities": ["tools:read"]},
            ),
            SimpleNamespace(
                name="write", description=None, inputSchema={"type": "object"}, annotations=None, meta=None
            ),
            SimpleNamespace(
                name="vague", description="v", inputSchema={}, annotations=SimpleNamespace(readOnlyHint=None)
            ),
        ]
    )
    sessions.append(_FakeSession([tools]))
    out = await StdioMCPClient([StdioServerSpec("s")]).discover_tools("s")
    assert [(t["name"], t["annotations"]["readOnlyHint"], t["annotations"]["riskLevel"]) for t in out] == [
        ("read", True, "LOW"),
        ("write", False, None),
        ("vague", False, None),
    ]
    assert out[0]["annotations"]["requiredCapabilities"] == ["tools:read"] and out[1]["description"] == ""


def test_is_disconnect_classification():
    import anyio
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    assert mcp_client._is_disconnect(ConnectionError())
    assert mcp_client._is_disconnect(anyio.ClosedResourceError())
    assert mcp_client._is_disconnect(McpError(ErrorData(code=-32000, message="Connection closed")))
    assert not mcp_client._is_disconnect(McpError(ErrorData(code=-32602, message="invalid params")))
    assert not mcp_client._is_disconnect(ValueError("closed"))


def test_child_environment_forwards_only_the_listed_variables(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/src")
    monkeypatch.setenv("CEAP_MCP_PRINCIPAL_ROLES", "admin")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    env = StdioServerSpec("s", env={"PYTHONPATH": "/explicit"}).environment()
    assert env["PYTHONPATH"] == "/explicit" and env["CEAP_MCP_PRINCIPAL_ROLES"] == "admin"
    assert "ANTHROPIC_API_KEY" not in env


async def test_in_process_client_errors_are_mcp_errors():
    srv = MCPServerDefinition("calc", "test")

    @srv.tool("add")
    async def add(a: int, b: int = 1):
        return {"sum": a + b}

    c = InProcessMCPClient({"calc": srv})
    assert c.servers() == ["calc"] and [t["name"] for t in await c.discover_tools("calc")] == ["add"]
    assert await c.invoke("calc", "add", {"a": 2}) == {"sum": 3}
    with pytest.raises(MCPError, match="invalid arguments"):
        await c.invoke("calc", "add", {"c": 1})
    with pytest.raises(MCPError, match="sub"):
        await c.invoke("calc", "sub", {})
    with pytest.raises(MCPError, match="unknown MCP server: other"):
        await c.discover_tools("other")
    await c.close()  # the base hook is a no-op


async def test_real_connection_surfaces_a_spawn_failure_and_stop_is_idempotent():
    conn = mcp_client._StdioConnection(StdioServerSpec("ghost", command="ceap-no-such-binary-e1"))
    await conn.stop()  # never started: nothing to stop
    with pytest.raises(Exception):  # noqa: B017 - the OS error type differs by platform
        await asyncio.wait_for(conn.start(), 60)
    assert not conn.alive
    await conn.stop()
