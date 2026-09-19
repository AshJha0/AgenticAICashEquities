"""Investigation memory: everything gathered during one harness execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ceap.domain.evidence import Evidence
from ceap.domain.findings import Finding
from ceap.domain.tools import ToolRequest, ToolResult


@dataclass(frozen=True)
class ToolOutput:
    step_id: str
    tool_id: str
    arguments: dict[str, Any]
    data: Any
    evidence_ids: tuple[str, ...]
    execution_time_ms: float

    def matches(self, **filters: Any) -> bool:
        return all(self.arguments.get(k) == v for k, v in filters.items())


@dataclass
class InvestigationMemory:
    evidence: dict[str, Evidence] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    tool_outputs: list[ToolOutput] = field(default_factory=list)
    agent_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    scratch: dict[str, Any] = field(default_factory=dict)

    def add_evidence(self, *items: Evidence) -> None:
        for e in items:
            self.evidence[e.id] = e

    def add_findings(self, *items: Finding) -> None:
        self.findings.extend(items)

    def replace_findings(self, items: list[Finding]) -> None:
        self.findings = list(items)

    def record_tool(self, step_id: str, request: ToolRequest, result: ToolResult) -> ToolOutput | None:
        if not result.ok:
            return None
        self.add_evidence(*result.evidence)
        out = ToolOutput(
            step_id,
            request.tool_id,
            dict(request.arguments),
            result.data,
            tuple(e.id for e in result.evidence),
            result.execution_time_ms,
        )
        self.tool_outputs.append(out)
        return out

    def find(self, tool_id: str, **filters: Any) -> ToolOutput | None:
        for out in reversed(self.tool_outputs):
            if out.tool_id == tool_id and out.matches(**filters):
                return out
        return None

    def find_all(self, tool_id: str, **filters: Any) -> list[ToolOutput]:
        return [o for o in self.tool_outputs if o.tool_id == tool_id and o.matches(**filters)]

    def evidence_list(self) -> tuple[Evidence, ...]:
        return tuple(self.evidence.values())

    def unresolved_evidence(self, finding: Finding) -> list[str]:
        return [
            e
            for e in (*finding.supporting_evidence, *finding.contradicting_evidence)
            if e not in self.evidence
        ]
