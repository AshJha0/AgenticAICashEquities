"""Planted-fault eval of the deterministic critic (0.9.0, lane L3).

Plain idea: hand the critic findings with known faults (uncited, unresolved, contradicted, wrong
number, wrong direction) mixed with clean controls, run it with no model, and count what it
catches. Two sets: the 0.8.0 set built by ``ceap.eval.agents.critic_cases`` and a held-out set,
``data/eval/critic_planted_heldout_v1.json``, committed before the 0.9.0 critic change.

Formal statement: a finding is *flagged* when the critic marks it unsupported, records a
contradiction against it, or attaches a check flag (``assessment["flags"]``) other than the
single-evidence cap. Detection = flagged planted / planted; false alarm = flagged controls /
controls; both with 95 % Wilson intervals (``ceap.eval.stats.rate``).

Run ``python -m ceap.agents.critic_planted`` for the table.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ceap.agents.critic import CAP_FLAG, CriticAgent
from ceap.domain.agents import AgentContext, ToolInvoker
from ceap.domain.evidence import Evidence, EvidenceType
from ceap.domain.findings import Finding
from ceap.domain.policy import PolicyContext
from ceap.domain.tools import ToolRegistry
from ceap.eval.stats import rate
from ceap.harness.memory import InvestigationMemory

HELDOUT = Path(__file__).resolve().parents[3] / "data" / "eval" / "critic_planted_heldout_v1.json"


class _NoTools(ToolInvoker):
    async def invoke(self, tool_id: str, arguments: dict[str, Any]) -> Any:
        raise AssertionError("the critic must not call tools")


def heldout_cases() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads(HELDOUT.read_text(encoding="utf-8"))["batches"]
    return data


def existing_cases() -> list[dict[str, Any]]:
    from ceap.eval.agents import critic_cases

    return critic_cases()


def _context(batch: dict[str, Any]) -> AgentContext:
    memory = InvestigationMemory()
    findings = []
    for f in batch["findings"]:
        for e in [*f["supporting_evidence"], *f["contradicting_evidence"]]:
            if f.get("evidence_resolved", True):
                memory.add_evidence(
                    Evidence(e["id"], EvidenceType(e["type"]), "planted", e["description"], None, e["attributes"])
                )
        findings.append(
            Finding(
                id=f["id"],
                statement=f["statement"],
                supporting_evidence=tuple(e["id"] for e in f["supporting_evidence"]),
                contradicting_evidence=tuple(e["id"] for e in f["contradicting_evidence"]),
                confidence=f["confidence"],
                produced_by="planted",
            )
        )
    return AgentContext(
        task_id="planted",
        execution_id="planted",
        deadline=datetime.now(UTC) + timedelta(minutes=1),
        task_input={},
        current_plan=None,
        evidence=memory.evidence_list(),
        state={"memory": memory, "agent_outputs": {}},
        tool_registry=ToolRegistry(),
        policy_context=PolicyContext("p", frozenset(), frozenset(), "planted"),
        cancellation_event=None,
        tools=_NoTools(),
        findings=tuple(findings),
    )


def flagged(assessment: dict[str, Any], contradictions: list[str]) -> bool:
    fid = assessment["finding_id"]
    flags = [x for x in assessment.get("flags", []) if x != CAP_FLAG]
    return (not assessment["supported"]) or bool(flags) or any(c.startswith(f"{fid}:") for c in contradictions)


def score(batches: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for batch in batches:
        result = asyncio.run(CriticAgent().execute(_context(batch)))
        critique = result.output["critique"]
        by_id = {a["finding_id"]: a for a in critique["assessments"]}
        for f in batch["findings"]:
            rows.append(
                {
                    "finding": f["id"],
                    "planted": f["planted"],
                    "flagged": flagged(by_id[f["id"]], critique["contradictions"]),
                    "flags": by_id[f["id"]].get("flags", []),
                }
            )
    planted = [r for r in rows if r["planted"]]
    controls = [r for r in rows if not r["planted"]]
    kinds = sorted({str(r["planted"]) for r in planted})
    return {
        "evidence_class": "deterministic",
        "detection": rate(sum(r["flagged"] for r in planted), len(planted)),
        "false_alarm": rate(sum(r["flagged"] for r in controls), len(controls)),
        "detection_by_kind": {
            k: rate(sum(r["flagged"] for r in planted if r["planted"] == k), sum(r["planted"] == k for r in planted))
            for k in kinds
        },
        "missed": [r["finding"] for r in planted if not r["flagged"]],
        "false_alarms": [r["finding"] for r in controls if r["flagged"]],
    }


def report() -> dict[str, Any]:
    return {"existing": score(existing_cases()), "heldout_v1": score(heldout_cases())}


def main() -> None:
    print(json.dumps(report(), indent=1))


if __name__ == "__main__":
    main()
