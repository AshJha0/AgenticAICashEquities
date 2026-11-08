"""Agent interface and the context the harness hands to each agent."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

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


# ------------------------------------------------------------------ typed agent messages (R3B 3B.6)

#: version of the message envelope agents exchange; a message with any other version is rejected
MESSAGE_SCHEMA_VERSION: Literal[1] = 1


class MessageVersionError(ValueError):
    """A message whose envelope version drifted from :data:`MESSAGE_SCHEMA_VERSION`."""


class AgentMessage(BaseModel):
    """What one agent tells the others, built by the harness from an ``AgentResult``.

    Agents read each other through these (``BaseAgent.message``) instead of an untyped ``state``
    dict. ``data`` is the JSON form of the agent's output; findings and evidence travel by id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = MESSAGE_SCHEMA_VERSION
    sender: str
    kind: Literal["agent_output"] = "agent_output"
    success: bool
    summary: str = ""
    error: str | None = None
    finding_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    data: dict[str, Any] = {}


def parse_message(raw: Mapping[str, Any]) -> AgentMessage:
    """Validate a message from its JSON form; a drifted version is a :class:`MessageVersionError`."""
    version = raw.get("schema_version")
    if version != MESSAGE_SCHEMA_VERSION:
        raise MessageVersionError(f"agent message version {version!r} is not {MESSAGE_SCHEMA_VERSION}")
    try:
        return AgentMessage.model_validate(dict(raw))
    except ValidationError as exc:
        raise MessageVersionError(
            f"agent message does not match version {MESSAGE_SCHEMA_VERSION}: {exc}"
        ) from exc
