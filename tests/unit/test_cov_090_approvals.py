"""Approval gateway edge cases (``ceap.policy.approvals``): four-eyes, content binding, one-winner
decisions, late decides, timeouts racing a human, cross-thread decides and the review payload.

The approval gate is the only thing between a planned order step and a staged order set, so each
refusal branch here has its own test.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import date
from types import SimpleNamespace

import pytest

from ceap.policy.approvals import (
    APPROVED,
    DISTINCT_FROM_KEY,
    EXPIRED,
    HASH_MISMATCH,
    PENDING,
    ApprovalConflictError,
    ApprovalDecision,
    ApprovalRequest,
    AutoApprovalGateway,
    ConcurrencySlot,
    DenyApprovalGateway,
    FourEyesViolationError,
    InMemoryApprovalStore,
    QueuedApprovalGateway,
    _num,
    approval_view,
    bind_to_content,
    check_four_eyes,
    released_slot,
    request_content_hash,
    review_payload,
)


def _req(requested_by: str = "alice", **args: object) -> ApprovalRequest:
    return ApprovalRequest.create("T1", "s1", "execution.stage_orders", {"qty": 10, **args}, "stage", requested_by)


class _Audit:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, dict]] = []

    def __call__(self, actor: str, action: str, subject: str, payload: dict) -> None:
        self.rows.append((actor, action, subject, payload))

    def actions(self) -> list[str]:
        return [r[1] for r in self.rows]


async def _until_pending(gw: QueuedApprovalGateway) -> ApprovalRequest:
    for _ in range(200):
        if gw.pending():
            return gw.pending()[0]
        await asyncio.sleep(0)
    raise AssertionError("request never became pending")


# ------------------------------------------------------------------ hashing and four-eyes
def test_request_hash_binds_tool_and_arguments_and_survives_odd_types():
    a = request_content_hash("t", {"x": 1})
    assert a == request_content_hash("t", {"x": 1})
    assert a != request_content_hash("t", {"x": 2}) and a != request_content_hash("u", {"x": 1})
    # dates and sets are not plain JSON: either way the hash is stable and still sees a change
    for v1, v2 in ((date(2024, 1, 2), date(2024, 1, 3)), ({1, 2}, {1, 3})):
        d = request_content_hash("t", {"when": v1})
        assert d == request_content_hash("t", {"when": v1}) and d != request_content_hash("t", {"when": v2})


def test_request_is_intact_until_its_arguments_change():
    r = _req()
    assert r.intact() and r.content_hash == r.current_hash()
    r.arguments["qty"] = 11  # the dict is mutable even though the dataclass is frozen
    assert not r.intact()


def test_four_eyes_rules():
    r = _req("alice", **{DISTINCT_FROM_KEY: ["bob"]})
    check_four_eyes(r, "carol")  # a different human passes
    for system in ("timeout", "policy", "auto-approver"):
        check_four_eyes(r, system)
    with pytest.raises(FourEyesViolationError, match="requested"):
        check_four_eyes(r, "alice")
    with pytest.raises(FourEyesViolationError, match="earlier stage"):
        check_four_eyes(r, "bob")


def test_bind_to_content_voids_an_approval_of_changed_arguments_only():
    r = _req()
    ok = ApprovalDecision(r.id, True, "bob")
    assert bind_to_content(r, ok) is ok
    r.arguments["qty"] = 999
    voided = bind_to_content(r, ok)
    assert not voided.approved and voided.decided_by == "policy" and voided.comment == HASH_MISMATCH
    assert voided.action == "reject"
    rejected = ApprovalDecision(r.id, False, "bob")
    assert bind_to_content(r, rejected) is rejected  # a refusal stays a refusal


async def test_simple_gateways():
    r = _req()
    deny = await DenyApprovalGateway().request(r)
    assert not deny.approved and deny.decided_by == "policy"
    assert DenyApprovalGateway().pending() == []
    auto = AutoApprovalGateway(keep=1)
    assert (await auto.request(r)).approved
    r2 = _req()
    r2.arguments["qty"] = 1  # tampered before the auto gateway saw it: still refused
    assert not (await auto.request(r2)).approved
    assert list(auto.log) == [r2]


# ------------------------------------------------------------------ store
def test_in_memory_store_one_winner_and_expiry():
    s = InMemoryApprovalStore()
    r = _req()
    assert s.status(r.id) is None
    s.insert(r)
    assert s.status(r.id) == PENDING
    assert s.transition(r.id, APPROVED, "bob", "", r.requested_at)
    assert not s.transition(r.id, "REJECTED", "carol", "", r.requested_at)  # loser
    assert s.status(r.id) == APPROVED
    r2 = _req()
    s.insert(r2)
    assert s.expire_pending("restart") == [r2.id] and s.status(r2.id) == EXPIRED
    assert s.expire_pending("again") == []


def test_in_memory_store_eviction_never_drops_a_pending_row():
    s = InMemoryApprovalStore(keep=1)
    first, second, third = _req(), _req(), _req()
    s.insert(first)
    s.insert(second)  # first is PENDING at the front: kept, the store may run over keep
    assert s.status(first.id) == PENDING
    s.transition(first.id, APPROVED, "bob", "", first.requested_at)
    s.transition(second.id, APPROVED, "bob", "", second.requested_at)
    s.insert(third)  # decided rows at the front are evicted down to keep
    assert s.status(first.id) is None and s.status(second.id) is None and s.status(third.id) == PENDING


# ------------------------------------------------------------------ queued gateway
async def test_decide_paths_refuse_before_any_state_change():
    audit = _Audit()
    gw = QueuedApprovalGateway(5.0, audit=audit)
    with pytest.raises(KeyError):
        gw.decide("APR-unknown", True, "bob")
    task = asyncio.create_task(gw.request(_req("alice")))
    r = await _until_pending(gw)
    with pytest.raises(FourEyesViolationError):
        gw.decide(r.id, True, "alice")
    with pytest.raises(ApprovalConflictError, match="content hash"):
        gw.decide(r.id, True, "bob", content_hash="0" * 64)
    with pytest.raises(ValueError, match="does not match"):
        gw.decide(r.id, True, "bob", action="reject")
    with pytest.raises(ValueError, match="does not match"):
        gw.decide(r.id, True, "bob", action="cancel")
    assert gw.store.status(r.id) == PENDING  # none of the refusals moved the row
    d = gw.decide(r.id, False, "bob", "too large", action="modify", content_hash=r.content_hash)
    assert d.action == "modify" and not d.approved
    out = await task
    assert out == d and list(gw.history) == [d] and gw.pending() == []
    assert audit.actions() == ["approval.requested", "approval.hash_mismatch", "approval.decided"]
    assert audit.rows[-1][3]["reason"] == "too large"
    # a second decide after the task returned is a late decide: 409 and audited
    with pytest.raises(ApprovalConflictError, match="REJECTED"):
        gw.decide(r.id, True, "carol")
    assert audit.actions()[-1] == "approval.late_decide"


async def test_timeout_expires_and_a_later_decide_is_refused():
    audit = _Audit()
    gw = QueuedApprovalGateway(0.01, audit=audit)
    d = await gw.request(_req())
    assert not d.approved and d.decided_by == "timeout"
    assert gw.store.status(d.request_id) == EXPIRED
    with pytest.raises(ApprovalConflictError, match="EXPIRED"):
        gw.decide(d.request_id, True, "bob")
    assert audit.actions() == ["approval.requested", "approval.expired", "approval.late_decide"]


async def test_a_human_decision_in_the_timeout_instant_stands():
    gw = QueuedApprovalGateway(0.01)
    human = {}

    class _RacyStore(InMemoryApprovalStore):
        def transition(self, approval_id, status, decided_by, comment, at):  # noqa: ANN001
            if status == EXPIRED:  # the human's write landed first
                fut, _ = gw._futures[approval_id]
                human["d"] = ApprovalDecision(approval_id, True, "bob")
                fut.set_result(human["d"])
                return False
            return super().transition(approval_id, status, decided_by, comment, at)

    gw.store = _RacyStore()
    d = await gw.request(_req())
    assert d is human["d"] and d.approved


async def test_lost_transition_is_a_conflict():
    audit = _Audit()
    gw = QueuedApprovalGateway(5.0, audit=audit)
    task = asyncio.create_task(gw.request(_req()))
    r = await _until_pending(gw)
    gw.store.transition(r.id, APPROVED, "carol", "", r.requested_at)  # another process won
    with pytest.raises(ApprovalConflictError, match="already decided"):
        gw.decide(r.id, False, "bob")
    assert audit.rows[-1][1] == "approval.late_decide" and audit.rows[-1][3]["status"] == APPROVED
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gw.pending() == []  # the finally block cleaned up


async def test_arguments_changed_while_waiting_turn_an_approval_into_a_refusal():
    audit = _Audit()
    gw = QueuedApprovalGateway(5.0, audit=audit)
    task = asyncio.create_task(gw.request(_req()))
    r = await _until_pending(gw)
    gw.decide(r.id, True, "bob")
    r.arguments["qty"] = 10_000  # mutated after the approver saw it
    d = await task
    assert not d.approved and d.comment == HASH_MISMATCH
    assert audit.actions()[-1] == "approval.hash_mismatch"


async def test_decide_from_another_thread_reaches_the_waiting_loop():
    gw = QueuedApprovalGateway(5.0)
    task = asyncio.create_task(gw.request(_req()))
    r = await _until_pending(gw)
    t = threading.Thread(target=gw.decide, args=(r.id, True, "bob"))
    t.start()
    t.join()
    d = await asyncio.wait_for(task, 2.0)
    assert d.approved and d.decided_by == "bob"


def test_expire_all_audits_each_pending_row():
    audit = _Audit()
    gw = QueuedApprovalGateway(audit=audit)
    a, b = _req(), _req()
    gw.store.insert(a)
    gw.store.insert(b)
    assert sorted(gw.expire_all("restart")) == sorted([a.id, b.id])
    assert audit.actions() == ["approval.expired", "approval.expired"]


async def test_waiting_for_a_human_releases_the_concurrency_slot():
    sem = asyncio.Semaphore(1)
    gw = QueuedApprovalGateway(5.0)

    async def parked() -> ApprovalDecision:
        async with ConcurrencySlot(sem) as slot:
            assert slot.held
            d = await gw.request(_req())
            assert slot.held  # taken back after the wait
            return d

    task = asyncio.create_task(parked())
    r = await _until_pending(gw)
    assert not sem.locked()  # a fifth task could start while this one waits
    gw.decide(r.id, True, "bob")
    assert (await task).approved
    assert not sem.locked()


async def test_released_slot_without_a_slot_is_a_no_op():
    async with released_slot():
        pass


# ------------------------------------------------------------------ review payload and view
def test_num_rejects_non_finite_and_non_numeric():
    assert _num("1.5") == 1.5 and _num(2) == 2.0
    for bad in (None, "x", float("nan"), float("inf"), float("-inf")):
        assert _num(bad) is None


def test_review_payload_groups_hypotheses_and_diffs_the_estimate():
    ref = SimpleNamespace(id="E1", path="window.is", value=12.5)
    f = SimpleNamespace(id="F1", statement="s" * 400, confidence=0.7, supporting_evidence=("E1", "E9"),
                        evidence_refs=(ref,))
    ev = {"E1": SimpleNamespace(type=SimpleNamespace(value="TOOL_OUTPUT"))}
    p = review_payload(
        hypotheses={"rows": [{"verdict": "SUPPORTED", "h": 1}, {"verdict": "REFUTED", "h": 2}, {"verdict": "X"}]},
        findings=[f] * 25, evidence=ev,
        execution={"window": {"implementation_shortfall_bps": 12.5}, "baseline": {"implementation_shortfall_bps": "10"}},
        critique={"contradictions": ["c1"]}, disputes={"checks": [{"check": "x"}]},
    )
    assert [r["h"] for r in p["hypotheses"]["SUPPORTED"]] == [1] and p["hypotheses"]["UNTESTED"] == []
    assert len(p["evidence_links"]) == 20 and len(p["evidence_links"][0]["statement"]) == 300
    assert p["evidence_links"][0]["evidence"] == [{"id": "E1", "type": "TOOL_OUTPUT"}, {"id": "E9", "type": None}]
    assert p["pre_trade_diff"]["diff_bps"] == 2.5
    assert p["disputes"]["contradictions"] == ["c1"] and p["disputes"]["recorded_without_check"] == []
    empty = review_payload(hypotheses=None, findings=[], evidence={}, execution=None, critique=None, disputes=None)
    assert empty["pre_trade_diff"] is None and empty["disputes"]["checks"] == []
    nodiff = review_payload(hypotheses=None, findings=[], evidence={},
                            execution={"window": {"implementation_shortfall_bps": float("nan")}},
                            critique=None, disputes=None)
    assert nodiff["pre_trade_diff"]["diff_bps"] is None


def test_approval_view_hides_bookkeeping_but_the_hash_covers_it():
    r = _req(review={"v": 1}, challenger={"agree": False})
    v = approval_view(r)
    assert v["arguments"] == {"qty": 10} and v["challenger"] == {"agree": False} and v["hash_verified"]
    r.arguments["review"]["v"] = 2  # a change to the hidden part still breaks the hash
    assert not approval_view(r)["hash_verified"]
    assert "challenger" not in approval_view(_req())
