"""R4 item 4.9: MCP schema contract tests.

Every tool every server advertises is checked against the MCP shape the stdio transport and the
harness rely on, and against a committed snapshot (``mcp_tools.json``). A schema change is therefore
always a reviewed diff. Regenerate after an intended change with ``CEAP_UPDATE_SNAPSHOTS=1``.

Integration note (0.5.0): the R2T lane may change TCA tool schemas; regenerate the snapshot on the
combined branch and review the diff.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ceap.domain.tools import RiskLevel
from ceap.mcp.registry import build_in_process_client

SNAPSHOT = Path(__file__).with_name("mcp_tools.json")
JSON_TYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}


async def _catalogue(mcp_client) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for server in sorted(mcp_client.servers()):
        for tool in await mcp_client.discover_tools(server):
            out[f"{server}.{tool['name']}"] = tool
    return out


def _types(schema: dict) -> set[str]:
    if "type" in schema:
        t = schema["type"]
        return set(t) if isinstance(t, list) else {t}
    return {t for sub in schema.get("anyOf", []) for t in _types(sub)}


async def test_every_tool_matches_the_mcp_contract(mcp_client):
    catalogue = await _catalogue(mcp_client)
    assert len(catalogue) >= 20
    for tool_id, tool in catalogue.items():
        assert tool["name"] and tool["description"].strip(), tool_id
        schema = tool["inputSchema"]
        assert schema["type"] == "object", tool_id
        props = schema.get("properties", {})
        assert set(schema.get("required", [])) <= set(props), tool_id
        for name, prop in props.items():
            assert _types(prop) <= JSON_TYPES and _types(prop), f"{tool_id}.{name}: {prop}"
        ann = tool["annotations"]
        assert isinstance(ann["readOnlyHint"], bool)
        assert ann["riskLevel"] in {r.value for r in RiskLevel}
        assert isinstance(ann["requiredCapabilities"], list)
        if not ann["readOnlyHint"]:  # a writing tool must be gated by a capability and not be LOW risk
            assert ann["requiredCapabilities"] and ann["riskLevel"] != "LOW", tool_id


async def test_staging_tool_accepts_the_approved_portfolio_hash(mcp_client):
    tool = (await _catalogue(mcp_client))["execution.stage_orders"]
    assert "approved_portfolio_hash" in tool["inputSchema"]["properties"]  # R4 4.5
    assert "approved_portfolio_hash" not in tool["inputSchema"].get("required", [])


async def test_tool_catalogue_matches_snapshot(mcp_client):
    catalogue = json.loads(json.dumps(await _catalogue(mcp_client), sort_keys=True))
    if os.getenv("CEAP_UPDATE_SNAPSHOTS") == "1" or not SNAPSHOT.exists():
        text = json.dumps(catalogue, indent=2, sort_keys=True) + "\n"
        SNAPSHOT.write_text(text, encoding="utf-8", newline="\n")
        if os.getenv("CEAP_UPDATE_SNAPSHOTS") != "1":
            pytest.fail(f"snapshot {SNAPSHOT.name} was missing and has been written; commit it")
    stored = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    added, removed = sorted(set(catalogue) - set(stored)), sorted(set(stored) - set(catalogue))
    changed = sorted(k for k in set(catalogue) & set(stored) if catalogue[k] != stored[k])
    assert not (added or removed or changed), (
        f"MCP tool contract changed: added={added} removed={removed} changed={changed}; "
        "if intended, regenerate with CEAP_UPDATE_SNAPSHOTS=1 and review the diff"
    )


def test_in_process_client_is_deterministic():
    a, b = build_in_process_client(), build_in_process_client()
    assert a.servers() == b.servers()
