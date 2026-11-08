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
    # structured output (PLAN 3A.1): a Pydantic model class; the adapter sends its JSON schema as
    # ``output_config.format`` through ``messages.parse()`` and the agent validates the reply again.
    output_model: Any = None


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
    parsed: dict[str, Any] | None = None  # the structured output, when ``output_model`` was set
    parse_errors: tuple[str, ...] = ()  # the adapter could not validate the reply against the schema
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    request_id: str | None = None
    # R3C 3C.4: citations on text blocks (citations call only): document_index, start/end_block_index,
    # cited_text, type, and the cited sentence with its [text_start, text_end) offsets in ``content``
    citations: tuple[dict[str, Any], ...] = ()
