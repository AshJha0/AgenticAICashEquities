"""Routes LLM requests to a client/model by purpose, with an explicit degradation ladder.

Fail closed (PLAN 1.1): the mock is used only when ``CEAP_LLM_PROVIDER=mock``. With
``anthropic`` a client that cannot be built is a :class:`SettingsError` at startup, never a
silent mock. Every answer that is not a plain model answer carries a :class:`Degradation`
(PLAN 1.5) that the harness turns into a ``ResultWarning`` and a ``DEGRADED`` status:

* rung 1 - the primary model refused; the pinned fallback model answered;
* rung 3 - the client failed; the deterministic client answered (canonical plan / template);
* rung 4 - ``CEAP_LLM_DISABLED=true``: :meth:`LLMRouter.complete` raises :class:`LLMDisabledError`
  and agents go straight to their deterministic path, with a banner on the result.

Rung 2 (one structured-output repair retry) lives in the agents, which know the expected shape.
"""

from __future__ import annotations

import asyncio
import logging

from ceap.config import Settings, SettingsError, load_settings
from ceap.llm.client import AnthropicLLMClient, LLMClient, MockLLMClient
from ceap.llm.models import (
    RUNG_DETERMINISTIC,
    RUNG_MODEL_FALLBACK,
    RUNG_REPAIR,
    Degradation,
    LLMRequest,
    LLMResponse,
)
from ceap.llm.pins import ModelPins, load_pins

log = logging.getLogger(__name__)

DEFAULT_MAX_CALLS_PER_TASK = 12


class LLMDisabledError(RuntimeError):
    """Raised by a router whose kill switch (``CEAP_LLM_DISABLED``) is on."""


class LLMBudgetExceededError(RuntimeError):
    """Raised when an investigation exceeds its model-call cap."""


class LLMRouter(LLMClient):
    def __init__(
        self,
        primary: LLMClient,
        fallback: LLMClient | None = None,
        models_by_purpose: dict[str, str] | None = None,
        *,
        pins: ModelPins | None = None,
        disabled: bool = False,
        timeout_s: float | None = None,
        max_concurrency: int = 4,
        max_calls_per_task: int = DEFAULT_MAX_CALLS_PER_TASK,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.models_by_purpose = dict(models_by_purpose or {})
        self.pins = pins
        self.disabled = disabled
        self.timeout_s = timeout_s
        self.max_calls_per_task = max_calls_per_task
        self._sem = asyncio.Semaphore(max_concurrency)
        self.usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "fallbacks": 0}
        self.last_fallback_reason: str | None = None

    async def _call(self, client: LLMClient, request: LLMRequest) -> LLMResponse:
        async with self._sem:  # model-call concurrency cap
            if self.timeout_s is None:
                return await client.complete(request)
            return await asyncio.wait_for(client.complete(request), timeout=self.timeout_s)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if self.disabled:
            raise LLMDisabledError("LLM disabled by CEAP_LLM_DISABLED=true")
        model = request.model or self.models_by_purpose.get(request.purpose)
        routed = LLMRequest(**{**request.__dict__, "model": model})
        degradations: list[Degradation] = []
        try:
            response = await self._call(self.primary, routed)
            if response.stop_reason == "refusal":
                fallback_model = self._fallback_model(request.purpose, response.model)
                category = (response.stop_details or {}).get("category")
                if fallback_model:
                    degradations.append(
                        Degradation(
                            RUNG_MODEL_FALLBACK,
                            "LLM_REFUSAL_FALLBACK",
                            f"{response.model} refused (category {category}); answered by {fallback_model}",
                        )
                    )
                    response = await self._call(
                        self.primary, LLMRequest(**{**request.__dict__, "model": fallback_model})
                    )
            if response.stop_reason in ("refusal", "max_tokens"):
                degradations.append(
                    Degradation(
                        RUNG_REPAIR,
                        "LLM_STOP_" + response.stop_reason.upper(),
                        f"stop_reason={response.stop_reason} stop_details={response.stop_details}",
                    )
                )
        except Exception as exc:  # noqa: BLE001 - deliberate fallback boundary
            if self.fallback is None:
                raise
            reason = f"{type(exc).__name__}: {str(exc)[:200]}"
            log.warning("primary LLM failed (%s); using deterministic fallback", reason)
            self.usage["fallbacks"] += 1
            self.last_fallback_reason = reason
            fallback_response = await self.fallback.complete(request)
            response = LLMResponse(**{**fallback_response.__dict__, "fallback_reason": reason})
            degradations.append(
                Degradation(RUNG_DETERMINISTIC, "LLM_ERROR_FALLBACK", f"LLM fallback used ({reason})")
            )
        if degradations:
            response = LLMResponse(
                **{**response.__dict__, "degradations": (*response.degradations, *degradations)}
            )
        self.usage["calls"] += 1
        self.usage["input_tokens"] += response.input_tokens
        self.usage["output_tokens"] += response.output_tokens
        return response

    def _fallback_model(self, purpose: str, refused_model: str) -> str | None:
        if self.pins is None:
            return None
        candidate = self.pins.for_purpose(purpose).fallback_model
        return candidate if candidate and candidate != refused_model else None

    @property
    def name(self) -> str:
        return f"router({self.primary.name}{', disabled' if self.disabled else ''})"


def build_router(settings: Settings | None = None) -> LLMRouter:
    """Build the router for ``settings``; raises :class:`SettingsError` instead of degrading silently."""
    settings = settings or load_settings()
    pins = load_pins()
    common = {
        "pins": pins,
        "disabled": settings.llm_disabled,
        "timeout_s": settings.llm_timeout_s,
        "max_concurrency": settings.llm_max_concurrency,
        "max_calls_per_task": settings.llm_max_calls_per_task,
    }
    if settings.llm_provider == "mock":
        return LLMRouter(MockLLMClient(), **common)  # type: ignore[arg-type]
    try:
        primary: LLMClient = AnthropicLLMClient(
            api_key=settings.anthropic_api_key, model=settings.llm_model, timeout_s=settings.llm_timeout_s
        )
    except Exception as exc:
        raise SettingsError(
            f"CEAP_LLM_PROVIDER=anthropic but the client cannot be built ({type(exc).__name__}: {exc}); "
            "install the [llm] extra or set CEAP_LLM_PROVIDER=mock explicitly"
        ) from exc
    models = {p: pins.for_purpose(p).model for p in ("planning", "narrative", "critique", "judge")}
    if settings.llm_planning_model:
        models["planning"] = settings.llm_planning_model
    models["narrative"] = models["critique"] = settings.llm_model
    return LLMRouter(primary, fallback=MockLLMClient(), models_by_purpose=models, **common)  # type: ignore[arg-type]
