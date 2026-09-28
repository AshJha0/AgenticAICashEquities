"""Final investigation report."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ceap.domain.evidence import Evidence
from ceap.domain.findings import Finding


@dataclass(frozen=True)
class InvestigationReport:
    task_id: str
    execution_id: str
    title: str
    executive_summary: str
    primary_observations: tuple[str, ...]
    conclusion: str
    alternative_explanations: tuple[str, ...]
    findings: tuple[Finding, ...]
    evidence: tuple[Evidence, ...]
    metrics: dict[str, Any]
    generated_at: datetime
    narrative: str
    attribution: dict[str, Any] = field(default_factory=dict)
    critique: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    kind: str = "investigation"
    proposal: dict[str, Any] = field(default_factory=dict)
