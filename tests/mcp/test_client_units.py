"""``ceap.mcp.client`` without subprocesses: in-process errors, spec environment, disconnect
classification, the one-reconnect rule and result unwrapping in ``StdioMCPClient``.

The stdio connection is replaced with a fake that hands out scripted sessions, so these tests pin the
client's own logic; the real subprocess lifecycle is covered in tests/mcp/test_stdio_lifecycle.py.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from ceap.mcp import client as mc
from ceap.mcp.client import InProcessMCPClient, MCPError, StdioMCPClient, StdioServerSpec


class _Server:
    name = "srv"

    def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": "t"}]

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        if tool == "missing":
            raise KeyError("no tool missing")
        if tool == "typed":
            raise TypeError("unexpected keyword 'x'")
        return {"ok": True}


async def test_in_process_errors_become_mcp_errors() -> None:
    c = InProcessMCPClient()
    c.register(_Server())
    assert c.servers() == ["srv"]
    assert await c.discover_tools("srv") == [{"name": "t"}]
    assert await c.invoke("srv", "t", {}) == {"ok": True}
    with pytest.raises(MCPError, match="no tool missing"):
        await c.invoke("srv", "missing", {})
    with pytest.raises(MCPError, match="srv.typed: invalid arguments"):
        await c.invoke("srv", "typed", {"x": 1})
    with pytest.raises(MCPError, match="unknown MCP server: nope"):
        await c.discover_tools("nope")
    assert await c.close() is None


def test_spec_environment_forwards_only_listed_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "from-env")
    monkeypatch.setenv("VIRTUAL_ENV", "venv-env")
    monkeypatch.setenv("SECRET_TOKEN", "never")
    env = StdioServerSpec("s", env={"PYTHONPATH": "explicit"}).environment()
    assert env["PYTHONPATH"] == "explicit" and env["VIRTUAL_ENV"] == "venv-env"
    assert "SECRET_TOKEN" not in env


def test_is_disconnect_classification() -> None:
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    assert mc._is_disconnect(ConnectionResetError())
    assert mc._is_disconnect(anyio.ClosedResourceError())
    assert mc._is_disconnect(McpError(ErrorData(code=-1, message="Connection closed")))
    assert not mc._is_disconnect(McpError(ErrorData(code=-1, message="tool failed")))
    assert not mc._is_disconnect(ValueError("x"))


def test_read_only_hint_fails_closed() -> None:
    assert mc._read_only_hint(None) is False
    assert mc._read_only_hint(SimpleNamespace()) is False
    assert mc._read_only_hint(SimpleNamespace(readOnlyHint="yes")) is False
    assert mc._read_only_hint(SimpleNamespace(readOnlyHint=True)) is True


class _Session:
    def __init__(self, script: list[Any]) -> None:
        self.script = script

    async def _next(self) -> Any:
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        return await self._next()

    async def list_tools(self) -> Any:
        return await self._next()


class _FakeConn:
    """Stands in for ``_StdioConnection``; each start hands out the next scripted session."""

    sessions: list[_Session] = []
    started = 0
    stopped = 0

    def __init__(self, spec: StdioServerSpec) -> None:
        self.spec = spec
        self.session: Any = None

    @property
    def alive(self) -> bool:
        return self.session is not None

    async def start(self) -> Any:
        _FakeConn.started += 1
        self.session = _FakeConn.sessions.pop(0)
        return self.session

    async def stop(self, timeout: float = 5.0) -> None:
        _FakeConn.stopped += 1
        self.session = None


@pytest.fixture
def fake_conn(monkeypatch: pytest.MonkeyPatch) -> type[_FakeConn]:
    _FakeConn.sessions, _FakeConn.started, _FakeConn.stopped = [], 0, 0
    monkeypatch.setattr(mc, "_StdioConnection", _FakeConn)
    return _FakeConn


def _result(**kw: Any) -> SimpleNamespace:
    base = {"isError": False, "structuredContent": None, "content": []}
    return SimpleNamespace(**(base | kw))


def _text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_result(structuredContent={"result": [1, 2]}), [1, 2]),
        (_result(structuredContent={"result": 1, "other": 2}), {"result": 1, "other": 2}),
        (_result(content=[_text('{"a": 1}')]), {"a": 1}),
        (_result(content=[_text("plain words")]), "plain words"),
        (_result(content=[_text("a"), _text("b"), SimpleNamespace(type="image")]), ["a", "b"]),
    ],
)
async def test_invoke_unwraps_results(fake_conn: type[_FakeConn], result: Any, expected: Any) -> None:
    fake_conn.sessions = [_Session([result])]
    client = StdioMCPClient([StdioServerSpec("s")])
    assert await client.invoke("s", "t", {}) == expected


async def test_invoke_error_result_raises(fake_conn: type[_FakeConn]) -> None:
    fake_conn.sessions = [_Session([_result(isError=True, content=["bad input"])])]
    with pytest.raises(MCPError, match=r"s\.t failed: \['bad input'\]"):
        await StdioMCPClient([StdioServerSpec("s")]).invoke("s", "t", {})


async def test_unknown_server(fake_conn: type[_FakeConn]) -> None:
    with pytest.raises(MCPError, match="unknown MCP server: x"):
        await StdioMCPClient([StdioServerSpec("s")]).invoke("x", "t", {})


async def test_session_is_reused_and_close_stops_connections(fake_conn: type[_FakeConn]) -> None:
    fake_conn.sessions = [_Session([_result(structuredContent={"n": 1}), _result(structuredContent={"n": 2})])]
    client = StdioMCPClient([StdioServerSpec("s")])
    assert client.servers() == ["s"]
    assert await asyncio.gather(client.invoke("s", "t", {}), client.invoke("s", "t", {})) in (
        [{"n": 1}, {"n": 2}], [{"n": 2}, {"n": 1}])
    assert fake_conn.started == 1
    await client.close()
    assert fake_conn.stopped == 1


async def test_dead_connection_is_replaced_on_next_call(fake_conn: type[_FakeConn]) -> None:
    fake_conn.sessions = [_Session([_result(structuredContent={"n": 1})]), _Session([_result(structuredContent={"n": 2})])]
    client = StdioMCPClient([StdioServerSpec("s")])
    await client.invoke("s", "t", {})
    client._connections["s"].session = None  # the child exited between calls
    assert await client.invoke("s", "t", {}) == {"n": 2}
    assert fake_conn.started == 2 and fake_conn.stopped == 1


async def test_one_reconnect_then_success(fake_conn: type[_FakeConn]) -> None:
    fake_conn.sessions = [_Session([EOFError()]), _Session([_result(structuredContent={"n": 3})])]
    client = StdioMCPClient([StdioServerSpec("s")])
    assert await client.invoke("s", "t", {}) == {"n": 3}
    assert client.reconnects == 1


async def test_second_disconnect_is_an_mcp_error(fake_conn: type[_FakeConn]) -> None:
    fake_conn.sessions = [_Session([EOFError()]), _Session([ConnectionResetError("gone")])]
    with pytest.raises(MCPError, match="server exited again after one reconnect") as info:
        await StdioMCPClient([StdioServerSpec("s")]).invoke("s", "t", {})
    assert isinstance(info.value.__cause__, EOFError)


async def test_tool_errors_are_not_retried(fake_conn: type[_FakeConn]) -> None:
    fake_conn.sessions = [_Session([ValueError("bad")])]
    client = StdioMCPClient([StdioServerSpec("s")])
    with pytest.raises(ValueError, match="bad"):
        await client.invoke("s", "t", {})
    assert client.reconnects == 0


async def test_error_after_reconnect_that_is_not_a_disconnect_propagates(fake_conn: type[_FakeConn]) -> None:
    fake_conn.sessions = [_Session([EOFError()]), _Session([ValueError("tool broke")])]
    with pytest.raises(ValueError, match="tool broke"):
        await StdioMCPClient([StdioServerSpec("s")]).invoke("s", "t", {})


async def test_discover_tools_maps_annotations_and_meta(fake_conn: type[_FakeConn]) -> None:
    tools = SimpleNamespace(tools=[
        SimpleNamespace(name="a", description=None, inputSchema={"type": "object"}, annotations=None, meta=None),
        SimpleNamespace(name="b", description="d", inputSchema={}, annotations=SimpleNamespace(readOnlyHint=True),
                        meta={"riskLevel": "LOW", "requiredCapabilities": ["read"]}),
    ])
    fake_conn.sessions = [_Session([tools])]
    out = await StdioMCPClient([StdioServerSpec("s")]).discover_tools("s")
    assert out[0]["description"] == "" and out[0]["annotations"] == {
        "readOnlyHint": False, "riskLevel": None, "requiredCapabilities": []}
    assert out[1]["annotations"] == {"readOnlyHint": True, "riskLevel": "LOW", "requiredCapabilities": ["read"]}


async def test_connection_stop_without_start_and_timeout_cancels() -> None:
    conn = mc._StdioConnection(StdioServerSpec("s"))
    await conn.stop()  # never started: nothing to do
    assert not conn.alive

    async def hang() -> None:
        await asyncio.sleep(60)

    conn._task = asyncio.create_task(hang())
    task = conn._task
    await conn.stop(timeout=0.01)
    await asyncio.sleep(0)
    assert task.cancelled() or task.cancelling()


async def test_spawn_failure_surfaces_to_start_and_leaves_no_session(tmp_path: Any) -> None:
    conn = mc._StdioConnection(StdioServerSpec("s", command=str(tmp_path / "no-such-server.exe")))
    with pytest.raises(BaseException):  # noqa: B017 - the SDK's exact error type varies by platform
        await conn.start()
    assert conn.session is None and not conn.alive
    await conn.stop()  # the owner task already ended; stopping it is a no-op


async def test_stop_swallows_a_failing_owner_task() -> None:
    conn = mc._StdioConnection(StdioServerSpec("s"))

    async def boom() -> None:
        raise RuntimeError("teardown failed")

    conn._task = asyncio.create_task(boom())
    await conn.stop()
    assert conn._task is None
