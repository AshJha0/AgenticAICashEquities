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
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

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
#: per-task caps, on by default (A10). ``CEAP_TASK_BUDGET_USD`` / ``CEAP_TASK_TOKEN_BUDGET`` override them;
#: ``off`` removes a cap (not recommended outside local experiments).
DEFAULT_TASK_BUDGET_USD = 2.00
DEFAULT_TASK_TOKEN_BUDGET = 400_000
#: circuit breaker: after this many consecutive primary failures the primary is skipped for the cooldown
DEFAULT_BREAKER_FALLBACKS = 5
DEFAULT_BREAKER_COOLDOWN_S = 60.0
UNATTRIBUTED = "unattributed"


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
        try:
            _current_task.reset(token)
        except ValueError:
            # An abandoned task's generator finalised by the garbage collector runs in another
            # context; the context that holds the token is gone, so there is nothing to restore.
            # Raising here only surfaced as an unraisable-exception warning (nightly, 2026-11-13).
            pass


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
    tokens: int = 0
    records: list[LLMCallRecord] = field(default_factory=list)
    # reserved by calls in flight: checked together with what is spent, so two concurrent calls
    # cannot both pass a budget check that only one of them fits (A10)
    reserved_usd: float = 0.0
    reserved_tokens: int = 0
    reserved_calls: int = 0


_ROUTERS: weakref.WeakSet[LLMRouter] = weakref.WeakSet()


def task_cost(task_id: str) -> TaskCost:
    """The cost span of ``task_id`` across every live router (the harness reads this after a run)."""
    total = TaskCost(task_id)
    for router in list(_ROUTERS):
        meter = router._costs.get(task_id)
        if meter is not None:
            total.usd += meter.usd
            total.calls += meter.calls
            total.tokens += meter.tokens
            total.records.extend(meter.records)
    return total


class LLMDisabledError(RuntimeError):
    """Raised by a router whose kill switch (``CEAP_LLM_DISABLED``) is on."""


class LLMBudgetExceededError(RuntimeError):
    """Raised when a task would exceed its model-call, token or USD cap."""


class LLMCircuitOpenError(RuntimeError):
    """The primary model is skipped after repeated failures (the circuit breaker is open)."""


@dataclass(frozen=True)
class _Reservation:
    task_id: str
    usd: float
    tokens: int


