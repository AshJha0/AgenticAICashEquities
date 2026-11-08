"""PLAN 3B.9: the stdio client's connection is task-owned and reconnects once when the child dies."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

from ceap.mcp.client import MCPError, StdioMCPClient, StdioServerSpec

SERVER = str(Path(__file__).parent / "fixtures" / "pid_server.py")
pytestmark = pytest.mark.slow


async def test_kill_the_child_reconnects_once() -> None:
    client = StdioMCPClient([StdioServerSpec("pid", sys.executable, [SERVER])])
    try:
        first = await client.invoke("pid", "pid", {})
        assert isinstance(first, int) and first != os.getpid()
        os.kill(first, signal.SIGTERM)
        second = await client.invoke("pid", "pid", {})  # the dead child is replaced, once
        assert isinstance(second, int) and second != first
        assert client.reconnects == 1
        assert await client.invoke("pid", "pid", {}) == second  # the new child is reused
    finally:
        await client.close()


async def test_connection_is_owned_by_a_dedicated_task_and_closes_cleanly() -> None:
    client = StdioMCPClient([StdioServerSpec("pid", sys.executable, [SERVER])])
    await client.invoke("pid", "pid", {})
    conn = client._connections["pid"]
    assert conn._task is not None and conn._task.get_name() == "mcp-stdio:pid"
    await client.close()
    assert conn.session is None and not client._connections


async def test_unknown_server_is_an_mcp_error() -> None:
    client = StdioMCPClient([])
    with pytest.raises(MCPError):
        await client.invoke("nope", "x", {})
