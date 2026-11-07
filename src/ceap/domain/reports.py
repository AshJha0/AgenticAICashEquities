"""Final investigation report."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ceap.domain.common import SCHEMA_VERSION
from ceap.domain.evidence import Evidence
from ceap.domain.findings import Finding, HypothesisTable


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
    hypotheses: HypothesisTable | None = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class LLMCallRecord:
    """Provenance of one model call, recorded by the harness (never by the model)."""

    model: str
    sdk: str
    prompt_sha: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd: float = 0.0
    latency_ms: float = 0.0
    request_id: str | None = None
    schema_version: int = SCHEMA_VERSION
