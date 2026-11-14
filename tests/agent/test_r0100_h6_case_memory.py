"""0.10.0 H6 (AI-2-06, -07, -08): case memory holds human decisions only; human wait is not task time.

Acceptance: auto-approved and timed-out decisions are absent from memory; a 10-minute simulated wait
does not time the task out.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from ceap.config import Settings
from ceap.domain.common import CLOCK
from ceap.harness.memory import NON_HUMAN_DECIDERS, CaseMemory, CaseRecord
from ceap.llm.client import MockLLMClient
from ceap.platform import Platform, default_request
from ceap.policy.approvals import SYSTEM_ACTORS, ApprovalDecision, ApprovalGateway


class Gateway(ApprovalGateway):
    """Decides every request as ``decided_by``; optionally lets ``wait`` pass on the clock first."""

    def __init__(self, decided_by: str, approved: bool = True, clock: FakeClock | None = None) -> None:
        self.decided_by, self.approved, self.clock = decided_by, approved, clock

    async def request(self, approval):
        if self.clock is not None:
            self.clock.offset += timedelta(minutes=10)  # the approver takes ten minutes
        return ApprovalDecision(approval.id, self.approved, self.decided_by, "decided")


class FakeClock:
    def __init__(self) -> None:
        self.offset = timedelta(0)

    def __call__(self) -> datetime:
        return datetime.now(UTC) + self.offset


async def _signed_off(store, history, approvals, **settings):
    memory = CaseMemory()
    p = Platform(
        settings=Settings(llm_provider="mock", db_path=":memory:", **settings),
        llm=MockLLMClient(),
        approvals=approvals,
        store=store,
        history=history,
        case_memory=memory,
    )
    req = default_request("AAPL", "T10")
    req.attributes = {"signoff": True}
    try:
        return await p.investigate(req), memory
    finally:
        p.close()


async def test_auto_approved_and_timed_out_decisions_are_absent_from_memory(store, history):
    assert NON_HUMAN_DECIDERS == SYSTEM_ACTORS  # memory's copy of the list is the policy's list
    _, auto = await _signed_off(store, history, None, auto_approve=True)
    _, timed_out = await _signed_off(store, history, Gateway("timeout", approved=False))
    human_result, human = await _signed_off(store, history, Gateway("approver.b"))
    assert len(auto) == 0 and auto.writes == 0
    assert len(timed_out) == 0 and timed_out.writes == 0
    assert len(human) == 1  # the control: a person's decision is recorded
    assert human_result.agent_outputs["case_memory"]["written"].startswith("CASE-")


async def test_a_ten_minute_wait_for_the_approver_does_not_time_the_task_out(store, history):
    clock = FakeClock()
    token = CLOCK.set(clock)
    try:
        result, _ = await _signed_off(
            store, history, Gateway("approver.b", clock=clock), task_timeout_seconds=300
        )
    finally:
        CLOCK.reset(token)
    assert clock.offset == timedelta(minutes=10)  # the wait happened, and is longer than the budget
    assert result.success, result.error


def test_cases_stay_with_their_owner_and_expire():
    now = datetime(2026, 11, 14, tzinfo=UTC)
    memory = CaseMemory(ttl_days=90, now=lambda: now)

    def case(case_id: str, owner: str, age_days: int) -> CaseRecord:
        at = (now - timedelta(days=age_days)).isoformat()
        return CaseRecord(
            case_id, "T-1", "T01", "investigation", "AAPL", "why", "APPROVED", "ann", "", (), at, owner
        )

    assert memory.append(case("CASE-a", "trader.a", 1))
    assert memory.append(case("CASE-old", "trader.a", 91))
    assert not memory.append(replace(case("CASE-auto", "trader.a", 1), decided_by="auto-approver"))
    ids = lambda views: [v["case_id"] for v in views]  # noqa: E731
    assert ids(memory.nearest("T01", "why AAPL", owner="trader.a")) == ["CASE-a"]  # the old case expired
    assert ids(memory.nearest("T01", "why AAPL", owner="trader.b")) == []  # another principal sees nothing
    assert ids(memory.nearest("T01", "why AAPL", owner="trader.b", read_any=True)) == ["CASE-a"]
