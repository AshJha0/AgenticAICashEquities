"""Explicit investigation lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ceap.domain.common import utc_now


class HarnessState(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    VALIDATING_PLAN = "VALIDATING_PLAN"
    EXECUTING = "EXECUTING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    CRITIQUING = "CRITIQUING"
    VALIDATING_EVIDENCE = "VALIDATING_EVIDENCE"
    FINALISING = "FINALISING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {HarnessState.COMPLETED, HarnessState.FAILED, HarnessState.CANCELLED}


_TERMINAL = {HarnessState.FAILED, HarnessState.CANCELLED}

TRANSITIONS: dict[HarnessState, set[HarnessState]] = {
    HarnessState.CREATED: {HarnessState.PLANNING} | _TERMINAL,
    HarnessState.PLANNING: {HarnessState.VALIDATING_PLAN} | _TERMINAL,
    HarnessState.VALIDATING_PLAN: {HarnessState.EXECUTING} | _TERMINAL,
    HarnessState.EXECUTING: {
        HarnessState.AWAITING_APPROVAL,
        HarnessState.CRITIQUING,
        HarnessState.VALIDATING_EVIDENCE,
        HarnessState.FINALISING,
    }
    | _TERMINAL,
    HarnessState.AWAITING_APPROVAL: {HarnessState.EXECUTING} | _TERMINAL,
    HarnessState.CRITIQUING: {HarnessState.EXECUTING, HarnessState.VALIDATING_EVIDENCE} | _TERMINAL,
    HarnessState.VALIDATING_EVIDENCE: {HarnessState.EXECUTING, HarnessState.FINALISING} | _TERMINAL,
    HarnessState.FINALISING: {HarnessState.COMPLETED} | _TERMINAL,
    HarnessState.COMPLETED: set(),
    HarnessState.FAILED: set(),
    HarnessState.CANCELLED: set(),
}


class InvalidTransition(RuntimeError):  # noqa: N818 - domain vocabulary
    pass


@dataclass(frozen=True)
class Transition:
    from_state: HarnessState
    to_state: HarnessState
    reason: str
    at: datetime


@dataclass
class StateMachine:
    state: HarnessState = HarnessState.CREATED
    history: list[Transition] = field(default_factory=list)
    #: guards for FINALISING (AI-2-11): the critic has reviewed the latest agent output, and the
    #: evidence has been validated after that critique
    critic_ran: bool = False
    evidence_validated: bool = False

    def mark_new_work(self) -> None:
        """An agent added output: it needs a fresh critique and a fresh evidence check."""
        self.critic_ran = self.evidence_validated = False

    def mark_critiqued(self) -> None:
        self.critic_ran, self.evidence_validated = True, False

    def mark_evidence_validated(self) -> None:
        self.evidence_validated = True

    def can(self, to: HarnessState) -> bool:
        if to is HarnessState.FINALISING and not (self.critic_ran and self.evidence_validated):
            return False
        return to in TRANSITIONS[self.state]

    def transition(self, to: HarnessState, reason: str = "") -> None:
        if to is HarnessState.FINALISING and not (self.critic_ran and self.evidence_validated):
            raise InvalidTransition(
                f"cannot finalise: critic_ran={self.critic_ran}, evidence_validated={self.evidence_validated}"
            )
        if not self.can(to):
            raise InvalidTransition(f"cannot move from {self.state.value} to {to.value}")
        self.history.append(Transition(self.state, to, reason, utc_now()))
        self.state = to

    def fail(self, reason: str) -> None:
        if self.state.terminal:
            return
        self.transition(HarnessState.FAILED, reason)

    def cancel(self, reason: str) -> None:
        if self.state.terminal:
            return
        self.transition(HarnessState.CANCELLED, reason)

    def export(self) -> list[dict[str, str]]:
        return [
            {"from": t.from_state.value, "to": t.to_state.value, "reason": t.reason, "at": t.at.isoformat()}
            for t in self.history
        ]
