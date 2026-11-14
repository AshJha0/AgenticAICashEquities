"""0.10.0 item H2 (finding R-12): a standalone MCP server never stages on capability alone.

The execution server is started as a real subprocess and driven over stdio with the ``admin`` role.
``stage_orders`` without a signed approval grant is refused; with a grant signed for exactly those
arguments the call reaches the tool.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from ceap.data.repositories import DatasetStore
from ceap.mcp.execution.server import build_server
from ceap.mcp.server import GRANT_KEY_ENV, PRINCIPAL_ROLES_ENV

pytest.importorskip("mcp")

SRC = str(Path(__file__).resolve().parents[2] / "src")
KEY = "test-grant-key"


async def _call(arguments: dict) -> tuple[bool, str]:
    from mcp.client.stdio import stdio_client

    from mcp import ClientSession, StdioServerParameters

    env = {**os.environ, PRINCIPAL_ROLES_ENV: "admin", GRANT_KEY_ENV: KEY, "PYTHONPATH": SRC}
    params = StdioServerParameters(command=sys.executable, args=["-m", "ceap.mcp.execution"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("stage_orders", arguments)
        text = " ".join(getattr(c, "text", "") for c in result.content)
        return bool(result.isError), text


async def test_stage_orders_over_stdio_without_a_grant_is_refused():
    args = {"signal": "momentum_12_1", "approved_portfolio_hash": "deadbeef"}
    is_error, text = await _call(args)
    assert is_error and "approval grant" in text

    forged = {**args, "approval_grant": build_server(DatasetStore()).sign_grant("stage_orders", args, "wrong-key")}
    is_error, text = await _call(forged)
    assert is_error and "approval grant" in text

    signed = {**args, "approval_grant": build_server(DatasetStore()).sign_grant("stage_orders", args, KEY)}
    is_error, text = await _call(signed)
    # the grant is accepted; the tool then refuses the stale hash, which proves the call got through
    assert is_error and "approval grant" not in text and "differs from the approved one" in text
