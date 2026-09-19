"""Policy: the authority that decides whether a tool request may run."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ceap.domain.tools import ToolMetadata, ToolRequest


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


@dataclass(frozen=True)
class PolicyContext:
    principal: str
    roles: frozenset[str]
    capabilities: frozenset[str]
    task_id: str
    environment: str = "dev"
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyEvaluation:
    decision: PolicyDecision
    reason: str
    rule: str
    tool_id: str


class PolicyEngine(ABC):
    @abstractmethod
    async def evaluate(
        self, request: ToolRequest, metadata: ToolMetadata, context: PolicyContext
    ) -> PolicyEvaluation: ...
