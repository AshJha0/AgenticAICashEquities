"""0.9.0 G5: durable runs - journal at step boundaries, resume, resumable approvals, idempotency keys.

Also the reproducibility check of the lane: the same investigation twice in one process gives the
same report (modulo random ids and wall-clock stamps).
"""

from __future__ import annotations

import re
from datetime import timedelta

from ceap.domain.common import to_jsonable, utc_now
from ceap.domain.tools import ToolRequest
from ceap.harness.durable import (
    InMemoryRunJournal,
    SqlRunJournal,
    approval_key,
    idempotency_key,
    result_from_payload,
    result_payload,
)
from ceap.observability.metrics import metrics
from ceap.persistence.db import Database
from ceap.persistence.repositories import SqlApprovalStore
from ceap.platform import default_request
from ceap.policy.approvals import ApprovalRequest

_ID = re.compile(r"\b[A-Z][A-Z0-9_]*-[0-9a-f]{6,}\b")
_VOLATILE = {"task_id", "execution_id", "generated_at", "trace_id", "started_at", "finished_at", "decided_at"}


def normalised(report) -> object:
    """The report with random ids and wall-clock fields removed (what must be reproducible)."""

    def walk(x):
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items() if k not in _VOLATILE}
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, str):
            return _ID.sub("<id>", x)
        return x

    return walk(to_jsonable(report))


def test_idempotency_key_ignores_correlation_id_and_argument_order():
    a = ToolRequest("execution.tca", {"symbol": "AAPL", "dataset": "T01"})
    b = ToolRequest("execution.tca", {"dataset": "T01", "symbol": "AAPL"})
    assert a.correlation_id != b.correlation_id
    assert idempotency_key("T1", "s1", a) == idempotency_key("T1", "s1", b)
    assert idempotency_key("T1", "s2", a) != idempotency_key("T1", "s1", a)
    assert idempotency_key("T2", "s1", a) != idempotency_key("T1", "s1", a)
    c = ToolRequest("execution.tca", {"symbol": "MSFT", "dataset": "T01"})
    assert idempotency_key("T1", "s1", c) != idempotency_key("T1", "s1", a)
    assert approval_key("s1", "h1") != approval_key("s1", "h2")


async def _run(platform, journal, req, task=None):
    harness = await platform.harness()
    harness.journal = journal
    task = task or platform.build_task(req)
    return task, await harness.execute(task, platform.policy_context(req, task))


def _replayed() -> float:
    return sum(
        v for k, v in metrics.snapshot().items() if k.startswith("ceap_tool_calls_total") and "replayed" in k
    )


async def test_rerun_with_journal_reads_tools_back_and_reproduces_the_report(platform):
    req = default_request("AAPL", "T01")
    journal = InMemoryRunJournal()
    task, first = await _run(platform, journal, req)
    assert first.success
    rows = len(journal)
    assert rows > 0
    before = _replayed()
    _, second = await _run(platform, journal, req, task)
    assert second.success
    assert len(journal) == rows  # nothing new journalled: every completed call was read back
    assert _replayed() - before == rows  # and every one of them was replayed, none re-invoked
    assert normalised(second.report)["narrative"] == normalised(first.report)["narrative"]
    assert normalised(second.report)["attribution"] == normalised(first.report)["attribution"]


async def test_same_report_twice_in_one_process_without_journal(platform):
    req = default_request("AAPL", "T01")
    _, a = await _run(platform, None, req)
    _, b = await _run(platform, None, req)
    assert a.success and b.success
    na, nb = normalised(a.report), normalised(b.report)
    for key in ("narrative", "attribution", "conclusion", "metrics", "executive_summary"):
        assert na[key] == nb[key], key


def test_sql_journal_round_trips_a_result_with_evidence(tmp_path, platform):
    from ceap.domain.evidence import Evidence, EvidenceType
    from ceap.domain.tools import ToolResult, ToolStatus

    ev = Evidence.create(EvidenceType.METRIC, "execution.tca", "IS 12.5 bps", utc_now(), {"bps": 12.5})
    res = ToolResult(ToolStatus.SUCCESS, {"is_bps": 12.5, "n": [1, 2]}, (ev,), None, 3.5)
    with Database(tmp_path / "j.db") as db:
        j = SqlRunJournal(db)
        assert j.put("T1", "k", "tool", "s1", result_payload(res))
        assert not j.put("T1", "k", "tool", "s1", result_payload(res))  # first write wins
    with Database(tmp_path / "j.db") as db:  # survives the process (a new connection)
        back = result_from_payload(SqlRunJournal(db).get("T1", "k")["payload"])
    assert back.data == res.data and back.status is res.status
    assert back.evidence[0].id == ev.id and back.evidence[0].attributes == {"bps": 12.5}
    assert back.evidence[0].timestamp == ev.timestamp


def test_decided_for_finds_the_decision_for_the_same_content(tmp_path):
    with Database(tmp_path / "a.db") as db:
        store = SqlApprovalStore(db)
        req = ApprovalRequest.create(
            "T1", "gov-approval", "harness.human_approval", {"x": 1}, "r", "trader:1"
        )
        store.insert(req)
        assert store.decided_for("T1", "gov-approval", req.content_hash) is None
        assert store.transition(req.id, "APPROVED", "user:alice", "ok", utc_now())
        row = store.decided_for("T1", "gov-approval", req.content_hash)
        assert row is not None and row["decided_by"] == "user:alice"
        assert store.decided_for("T1", "gov-approval", "other-hash") is None


def test_expire_stale_skips_live_and_recent_rows(tmp_path):
    with Database(tmp_path / "s.db") as db:
        store = SqlApprovalStore(db)
        old_live = ApprovalRequest.create("T1", "a", "t", {"i": 1}, "r", "p")
        old_orphan = ApprovalRequest.create("T1", "b", "t", {"i": 2}, "r", "p")
        recent = ApprovalRequest.create("T1", "c", "t", {"i": 3}, "r", "p")
        for r in (old_live, old_orphan, recent):
            store.insert(r)
        future = utc_now() + timedelta(seconds=1)
        db.run(
            lambda c: c.execute(
                "UPDATE approvals SET requested_at='2000-01-01T00:00:00Z' WHERE id IN (?,?)",
                (old_live.id, old_orphan.id),
            )
        )
        ids = store.expire_stale(future - timedelta(days=1), "ttl sweep", keep=frozenset({old_live.id}))
        assert ids == [old_orphan.id]
        assert store.status(old_orphan.id) == "EXPIRED"
        assert store.status(old_live.id) == "PENDING" and store.status(recent.id) == "PENDING"
