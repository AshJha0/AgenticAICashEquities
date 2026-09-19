"""Routes LLM requests to a client/model by purpose, with fallback."""

from __future__ import annotations

import logging

from ceap.config import Settings, load_settings
from ceap.llm.client import AnthropicLLMClient, LLMClient, MockLLMClient
from ceap.llm.models import LLMRequest, LLMResponse

log = logging.getLogger(__name__)


class LLMRouter(LLMClient):
    """Chooses the client and model per request ``purpose``.

    ``planning`` may use a stronger model than ``narrative``; if the primary
    client fails the router falls back to the deterministic mock so an
    investigation never dies because of a model outage.
    """

    def __init__(
        self,
        primary: LLMClient,
        fallback: LLMClient | None = None,
        models_by_purpose: dict[str, str] | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.models_by_purpose = dict(models_by_purpose or {})
        self.usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "fallbacks": 0}
        self.last_fallback_reason: str | None = None

    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = request.model or self.models_by_purpose.get(request.purpose)
        routed = LLMRequest(**{**request.__dict__, "model": model})
        try:
            response = await self.primary.complete(routed)
        except Exception as exc:  # noqa: BLE001 - deliberate fallback boundary
            if self.fallback is None:
                raise
            reason = f"{type(exc).__name__}: {str(exc)[:200]}"
            log.warning("primary LLM failed (%s); using fallback", reason)
            self.usage["fallbacks"] += 1
            self.last_fallback_reason = reason
            fallback_response = await self.fallback.complete(request)
            # the fallback is visible to callers: agents turn it into an investigation warning
            response = LLMResponse(**{**fallback_response.__dict__, "fallback_reason": reason})
        self.usage["calls"] += 1
        self.usage["input_tokens"] += response.input_tokens
        self.usage["output_tokens"] += response.output_tokens
        return response

    @property
    def name(self) -> str:
        return f"router({self.primary.name})"


def build_router(settings: Settings | None = None) -> LLMRouter:
    settings = settings or load_settings()
    mock = MockLLMClient()
    if settings.use_anthropic:
        try:
            primary: LLMClient = AnthropicLLMClient(
                api_key=settings.anthropic_api_key, model=settings.llm_model
            )
        except Exception as exc:  # noqa: BLE001 - missing SDK/key: degrade to the mock, loudly
            log.warning("%s - falling back to mock LLM", exc)
            return LLMRouter(mock)
        models = {
            "planning": settings.llm_planning_model or settings.llm_model,
            "narrative": settings.llm_model,
            "critique": settings.llm_model,
        }
        return LLMRouter(primary, fallback=mock, models_by_purpose=models)
    return LLMRouter(mock)
