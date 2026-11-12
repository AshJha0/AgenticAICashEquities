"""The investigation risk agent in isolation: breached limits, respected limits, a missing exposure,
a limit check the plan did not fetch (the agent asks for it) and plan data that is missing.

Tool outputs are placed in memory by hand, so the arguments the agent must use are explicit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ceap.agents.base import PlanDataMissingError
from ceap.agents.risk import RiskAgent
from ceap.domain.agents import AgentContext, ToolInvoker
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.policy import PolicyContext
from ceap.domain.tools import ToolRegistry
from ceap.harness.memory import InvestigationMemory, ToolOutput

TASK = {"symbol": "AAPL", "window_start": "2024-03-01", "window_end": "2024-03-05",
        "baseline_start": "2024-02-01", "baseline_end": "2024-02-28", "dataset": "T01"}
WINDOW = {"symbol": "AAPL", "start": "2024-03-01", "end": "2024-03-05", "dataset": "T01"}


class _Tools(ToolInvoker):
    def __init__(self, memory: InvestigationMemory, replies: dict[str, Any]) -> None:
        self.memory, self.replies, self.calls = memory, replies, []

    async def invoke(self, tool_id: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool_id, dict(arguments)))
        if tool_id not in self.replies:
            raise AssertionError(f"unexpected tool call {tool_id}")
        _put(self.memory, tool_id, arguments, self.replies[tool_id])  # as the harness would record it
        return self.replies[tool_id]


def _put(mem: InvestigationMemory, tool_id: str, args: dict[str, Any], data: Any) -> str:
    ev = Evidence.create(EvidenceType.RISK, tool_id, tool_id)
    mem.add_evidence(ev)
    mem.tool_outputs.append(ToolOutput("s", tool_id, dict(args), data, (ev.id,), 1.0))
    return ev.id


def _limit_args(qty: int, part: Any) -> dict[str, Any]:
    return {"symbol": "AAPL", "order_quantity": qty, "participation_rate": part, "dataset": "T01"}


def _ctx(mem: InvestigationMemory, tools: ToolInvoker) -> AgentContext:
    return AgentContext(
        task_id="t", execution_id="x", deadline=datetime.now(UTC) + timedelta(minutes=1), task_input=TASK,
        current_plan=None, evidence=mem.evidence_list(), state={"memory": mem, "agent_outputs": {}},
        tool_registry=ToolRegistry(), policy_context=PolicyContext("p", frozenset(), frozenset(), "t"),
        cancellation_event=None, tools=tools, findings=(),
    )


async def test_breached_limits_name_each_limit_with_observed_and_limit_values():
    mem = InvestigationMemory()
    m_ev = _put(mem, "execution.get_execution_metrics", WINDOW,
                {"target_quantity": 50_000, "participation_rate": 0.31})
    checks = {"checks": [
        {"limit_name": "participation", "breached": True, "observed_value": 0.31, "limit_value": 0.2},
        {"limit_name": "notional", "breached": False, "observed_value": 1.0, "limit_value": 2.0},
        {"limit_name": "order_size", "breached": True, "observed_value": None, "limit_value": None},
    ]}
    l_ev = _put(mem, "risk.check_limit", _limit_args(50_000, 0.31), checks)
    _put(mem, "risk.get_exposure", {"symbol": "AAPL", "as_of": "2024-03-05", "dataset": "T01"},
         {"gross_exposure": 1_234_567.4, "notional_utilisation": 0.42})
    out = await RiskAgent().execute(_ctx(mem, _Tools(mem, {})))
    first, second = out.findings
    assert first.statement == (
        "The parent order breached trading limits: participation (0.31 vs 0.20), order_size (0.00 vs 0.00).")
    assert first.attributes == {"anomaly": "LIMIT_BREACH", "breached": ["participation", "order_size"]}
    assert set(first.supporting_evidence) == {l_ev, m_ev} and first.confidence == 0.9
    assert second.statement == ("Post-window gross exposure in AAPL was 1,234,567 USD (42% of the notional limit).")
    assert out.output["any_breached"] and out.summary == "limits breached"


async def test_respected_limits_report_peak_utilisation_and_skip_missing_exposure():
    mem = InvestigationMemory()
    # a non-finite participation is sent as None, and the cached limit check must match that exactly
    _put(mem, "execution.get_execution_metrics", WINDOW, {"target_quantity": None,
                                                          "participation_rate": float("nan")})
    _put(mem, "risk.check_limit", _limit_args(0, None),
         {"checks": [{"limit_name": "a", "utilisation": 0.25}, {"limit_name": "b", "utilisation": 0.8}]})
    _put(mem, "risk.get_exposure", {"symbol": "AAPL", "as_of": "2024-03-05", "dataset": "T01"},
         {"gross_exposure": None})
    out = await RiskAgent().execute(_ctx(mem, _Tools(mem, {})))
    assert [f.statement for f in out.findings] == ["Trading limits were respected (peak utilisation 80%)."]
    assert out.findings[0].attributes == {"utilisation": 0.8}
    assert not out.output["any_breached"] and out.summary == "limits respected"


async def test_unfetched_limit_check_is_invoked_with_the_task_arguments():
    mem = InvestigationMemory()
    _put(mem, "execution.get_execution_metrics", WINDOW, {"target_quantity": 10, "participation_rate": 0.05})
    _put(mem, "risk.get_exposure", {"symbol": "AAPL", "as_of": "2024-03-05", "dataset": "T01"}, {})
    tools = _Tools(mem, {"risk.check_limit": {"checks": []}})
    out = await RiskAgent().execute(_ctx(mem, tools))
    assert tools.calls == [("risk.check_limit", _limit_args(10, 0.05))]
    # no checks at all: utilisation defaults to 0 and the limits count as respected
    assert out.findings[0].statement == "Trading limits were respected (peak utilisation 0%)."
    assert out.findings[0].supporting_evidence  # the fresh call is backed by recorded evidence


async def test_missing_plan_data_fails_loudly_instead_of_fetching():
    mem = InvestigationMemory()
    tools = _Tools(mem, {})
    with pytest.raises(PlanDataMissingError, match="execution.get_execution_metrics"):
        await RiskAgent().execute(_ctx(mem, tools))
    assert tools.calls == []
