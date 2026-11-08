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
    prompts: dict[str, dict[str, str]] = field(default_factory=dict)  # prompt id -> {version, sha256} (3A.7)
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
    # 3A.6 / 3A.7 (additive): attribution, provenance and the redacted request body
    task_id: str | None = None
    purpose: str = "general"
    prompt_id: str | None = None
    prompt_version: str | None = None
    stop_reason: str | None = None
    usd_is_estimate: bool = (
        True  # tokens are the provider's when it reports them; prices are pinned list prices
    )
    redacted_request: str | None = None
    schema_version: int = SCHEMA_VERSION
