"""Domain model invariants."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ceap.domain.common import bps, new_id, to_jsonable
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.execution import Side
from ceap.domain.findings import Finding
from ceap.domain.market import OrderBookLevel, OrderBookSnapshot, Quote
from ceap.domain.plans import Plan, PlanStep, StepType
from ceap.domain.tasks import Task, TaskPriority
from ceap.domain.tools import ToolMetadata, ToolRegistry, ToolRequest, ToolResult, ToolStatus


def test_finding_confidence_must_be_in_unit_interval():
    with pytest.raises(ValueError):
        Finding.create("x", ["E-1"], 1.5)
    f = Finding.create("x", ["E-1"], 0.8)
    assert f.with_confidence(2.0).confidence == 1.0
    assert f.with_confidence(-1).confidence == 0.0
    assert f.with_confidence(0.5, note="n").attributes["note"] == "n"


def test_evidence_ids_are_prefixed_by_type():
    e = Evidence.create(EvidenceType.EXECUTION_DATA, "src", "desc")
    assert e.id.startswith("EXEC-")
    assert Evidence.create(EvidenceType.CALCULATION, "s", "d").id.startswith("TCA-")
    assert Evidence.create(EvidenceType.SIGNAL, "s", "d").id.startswith("SIG-")
    assert Evidence.create(EvidenceType.BACKTEST, "s", "d").id.startswith("BT-")
    assert new_id("X").startswith("X-") and len(new_id()) == 8


def test_tool_registry_rejects_duplicates_and_unknown():
    class T:
        id = "a.b"
        metadata = ToolMetadata("a.b", "b", "", True)

        async def execute(self, r, c):
            return ToolResult(ToolStatus.SUCCESS)

    reg = ToolRegistry()
    reg.register(T())
    with pytest.raises(ValueError):
        reg.register(T())
    with pytest.raises(KeyError):
        reg.get("nope")
    assert reg.has("a.b") and reg.ids() == ["a.b"] and len(reg) == 1


def test_plan_ordering_and_lookup():
    steps = (PlanStep("s2", 2, StepType.FINALISE, "f"), PlanStep("s1", 1, StepType.VALIDATION, "v"))
    plan = Plan("p", "t", steps, "r")
    assert [s.id for s in plan.ordered_steps()] == ["s1", "s2"]
    assert plan.step("s2").type is StepType.FINALISE
    with pytest.raises(KeyError):
        plan.step("zz")


def test_to_jsonable_handles_enums_datetimes_dataclasses():
    q = Quote("AAPL", datetime(2026, 9, 18, 13, tzinfo=UTC), 99.99, 100, 100.01, 200)
    out = to_jsonable({"side": Side.BUY, "q": q, "set": {1}, "req": ToolRequest("a.b", {"x": 1})})
    assert out["side"] == "BUY"
    assert out["q"]["timestamp"].startswith("2026-09-18T13:00:00")
    assert out["set"] == [1] and out["req"]["tool_id"] == "a.b"


def test_quote_and_order_book_helpers():
    q = Quote("AAPL", datetime(2026, 9, 18, tzinfo=UTC), 99.99, 100, 100.01, 200)
    assert q.mid == pytest.approx(100.0) and q.spread_bps == pytest.approx(2.0)
    assert not q.is_crossed and not q.is_locked
    book = OrderBookSnapshot(
        "AAPL",
        q.timestamp,
        (
            OrderBookLevel("BID", 1, 99.99, 300),
            OrderBookLevel("ASK", 1, 100.01, 100),
            OrderBookLevel("ASK", 2, 100.02, 100),
        ),
    )
    assert book.displayed_depth() == 500 and book.displayed_depth("ASK") == 200
    assert book.imbalance() == pytest.approx(0.2)
    assert [lv.level for lv in book.asks()] == [1, 2]


def test_task_and_bps_helpers():
    t = Task.create("desc", {"symbol": "AAPL"}, priority=TaskPriority.HIGH)
    assert t.id.startswith("TASK-") and t.input["symbol"] == "AAPL"
    assert bps(1, 100) == pytest.approx(100.0) and bps(1, 0) == 0.0
    assert Side.SELL.sign == -1
