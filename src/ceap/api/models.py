"""Pydantic request / response schemas for the API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class InvestigationCreate(BaseModel):
    question: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        examples=[
            "Analyse our AAPL execution between 14:00 and 15:00 and explain why implementation shortfall increased."
        ],
    )
    symbol: str | None = Field(default=None, description="Ticker; parsed from the question when omitted")
    window_start: datetime | None = Field(
        default=None, description="ISO-8601; naive values are Europe/London"
    )
    window_end: datetime | None = None
    baseline_start: datetime | None = None
    baseline_end: datetime | None = None
    dataset: str | None = Field(default=None, description="Scenario dataset id (e.g. T01..T10, S01..S50)")
    session_date: str | None = Field(
        default=None, description="YYYY-MM-DD used when the question says 14:00 without a date"
    )


class InvestigationAccepted(BaseModel):
    task_id: str
    status: str
    symbol: str
    window_start: datetime
    window_end: datetime
    baseline_start: datetime
    baseline_end: datetime
    dataset: str
    links: dict[str, str]


class InvestigationStatus(BaseModel):
    task_id: str
    status: str
    state: str | None = None
    error: str | None = None
    duration_ms: float | None = None
    warnings: list[str] = Field(default_factory=list)


class ReportOut(BaseModel):
    task_id: str
    execution_id: str
    title: str
    executive_summary: str
    primary_observations: list[str]
    conclusion: str
    alternative_explanations: list[str]
    attribution: dict[str, Any]
    metrics: dict[str, Any]
    findings: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    critique: dict[str, Any]
    narrative: str
    generated_at: datetime
    kind: str = "investigation"
    proposal: dict[str, Any] = Field(default_factory=dict)


class ResearchCreate(BaseModel):
    question: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        examples=["Does 12-1 momentum work on the R01 universe? Evaluate it out-of-sample with costs."],
    )
    signal: str | None = Field(default=None, description="Signal id; parsed from the question when omitted")
    dataset: str | None = Field(default=None, description="Research dataset id (R01..R07, RS01..RS21)")
    start: date | None = Field(default=None, description="First research date (defaults to the dataset's)")
    end: date | None = None
    in_sample_end: date | None = Field(default=None, description="Walk-forward split (defaults to the dataset's)")
    rebalance_days: int | None = Field(default=None, ge=1, le=252, description="Defaults to the signal's")
    long_short: bool = True
    gross_notional: float = Field(default=50_000_000.0, gt=0)
    stage_orders: bool = Field(default=False, description="Stage paper orders after approval (admin only)")


class ResearchAccepted(BaseModel):
    task_id: str
    status: str
    signal: str
    dataset: str
    start: date
    end: date
    in_sample_end: date
    rebalance_days: int
    long_short: bool
    stage_orders: bool
    links: dict[str, str]


class ResearchScenarioOut(BaseModel):
    id: str
    template: str
    name: str
    description: str
    signal: str
    expected_verdict: str
    expected_flags: list[str]
    rebalance_days: int


class ApprovalOut(BaseModel):
    id: str
    task_id: str
    step_id: str
    tool_id: str
    arguments: dict[str, Any]
    reason: str
    requested_by: str
    requested_at: datetime


class ApprovalDecisionIn(BaseModel):
    approved: bool
    comment: str = ""


class ScenarioOut(BaseModel):
    id: str
    template: str
    name: str
    description: str
    symbol: str
    ground_truth: list[str]


class ToolOut(BaseModel):
    id: str
    name: str
    description: str
    read_only: bool
    risk_level: str
    server: str | None
    required_capabilities: list[str]


class HealthOut(BaseModel):
    status: str
    version: str
    environment: str
    llm: str
    servers: list[str]
    tools: int
    llm_usage: dict[str, int] | None = None
    running: int = 0