def _env_cap(name: str, default: float) -> float | None:
    """A cap from the environment: unset or empty means the default, ``off`` means no cap."""
    raw = (os.environ.get(name) or "").strip().lower()
    if raw == "":
        return default
    if raw in ("off", "none", "disabled"):
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be a number or 'off', got {raw!r}") from exc
    if value <= 0:
        raise SettingsError(f"{name} must be > 0 (or 'off'), got {raw!r}")
    return value


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
        task_token_budget: int | None = None,
        breaker_fallbacks: int = DEFAULT_BREAKER_FALLBACKS,
        breaker_cooldown_s: float = DEFAULT_BREAKER_COOLDOWN_S,
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
        self.task_token_budget = task_token_budget
        self.breaker_fallbacks = breaker_fallbacks
        self.breaker_cooldown_s = breaker_cooldown_s
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0  # time.monotonic(); 0 = closed
        self._budget_lock = threading.Lock()  # reserve-and-check is atomic across threads too
        self._keep_tasks = keep_tasks
        self._costs: OrderedDict[str, TaskCost] = OrderedDict()  # bounded: the router is long-lived
        self.slo: Any = None  # 0.9.0 B8: an observability.ai_slo.AISLOMonitor; None uses ai_slo.default_monitor
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

    def _estimate(self, request: LLMRequest, model: str | None) -> tuple[float, int]:
        """Upper-bound local estimate of one call: estimated input tokens plus ``max_tokens`` output."""
        if model is None and self.pins is not None:
            model = self.pins.for_purpose(request.purpose).model
        max_out = request.max_tokens or (
            self.pins.for_purpose(request.purpose).max_tokens if self.pins is not None else 0
        )
        tokens_in = estimate_input_tokens(request)
        return usd(self._price(model), tokens_in, max_out), tokens_in + max_out

    def _reserve(self, task_id: str, request: LLMRequest, model: str | None) -> _Reservation:
        """Check every per-task cap against spent + reserved + this estimate, then reserve it.

        Raises :class:`LLMBudgetExceededError` *before* the call. Calls not attributed to a task
        (no ``task_scope``) count in the global usage but are not capped per task.
        """
        est_usd, est_tokens = self._estimate(request, model)
        if task_id == UNATTRIBUTED:
            # No task meter to accumulate against, but one call alone still may not exceed a cap.
            if self.task_token_budget is not None and est_tokens > self.task_token_budget:
                raise LLMBudgetExceededError(
                    f"task token budget {self.task_token_budget} would be exceeded by one call: "
                    f"local estimate {est_tokens} (estimated input + max_tokens output)"
                )
            if self.task_budget_usd is not None and est_usd > self.task_budget_usd:
                raise LLMBudgetExceededError(
                    f"task budget ${self.task_budget_usd:.4f} would be exceeded by one call: local estimate "
                    f"${est_usd:.4f} (upper bound: estimated input + max_tokens output)"
                )
            return _Reservation(task_id, 0.0, 0)
        with self._budget_lock:
            meter = self._meter(task_id)
            calls = meter.calls + meter.reserved_calls
            if calls + 1 > self.max_calls_per_task:
                raise LLMBudgetExceededError(f"per-task model-call cap of {self.max_calls_per_task} reached")
            if self.task_token_budget is not None:
                tokens = meter.tokens + meter.reserved_tokens
                if tokens + est_tokens > self.task_token_budget:
                    raise LLMBudgetExceededError(
                        f"task token budget {self.task_token_budget} would be exceeded: used or reserved "
                        f"{tokens} + local estimate {est_tokens} (estimated input + max_tokens output)"
                    )
            if self.task_budget_usd is not None:
                spent = meter.usd + meter.reserved_usd
                if spent + est_usd > self.task_budget_usd:
                    raise LLMBudgetExceededError(
                        f"task budget ${self.task_budget_usd:.4f} would be exceeded: spent or reserved "
                        f"${spent:.4f} + local estimate ${est_usd:.4f} (upper bound: estimated input + "
                        "max_tokens output)"
                    )
            meter.reserved_usd += est_usd
            meter.reserved_tokens += est_tokens
            meter.reserved_calls += 1
        return _Reservation(task_id, est_usd, est_tokens)

    def _release(self, reservation: _Reservation) -> None:
        if reservation.task_id == UNATTRIBUTED:
            return
        with self._budget_lock:
            meter = self._costs.get(reservation.task_id)
            if meter is None:  # evicted while in flight
                return
            meter.reserved_usd = max(0.0, meter.reserved_usd - reservation.usd)
            meter.reserved_tokens = max(0, meter.reserved_tokens - reservation.tokens)
            meter.reserved_calls = max(0, meter.reserved_calls - 1)
            if meter.reserved_calls == 0:  # nothing in flight: drop float residue
                meter.reserved_usd, meter.reserved_tokens = 0.0, 0

    # --------------------------------------------------------------- circuit breaker
    @property
    def circuit_open(self) -> bool:
        return self._breaker_open_until > time.monotonic()

    def _primary_failed(self) -> None:
        self._consecutive_failures += 1
        if self.breaker_fallbacks > 0 and self._consecutive_failures >= self.breaker_fallbacks:
            if not self.circuit_open:
                log.warning(
                    "LLM circuit breaker open for %.0fs after %d consecutive primary failures",
                    self.breaker_cooldown_s,
                    self._consecutive_failures,
                )
            self._breaker_open_until = time.monotonic() + self.breaker_cooldown_s

    def _primary_ok(self) -> None:
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0

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
        with self._budget_lock:
            meter = self._meter(task_id)
            meter.usd += cost
            meter.calls += 1
            meter.tokens += response.input_tokens + response.output_tokens
            meter.records.append(record)
        self._report_slo(request, response, latency_ms)

    def _report_slo(self, request: LLMRequest, response: LLMResponse, latency_ms: float) -> None:
        from ceap.observability import ai_slo

        monitor = self.slo if self.slo is not None else ai_slo.default_monitor
        if monitor is not None:
            monitor.record(ai_slo.event_from_response(request, response, latency_ms))

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
        reservation = self._reserve(task_id, request, model)
        try:
            return await self._complete_reserved(request, routed, task_id)
        finally:
            self._release(reservation)  # settle: the actual cost was recorded by _record

    async def _complete_reserved(self, request: LLMRequest, routed: LLMRequest, task_id: str) -> LLMResponse:
        started = time.perf_counter()
        degradations: list[Degradation] = []
        try:
            if self.circuit_open:
                raise LLMCircuitOpenError(
                    f"circuit open after {self._consecutive_failures} consecutive primary failures"
                )
            response = await self._call(self.primary, routed)
            self._primary_ok()
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
            if not isinstance(exc, LLMCircuitOpenError):
                self._primary_failed()
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


def _int_or_none(value: float | None) -> int | None:
    return None if value is None else int(value)


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
        # read here until they move into Settings; on by default, "off" removes a cap (A10)
        "task_budget_usd": _env_cap("CEAP_TASK_BUDGET_USD", DEFAULT_TASK_BUDGET_USD),
        "task_token_budget": _int_or_none(_env_cap("CEAP_TASK_TOKEN_BUDGET", DEFAULT_TASK_TOKEN_BUDGET)),
        "breaker_fallbacks": int(_env_cap("CEAP_LLM_BREAKER_FALLBACKS", DEFAULT_BREAKER_FALLBACKS) or 0),
        "breaker_cooldown_s": _env_cap("CEAP_LLM_BREAKER_COOLDOWN_S", DEFAULT_BREAKER_COOLDOWN_S) or 0.0,
    }
    if settings.llm_provider == "mock":
        return LLMRouter(MockLLMClient(), **common)  # type: ignore[arg-type]
    try:
        primary: LLMClient = AnthropicLLMClient(
            api_key=settings.anthropic_api_key, model=settings.llm_model, timeout_s=settings.llm_timeout_s
        )
    except Exception as exc:  # noqa: BLE001 - any SDK construction failure becomes one SettingsError
        raise SettingsError(
            f"CEAP_LLM_PROVIDER=anthropic but the client cannot be built ({type(exc).__name__}: {exc}); "
            "install the [llm] extra or set CEAP_LLM_PROVIDER=mock explicitly"
        ) from exc
    models = {p: pins.for_purpose(p).model for p in ("planning", "narrative", "critique", "judge")}
    if settings.llm_planning_model:
        models["planning"] = settings.llm_planning_model
    models["narrative"] = models["critique"] = settings.llm_model
    return LLMRouter(primary, fallback=MockLLMClient(), models_by_purpose=models, **common)  # type: ignore[arg-type]
