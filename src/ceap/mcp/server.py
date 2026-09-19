"""Transport-agnostic MCP server definition.

A :class:`MCPServerDefinition` is a named collection of typed, documented
tools. It can be:

* called in-process (fast path for tests and the API), or
* exported to a real ``FastMCP`` server and run over stdio / streamable HTTP
  (``python -m ceap.mcp.market_data``).

The JSON schema for each tool is derived from the Python signature, so the
in-process and the network representation never drift apart.
"""

from __future__ import annotations

import functools
import inspect
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ceap.domain.common import to_jsonable
from ceap.domain.tools import RiskLevel

ToolFn = Callable[..., Awaitable[Any]]

_JSON_TYPES: dict[Any, dict[str, Any]] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    datetime: {"type": "string", "format": "date-time"},
    dict: {"type": "object"},
    list: {"type": "array"},
}


def _schema_for(annotation: Any) -> dict[str, Any]:
    origin = typing.get_origin(annotation)
    if origin is typing.Union or (origin is not None and str(origin) == "<class 'types.UnionType'>"):
        all_args = typing.get_args(annotation)
        args = [a for a in all_args if a is not type(None)]
        base = _schema_for(args[0]) if len(args) == 1 else {"anyOf": [_schema_for(a) for a in args]}
        if len(args) != len(all_args):  # Optional[...] -> nullable
            base = (
                {"anyOf": [base, {"type": "null"}]}
                if "anyOf" not in base
                else {"anyOf": [*base["anyOf"], {"type": "null"}]}
            )
        return base
    if origin in (list, tuple):
        return {"type": "array"}
    if origin is dict:
        return {"type": "object"}
    return _JSON_TYPES.get(annotation, {"type": "string"})


def schema_from_signature(fn: Callable[..., Any]) -> dict[str, Any]:
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)
    props: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        schema = _schema_for(hints.get(name, str))
        if param.default is inspect.Parameter.empty:
            required.append(name)
        else:
            schema = {**schema, "default": to_jsonable(param.default)}
        props[name] = schema
    return {"type": "object", "properties": props, "required": required}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    fn: ToolFn
    read_only: bool = True
    risk_level: RiskLevel = RiskLevel.LOW
    required_capabilities: frozenset[str] = frozenset()
    input_schema: dict[str, Any] = field(default_factory=dict)

    def descriptor(self) -> dict[str, Any]:
        """MCP-style tool descriptor (``tools/list`` shape plus our annotations)."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "readOnlyHint": self.read_only,
                "riskLevel": self.risk_level.value,
                "requiredCapabilities": sorted(self.required_capabilities),
            },
        }


class MCPServerDefinition:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self._tools: dict[str, ToolSpec] = {}

    # ------------------------------------------------------------ registration
    def tool(
        self,
        description: str,
        *,
        name: str | None = None,
        read_only: bool = True,
        risk_level: RiskLevel = RiskLevel.LOW,
        required_capabilities: frozenset[str] | set[str] = frozenset(),
    ) -> Callable[[ToolFn], ToolFn]:
        def decorator(fn: ToolFn) -> ToolFn:
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"MCP tool {fn.__name__} must be an async function")
            tool_name = name or fn.__name__
            if tool_name in self._tools:
                raise ValueError(f"duplicate tool {tool_name} on server {self.name}")
            self._tools[tool_name] = ToolSpec(
                name=tool_name,
                description=description,
                fn=fn,
                read_only=read_only,
                risk_level=risk_level,
                required_capabilities=frozenset(required_capabilities),
                input_schema=schema_from_signature(fn),
            )
            return fn

        return decorator

    # ------------------------------------------------------------------ access
    def list_tools(self) -> list[dict[str, Any]]:
        return [spec.descriptor() for spec in self._tools.values()]

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"{self.name}: unknown tool {name}") from exc

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        spec = self.get(name)
        result = await spec.fn(**(arguments or {}))
        return to_jsonable(result)

    # ---------------------------------------------------------------- FastMCP
    def to_fastmcp(self) -> Any:
        """Export as a real ``FastMCP`` server (requires the ``mcp`` package)."""
        from mcp.server.fastmcp import FastMCP
        from mcp.types import ToolAnnotations

        server = FastMCP(self.name, instructions=self.description)
        for spec in self._tools.values():
            server.add_tool(
                _jsonable_wrapper(spec.fn),
                name=spec.name,
                description=spec.description,
                annotations=ToolAnnotations(readOnlyHint=spec.read_only, destructiveHint=not spec.read_only),
                meta={
                    "riskLevel": spec.risk_level.value,
                    "requiredCapabilities": sorted(spec.required_capabilities),
                },
            )
        return server

    def run_stdio(self) -> None:  # pragma: no cover - process entry point
        self.to_fastmcp().run(transport="stdio")


def _jsonable_wrapper(fn: ToolFn) -> ToolFn:
    """FastMCP serialises return values; make sure dataclasses/NaN become JSON first."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return to_jsonable(await fn(*args, **kwargs))

    # resolve string annotations (PEP 563) so FastMCP does not look them up in this module
    wrapper.__annotations__ = typing.get_type_hints(fn)
    wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    return wrapper
