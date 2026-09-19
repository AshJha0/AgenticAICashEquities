"""Agent interface and the context the harness hands to each agent."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ceap.domain.evidence import Evidence
from ceap.domain.findings import Finding

if TYPE_CHECKING:  # pragma: no cover
    from ceap.domain.plans import Plan
    from ceap.domain.policy import PolicyContext
    from ceap.domain.tools import ToolRegistry


class ToolInvoker(ABC):
    """Narrow interface agents use to call tools *through the harness*.

    Agents never touch the ``ToolRegistry`` directly: every invocation goes
    through policy, retry, timeout and tracing in the harness executor.
    """

    @abstractmethod
    async def invoke(self, tool_id: str, arguments: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class AgentContext:
    task_id: str
    execution_id: str
    deadline: datetime
    task_input: dict[str, Any]
    current_plan: Plan | None
    evidence: tuple[Evidence, ...]
    state: dict[str, Any]
    tool_registry: ToolRegistry
    policy_context: PolicyContext
    cancellation_event: Any
    tools: ToolInvoker
    findings: tuple[Finding, ...] = ()

    def evidence_by_id(self, evidence_id: str) -> Evidence | None:
        for e in self.evidence:
            if e.id == evidence_id:
                return e
        return None


@dataclass(frozen=True)
class AgentResult:
    agent_id: str
    success: bool
    findings: tuple[Finding, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    summary: str = ""


class Agent(ABC):
    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def type(self) -> str: ...

    @abstractmethod
    async def execute(self, context: AgentContext) -> AgentResult: ...
