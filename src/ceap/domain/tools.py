"""Tool abstraction.

A ``Tool`` is any capability the harness can invoke on behalf of an agent.
MCP tools are exposed through an adapter that implements this interface;
the harness never talks MCP directly.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from ceap.domain.common import new_id

if TYPE_CHECKING:  # pragma: no cover
    from ceap.domain.evidence import Evidence


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TransientToolError(RuntimeError):
    """A transport-level failure the control plane may retry (connection reset, timeout)."""


class ToolStatus(str, Enum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    DENIED = "DENIED"
    TIMEOUT = "TIMEOUT"
    PENDING_APPROVAL = "PENDING_APPROVAL"


@dataclass(frozen=True)
class ToolMetadata:
    id: str
    name: str
    description: str
    read_only: bool
    risk_level: RiskLevel = RiskLevel.LOW
    required_capabilities: frozenset[str] = frozenset()
    input_schema: dict[str, Any] = field(default_factory=dict)
    server: str | None = None


@dataclass(frozen=True)
class ToolRequest:
    tool_id: str
    arguments: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = field(default_factory=lambda: new_id("CORR"))


@dataclass(frozen=True)
class ToolResult:
    status: ToolStatus
    data: Any = None
    evidence: tuple[Evidence, ...] = ()
    error: str | None = None
    execution_time_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status is ToolStatus.SUCCESS


@dataclass(frozen=True)
class ToolExecutionContext:
    task_id: str
    execution_id: str
    step_id: str
    deadline: datetime
    principal: str
    capabilities: frozenset[str] = frozenset()


class Tool(ABC):
    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def metadata(self) -> ToolMetadata: ...

    @abstractmethod
    async def execute(self, request: ToolRequest, context: ToolExecutionContext) -> ToolResult: ...


class ToolRegistry:
    """In-memory registry of tools addressable by id (``server.tool``)."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.id in self._tools:
            raise ValueError(f"Tool already registered: {tool.id}")
        self._tools[tool.id] = tool

    def get(self, tool_id: str) -> Tool:
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {tool_id}") from exc

    def has(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def list(self) -> builtins.list[ToolMetadata]:
        return [t.metadata for t in self._tools.values()]

    def ids(self) -> builtins.list[str]:
        return sorted(self._tools)

    def __len__(self) -> int:
        return len(self._tools)
