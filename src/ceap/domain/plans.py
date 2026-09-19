"""Plans and plan steps produced by the Planner and executed by the harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ceap.domain.tools import ToolRequest


class StepType(str, Enum):
    ANALYSIS = "ANALYSIS"
    TOOL_CALL = "TOOL_CALL"
    AGENT_CALL = "AGENT_CALL"
    VALIDATION = "VALIDATION"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    FINALISE = "FINALISE"


class PlanValidationError(ValueError):
    """Raised by the harness when a plan violates structural or policy rules."""


@dataclass(frozen=True)
class PlanStep:
    """A single, typed step.

    * ``TOOL_CALL``  -> ``tool_request`` must be set
    * ``AGENT_CALL`` -> ``agent_id`` must be set
    * ``VALIDATION`` / ``FINALISE`` / ``HUMAN_APPROVAL`` -> handled by the harness
    """

    id: str
    sequence: int
    type: StepType
    description: str
    tool_request: ToolRequest | None = None
    agent_id: str | None = None
    depends_on: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    id: str
    task_id: str
    steps: tuple[PlanStep, ...]
    rationale: str
    generated_by: str = "planner"

    def step(self, step_id: str) -> PlanStep:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    def ordered_steps(self) -> list[PlanStep]:
        return sorted(self.steps, key=lambda s: s.sequence)
