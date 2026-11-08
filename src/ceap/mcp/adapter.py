"""Adapts a discovered MCP tool to the domain ``Tool`` interface.

Every invocation yields an :class:`Evidence` record carrying the tool id,
arguments, a content digest and a compact summary, so the audit trail is
complete even before any agent interprets the data.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from ceap.domain.common import SCHEMA_VERSION, to_jsonable, utc_now
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.tools import (
    RiskLevel,
    Tool,
    ToolExecutionContext,
    ToolMetadata,
    ToolRequest,
    ToolResult,
    ToolStatus,
    TransientToolError,
)
from ceap.mcp.client import MCPClient, MCPError

_SERVER_EVIDENCE = {
    "market_data": EvidenceType.MARKET_DATA,
    "execution": EvidenceType.EXECUTION_DATA,
    "risk": EvidenceType.RISK,
    "engineering": EvidenceType.SYSTEM_METRIC,
    "research_data": EvidenceType.MARKET_DATA,
    "alpha": EvidenceType.SIGNAL,
    "backtest": EvidenceType.BACKTEST,
    "knowledge": EvidenceType.DOCUMENT,  # R3B 3B.4: retrieved policy text is DOC evidence
}
MAX_BOUND_VALUES = 40
_TOOL_EVIDENCE = {
    "get_order_book": EvidenceType.ORDER_BOOK,
    "get_parent_orders": EvidenceType.ORDER_DATA,
    "get_child_orders": EvidenceType.ORDER_DATA,
    "stage_orders": EvidenceType.ORDER_DATA,
    "get_staged_orders": EvidenceType.ORDER_DATA,
    "get_execution_metrics": EvidenceType.CALCULATION,
    "get_venue_statistics": EvidenceType.CALCULATION,
    "get_market_statistics": EvidenceType.CALCULATION,
    "search_logs": EvidenceType.LOG,
    "get_deployments": EvidenceType.CODE_CHANGE,
}


def _digest(data: Any) -> str:
    payload = json.dumps(to_jsonable(data), sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _summarise(data: Any) -> str:
    if isinstance(data, dict):
        if "count" in data:
            return f"{data['count']} records"
        keys = ", ".join(list(data)[:6])
        return f"object with keys: {keys}"
    if isinstance(data, list):
        return f"{len(data)} records"
    return type(data).__name__


def _risk(value: Any) -> RiskLevel:
    """Missing or unknown ``riskLevel`` means HIGH (fail closed)."""
    try:
        return RiskLevel(value) if value is not None else RiskLevel.HIGH
    except ValueError:
        return RiskLevel.HIGH


class MCPToolAdapter(Tool):
    """Fail closed on annotations (PLAN 1.7): a missing ``readOnlyHint`` means *not* read-only and
    a missing ``riskLevel`` means HIGH. Whether the server is trusted is decided by the policy engine
    (``trusted_servers``), which denies every write to a tool of an untrusted server."""

    def __init__(self, client: MCPClient, server: str, descriptor: dict[str, Any]) -> None:
        self._client = client
        self._server = server
        self._name = descriptor["name"]
        ann = descriptor.get("annotations", {}) or {}
        self._metadata = ToolMetadata(
            id=f"{server}.{self._name}",
            name=self._name,
            description=descriptor.get("description", ""),
            read_only=ann.get("readOnlyHint") is True,
            risk_level=_risk(ann.get("riskLevel")),
            required_capabilities=frozenset(ann.get("requiredCapabilities", [])),
            input_schema=descriptor.get("inputSchema", {}),
            server=server,
        )

    @property
    def id(self) -> str:
        return self._metadata.id

    @property
    def metadata(self) -> ToolMetadata:
        return self._metadata

    async def execute(self, request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
        started = time.perf_counter()
        try:
            data = await self._client.invoke(self._server, self._name, dict(request.arguments))
        except MCPError as exc:
            return ToolResult(status=ToolStatus.ERROR, error=str(exc), execution_time_ms=_ms(started))
        except (ConnectionError, OSError, TimeoutError) as exc:  # transport faults: let the harness retry
            raise TransientToolError(f"{self.id}: transport error: {type(exc).__name__}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - surface as tool error, harness decides on retry
            return ToolResult(
                status=ToolStatus.ERROR, error=f"{type(exc).__name__}: {exc}", execution_time_ms=_ms(started)
            )
        version = data.get("schema_version") if isinstance(data, dict) else None
        if version is not None and version != SCHEMA_VERSION:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"{self.id}: schema_version {version!r} drifted from {SCHEMA_VERSION}; result rejected",
                execution_time_ms=_ms(started),
            )
        evidence = Evidence.create(
            type=_TOOL_EVIDENCE.get(
                self._name, _SERVER_EVIDENCE.get(self._server, EvidenceType.SYSTEM_METRIC)
            ),
            source=self.id,
            description=f"{self.id}({_args_preview(request.arguments)}) -> {_summarise(data)}",
            timestamp=utc_now(),
            attributes={
                "tool_id": self.id,
                "arguments": to_jsonable(request.arguments),
                "correlation_id": request.correlation_id,
                "task_id": context.task_id,
                "step_id": context.step_id,
                "digest": _digest(data),
                # R3B 3B.4: top-level scalar values, so a finding can cite ``values.<field>``
                "values": _scalar_values(data),
            },
        )
        return ToolResult(
            status=ToolStatus.SUCCESS, data=data, evidence=(evidence,), execution_time_ms=_ms(started)
        )


def _scalar_values(data: Any) -> dict[str, Any]:
    """The first ``MAX_BOUND_VALUES`` top-level scalar fields of a dict result (the citable values)."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in data.items():
        if len(out) >= MAX_BOUND_VALUES:
            break
        if isinstance(value, (int, float, str, bool)) or value is None:
            out[str(key)] = value if not isinstance(value, str) else value[:200]
    return out


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def _args_preview(args: dict[str, Any]) -> str:
    parts = []
    for k, v in args.items():
        s = str(v)
        parts.append(f"{k}={s[:32]}")
    return ", ".join(parts)
