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
import contextvars
import json
import logging
import os
import time
import weakref
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from ceap.config import Settings, SettingsError, load_settings
from ceap.domain.reports import LLMCallRecord
from ceap.llm.client import AnthropicLLMClient, LLMClient, MockLLMClient
from ceap.llm.models import (
    RUNG_DETERMINISTIC,
    RUNG_MODEL_FALLBACK,
    RUNG_REPAIR,
    Degradation,
    LLMRequest,
    LLMResponse,
)
from ceap.llm.pins import ModelPins, Price, load_pins
from ceap.util.redact import redact

log = logging.getLogger(__name__)

DEFAULT_MAX_CALLS_PER_TASK = 12


CHARS_PER_TOKEN = 4  # LOCAL ESTIMATE: count_tokens is a network call and is never used (D15)
_current_task: contextvars.ContextVar[str | None] = contextvars.ContextVar("ceap_llm_task", default=None)


@contextmanager
def task_scope(task_id: str) -> Iterator[None]:
    """Attribute every model call made inside this block (and the asyncio tasks it starts) to ``task_id``.

    A context variable, so two tasks running concurrently on one router never share a meter.
    """
    token = _current_task.set(task_id)
    try:
        yield
    finally:
        _current_task.reset(token)


def estimate_input_tokens(request: LLMRequest) -> int:
    """Local, offline token estimate of the request (labelled an estimate wherever it is shown)."""
    text = (
        request.system_prompt
        + json.dumps(request.messages, default=str)
        + json.dumps(request.tools, default=str)
    )
    return len(text) // CHARS_PER_TOKEN + 1


def usd(
    price: Price | None, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0
) -> float:
    if price is None:
        return 0.0
    return (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read * price.cache_read
        + cache_write * price.cache_write
    ) / 1_000_000


@dataclass
class TaskCost:
    """Per-task cost span: every call's record and the running USD estimate."""

    task_id: str
    usd: float = 0.0
    calls: int = 0
    records: list[LLMCallRecord] = field(default_factory=list)


_ROUTERS: weakref.WeakSet[LLMRouter] = weakref.WeakSet()


def task_cost(task_id: str) -> TaskCost:
    """The cost span of ``task_id`` across every live router (the harness reads this after a run)."""
    total = TaskCost(task_id)
    for router in list(_ROUTERS):
        meter = router._costs.get(task_id)
        if meter is not None:
            total.usd += meter.usd
            total.calls += meter.calls
            total.records.extend(meter.records)
    return total


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
        task_budget_usd: float | None = None,
        keep_tasks: int = 256,
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
        self.task_budget_usd = task_budget_usd
        self._keep_tasks = keep_tasks
        self._costs: OrderedDict[str, TaskCost] = OrderedDict()  # bounded: the router is long-lived
        _ROUTERS.add(self)

    def cost_for(self, task_id: str) -> TaskCost:
        return self._costs.get(task_id) or TaskCost(task_id)

    def _meter(self, task_id: str) -> TaskCost:
        meter = self._costs.get(task_id)
        if meter is None:
            meter = self._costs[task_id] = TaskCost(task_id)
            while len(self._costs) > self._keep_tasks:
                self._costs.popitem(last=False)
        return meter

    def _price(self, model: str | None) -> Price | None:
        return self.pins.prices.get(model or "") if self.pins is not None else None

    def _check_budget(self, task_id: str, request: LLMRequest, model: str | None) -> None:
        """Abort *before* the call when the local upper-bound estimate would exceed the task budget."""
        if self.task_budget_usd is None:
            return
        if model is None and self.pins is not None:
            model = self.pins.for_purpose(request.purpose).model
        max_out = request.max_tokens or (
            self.pins.for_purpose(request.purpose).max_tokens if self.pins is not None else 0
        )
        estimate = usd(self._price(model), estimate_input_tokens(request), max_out)
        spent = self.cost_for(task_id).usd
        if spent + estimate > self.task_budget_usd:
            raise LLMBudgetExceededError(
                f"task budget ${self.task_budget_usd:.4f} would be exceeded: spent ${spent:.4f} + "
                f"local estimate ${estimate:.4f} (upper bound: estimated input + max_tokens output)"
            )

    def _record(self, task_id: str, request: LLMRequest, response: LLMResponse, latency_ms: float) -> None:
        price = self._price(response.model)
        cost = usd(
            price,
            response.input_tokens,
            response.output_tokens,
            response.cache_read_tokens,
            response.cache_write_tokens,
        )
        prompt = request.metadata.get("prompt") or {}
        body = redact(
            json.dumps({"system": request.system_prompt, "messages": request.messages}, default=str)
        )
        record = LLMCallRecord(
            model=response.model,
            sdk=f"{self.pins.sdk_package}=={self.pins.sdk_version}" if self.pins is not None else "unknown",
            prompt_sha=str(prompt.get("sha256", "")),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_tokens,
            cache_write_tokens=response.cache_write_tokens,
            usd=round(cost, 8),
            latency_ms=round(latency_ms, 3),
            request_id=response.request_id,
            task_id=task_id,
            purpose=request.purpose,
            prompt_id=prompt.get("id"),
            prompt_version=prompt.get("version"),
            stop_reason=response.stop_reason,
            redacted_request=body[:20000],
        )
        meter = self._meter(task_id)
        meter.usd += cost
        meter.calls += 1
        meter.records.append(record)

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
        task_id = str(request.metadata.get("task_id") or _current_task.get() or "unattributed")
        self._check_budget(task_id, request, model)
        started = time.perf_counter()
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
        self._record(task_id, request, response, (time.perf_counter() - started) * 1000)
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
        # read here until R4 moves it into Settings; empty or 0 means no budget
        "task_budget_usd": float(os.environ.get("CEAP_TASK_BUDGET_USD") or 0) or None,
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
