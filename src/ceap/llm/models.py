"""LLM request / response value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LLMRequest:
    system_prompt: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    model: str | None = None
    temperature: float = 0.0
    max_tokens: int = 2048
    purpose: str = "general"  # planning | narrative | critique | general
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    content: str
    tool_calls: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str | None = None
    fallback_reason: str | None = None  # set by the router when a fallback client answered
