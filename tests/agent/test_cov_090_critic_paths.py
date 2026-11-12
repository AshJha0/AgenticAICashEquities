"""Critic branches the end-to-end runs rarely reach: a deployment without a latency anomaly, the
research caps, and every path of the optional model critique (kill switch, crash, repair, unusable
reply, named and unbacked disputes). The rule under test throughout: the model may comment and
dispute, but only the deterministic checks change a confidence.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ceap.agents.critic import CriticAgent
from ceap.domain.agents import AgentContext, ToolInvoker
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.findings import Finding
from ceap.domain.policy import PolicyContext
from ceap.domain.tools import ToolRegistry
from ceap.harness.memory import InvestigationMemory
from ceap.llm.client import LLMClient
from ceap.llm.models import LLMRequest, LLMResponse


class _NoTools(ToolInvoker):
    async def invoke(self, tool_id: str, arguments: dict[str, Any]) -> Any:
        raise AssertionError("the critic must not call tools")


class _Scripted(LLMClient):
    """Returns the scripted replies in order; a reply that is an exception is raised."""

    def __init__(self, *replies: Any, disabled: bool = False) -> None:
        self.replies = list(replies)
        self.requests: list[LLMRequest] = []
        self.disabled = disabled

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def _resp(parsed: dict | None = None, content: str = "", parse_errors: tuple[str, ...] = ()) -> LLMResponse:
    return LLMResponse(content, [], 0, 0, "scripted", parsed=parsed, parse_errors=parse_errors)


def _setup(n_ev: int = 2, **finding_kw: Any) -> tuple[InvestigationMemory, Finding]:
    mem = InvestigationMemory()
    evs = [Evidence.create(EvidenceType.CALCULATION, "t", f"d{i}") for i in range(n_ev)]
    mem.add_evidence(*evs)
    kw = {"statement": "a plain statement", "confidence": 0.9, **finding_kw}
    f = Finding.create(kw.pop("statement"), [e.id for e in evs], kw.pop("confidence"), **kw)
    return mem, f


def _ctx(mem: InvestigationMemory, findings: list[Finding], outputs: dict | None = None,
         kind: str = "") -> AgentContext:
    return AgentContext(
        task_id="t", execution_id="x", deadline=datetime.now(UTC) + timedelta(minutes=1),
        task_input={"kind": kind} if kind else {}, current_plan=None, evidence=mem.evidence_list(),
        state={"memory": mem, "agent_outputs": outputs or {}}, tool_registry=ToolRegistry(),
        policy_context=PolicyContext("p", frozenset(), frozenset(), "t"), cancellation_event=None,
        tools=_NoTools(), findings=tuple(findings),
    )


def _codes(result: Any) -> list[str]:
    return [d["code"] for d in result.output.get("degradations", [])]


# ------------------------------------------------------------------ deterministic branches
async def test_deployment_without_a_latency_anomaly_is_context_not_cause():
    mem, f = _setup(attributes={"anomaly": "CODE_CHANGE"})
    out = await CriticAgent().execute(_ctx(mem, [f], {"engineering": {"latency_ratio": 1.2}}))
    adj = out.output["adjusted_findings"][0]
    assert adj.confidence == 0.6 and any("context, not cause" in n for n in adj.attributes["critic_notes"])
    # with a real latency spike the deployment claim keeps its confidence
    hot = await CriticAgent().execute(_ctx(mem, [f], {"engineering": {"latency_ratio": 5.0}}))
    assert hot.output["adjusted_findings"][0].confidence == 0.9


async def test_overall_text_counts_lowered_and_unsupported():
    mem, f = _setup()
    out = await CriticAgent().execute(_ctx(mem, [f]))
    assert out.output["critique"]["overall"] == (
        "Reviewed 1 findings: 1 supported by resolvable evidence, 0 unsupported. No cross-agent contradictions."
    )
    assert "prompt" not in out.output and "degradations" not in out.output


async def test_research_caps_follow_the_deterministic_assessment():
    # no alpha, backtest or risk output: the assessment is REJECT with INCOMPLETE_ANALYSIS only
    mem, _ = _setup()
    ids = [e.id for e in mem.evidence_list()]

    def mk(stmt: str, **attrs: Any) -> Finding:
        return Finding.create(stmt, ids, 0.9, attributes=attrs)

    alpha, complete, flag_ok, flag_bad, profitable = (
        mk("alpha", claim="ALPHA"), mk("complete", coverage="COMPLETE"),
        mk("incomplete", flag="INCOMPLETE_ANALYSIS"), mk("decay", flag="OOS_DECAY"),
        mk("profitable", claim="PROFITABLE"),
    )
    out = await CriticAgent().execute(_ctx(mem, [alpha, complete, flag_ok, flag_bad, profitable], kind="research"))
    conf = {f.statement: f.confidence for f in out.output["adjusted_findings"]}
    assert conf == {"alpha": 0.4, "complete": 0.5, "incomplete": 0.9, "decay": 0.6, "profitable": 0.9}
    a = out.output["assessment"]
    assert a["verdict"] == "REJECT" and a["evidence_id"]
    capped = {f.statement: f for f in out.output["adjusted_findings"]}["alpha"]
    assert a["evidence_id"] in capped.contradicting_evidence
    rec = {r["finding_id"]: r for r in out.output["critique"]["assessments"]}[alpha.id]
    assert rec["adjusted_confidence"] == 0.4 and "promotion threshold" in rec["notes"][-1]
    assert "Research assessment: REJECT (INCOMPLETE_ANALYSIS)" in out.output["critique"]["overall"]


# ------------------------------------------------------------------ model critique
async def test_kill_switched_model_is_never_called():
    mem, f = _setup()
    llm = _Scripted(disabled=True)
    out = await CriticAgent(llm).execute(_ctx(mem, [f]))
    assert _codes(out) == ["LLM_DISABLED"] and llm.requests == [] and "prompt" not in out.output


async def test_model_crash_keeps_the_deterministic_critique():
    mem, f = _setup()
    out = await CriticAgent(_Scripted(RuntimeError("boom"))).execute(_ctx(mem, [f]))
    assert _codes(out) == ["LLM_CRITIQUE_FAILED"]
    assert "RuntimeError: boom" in out.output["degradations"][0]["message"]
    assert out.output["adjusted_findings"][0].confidence == 0.9 and "llm_overall" not in out.output["critique"]


async def test_cancellation_is_not_swallowed():
    mem, f = _setup()
    with pytest.raises(asyncio.CancelledError):
        await CriticAgent(_Scripted(asyncio.CancelledError())).execute(_ctx(mem, [f]))


async def test_named_dispute_goes_to_the_harness_and_unbacked_is_only_recorded():
    mem, f = _setup()
    g = Finding.create("another statement", list(f.supporting_evidence), 0.9)
    reply = {"assessments": [
        {"finding_id": f.id, "supported": False, "adjusted_confidence": 0.3, "comment": "c" * 600,
         "check": "peer_set"},
        {"finding_id": g.id, "supported": True, "adjusted_confidence": 0.1, "comment": "lower", "check": None},
    ], "overall": "model view"}
    llm = _Scripted(_resp(parsed=reply))
    out = await CriticAgent(llm).execute(_ctx(mem, [f, g]))
    crit = out.output["critique"]
    assert crit["disputes"] == [{"finding_id": f.id, "check": "peer_set"}]
    assert crit["unbacked_disputes"] == [g.id]
    assert crit["llm_overall"] == "model view" and out.output["prompt"]
    rec = {r["finding_id"]: r for r in crit["assessments"]}
    assert len(rec[f.id]["llm_comment"]) == 500 and rec[f.id]["llm_proposed_confidence"] == 0.3
    # the model never sets a confidence: f keeps the deterministic 0.9
    assert {x.id: x.confidence for x in out.output["adjusted_findings"]}[f.id] == 0.9
    assert len(llm.requests) == 1


async def test_parse_errors_trigger_one_repair_that_can_succeed():
    mem, f = _setup()
    good = {"assessments": [{"finding_id": f.id, "supported": True, "adjusted_confidence": 0.9,
                             "comment": "fine"}], "overall": "ok"}
    llm = _Scripted(_resp(parse_errors=("bad json",)), _resp(content=json.dumps(good)))
    out = await CriticAgent(llm).execute(_ctx(mem, [f]))
    assert _codes(out) == ["LLM_REPAIR_RETRY"] and len(llm.requests) == 2
    repair_msg = llm.requests[1].messages[-1]["content"]
    assert "bad json" in repair_msg
    assert out.output["critique"]["llm_overall"] == "ok"
    assert out.output["critique"]["disputes"] == [] and out.output["critique"]["unbacked_disputes"] == []


async def test_an_empty_reply_twice_is_unusable_and_changes_nothing():
    mem, f = _setup()
    llm = _Scripted(_resp(content=""), _resp(content=""))
    out = await CriticAgent(llm).execute(_ctx(mem, [f]))
    assert _codes(out) == ["LLM_REPAIR_RETRY", "LLM_CRITIQUE_UNUSABLE"]
    assert "llm_overall" not in out.output["critique"]
    assert "llm_comment" not in out.output["critique"]["assessments"][0]


async def test_a_reply_about_no_findings_needs_no_repair():
    mem = InvestigationMemory()
    llm = _Scripted(_resp(content="not json"))
    out = await CriticAgent(llm).execute(_ctx(mem, []))
    assert len(llm.requests) == 1 and "LLM_REPAIR_RETRY" not in _codes(out)


# ------------------------------------------------------------------ planted-fault scorer helpers
def test_planted_flagged_ignores_only_the_single_evidence_cap():
    from ceap.agents.critic import CAP_FLAG
    from ceap.agents.critic_planted import flagged

    ok = {"finding_id": "F1", "supported": True, "flags": [CAP_FLAG]}
    assert not flagged(ok, ["F2: something else", "F10: prefix is exact"])
    assert flagged({**ok, "supported": False}, [])
    assert flagged({**ok, "flags": [CAP_FLAG, "numeric_mismatch"]}, [])
    assert flagged({"finding_id": "F1", "supported": True}, ["F1: contradicted"])


def test_planted_scorer_context_refuses_tool_calls_and_main_prints_json(monkeypatch, capsys):
    from ceap.agents import critic_planted as cp

    with pytest.raises(AssertionError, match="must not call tools"):
        asyncio.run(cp._NoTools().invoke("x", {}))
    monkeypatch.setattr(cp, "report", lambda: {"existing": {"detection": 1.0}})
    cp.main()
    assert json.loads(capsys.readouterr().out) == {"existing": {"detection": 1.0}}
