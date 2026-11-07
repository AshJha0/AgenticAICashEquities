"""LLM request / response value objects.

``LLMRequest`` has no ``temperature``: current models reject sampling parameters, so depth is
controlled by the pinned ``effort`` per purpose (``model_pins.toml``). ``max_tokens`` defaults to
the pinned value for the purpose, which leaves headroom for adaptive thinking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Degradation ladder (PLAN 1.5). Rung 0 is a normal model answer and is never recorded.
RUNG_MODEL_FALLBACK = 1  # server-side refusal: answered by the pinned fallback model
RUNG_REPAIR = 2  # unparsable structured output: one repair retry
RUNG_DETERMINISTIC = 3  # canonical plan / templated narrative / deterministic critique
RUNG_KILL_SWITCH = 4  # CEAP_LLM_DISABLED=true forces rung 3 everywhere


@dataclass(frozen=True)
class Degradation:
    rung: int
    code: str
    message: str


@dataclass(frozen=True)
class LLMRequest:
    system_prompt: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    model: str | None = None
    max_tokens: int | None = None  # None: the pinned max_tokens for ``purpose``
    effort: str | None = None  # None: the pinned effort for ``purpose``
    purpose: str = "general"  # planning | narrative | critique | judge | general
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
    stop_details: dict[str, Any] | None = None  # populated by the API for ``refusal`` only
    degradations: tuple[Degradation, ...] = ()
