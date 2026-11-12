"""0.9.0 B7 wiring: the challenger agreement block reaches the approver only when a challenger plan exists."""

from __future__ import annotations

from ceap.domain.plans import Plan, PlanStep, StepType
from ceap.domain.tools import ToolRequest
from ceap.harness.common import APPROVAL_STEP_ID, CHALLENGER_PLAN_KEY, _approval_payload
from ceap.harness.memory import InvestigationMemory
from ceap.policy.approvals import ApprovalRequest, approval_view


def _primary() -> Plan:
    steps = tuple(
        PlanStep(id=f"s{i}", sequence=i, type=StepType.TOOL_CALL, description=t, tool_request=ToolRequest(t))
        for i, t in enumerate(["market.get_bars", "tca.compute", "venue.stats"])
    )
    return Plan(id="P1", task_id="T1", steps=steps, rationale="test")


def _step() -> PlanStep:
    return PlanStep(id=APPROVAL_STEP_ID, sequence=99, type=StepType.HUMAN_APPROVAL, description="approve")


def _request(payload: dict) -> ApprovalRequest:
    return ApprovalRequest(
        id="A1",
        task_id="T1",
        step_id=APPROVAL_STEP_ID,
        tool_id="harness.human_approval",
        arguments=payload,
        reason="test",
        requested_by="tester",
    )


def test_no_challenger_by_default() -> None:
    memory = InvestigationMemory()
    memory.scratch["plan"] = _primary()
    payload = _approval_payload(_step(), memory)
    assert "challenger" not in payload
    assert "challenger" not in approval_view(_request(payload))


def test_challenger_block_when_plan_present() -> None:
    memory = InvestigationMemory()
    memory.scratch["plan"] = _primary()
    challenger_plan = {
        "steps": [
            {"type": "TOOL_CALL", "tool_id": "market.get_bars"},
            {"type": "TOOL_CALL", "tool_id": "tca.compute"},
            {"type": "TOOL_CALL", "tool_id": "news.search"},
        ]
    }
    memory.scratch[CHALLENGER_PLAN_KEY] = {"plan": challenger_plan, "model": "llama3.1:8b-instruct-q4_K_M"}
    payload = _approval_payload(_step(), memory)
    view = approval_view(_request(payload))
    block = view["challenger"]
    # the worked example in ceap.eval.challenger: intersection 2, union 4 -> jaccard 0.5, partial
    assert block["level"] == "partial"
    assert block["jaccard"] == 0.5
    assert block["only_primary"] == ["venue.stats"]
    assert block["only_challenger"] == ["news.search"]
    assert block["challenger_model"] == "llama3.1:8b-instruct-q4_K_M"
    assert "challenger" not in view["arguments"]
    assert view["hash_verified"] is True
