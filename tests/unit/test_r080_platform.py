"""0.8.0 platform lane: dataset store locking (G1), tool metadata (G3), LLM budget, breaker and rate limit (A10)."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from ceap.api.ratelimit import RateLimiter, retry_after_header
from ceap.config import SettingsError
from ceap.data.repositories import DatasetStore
from ceap.data.scenarios import get_scenario
from ceap.llm.client import LLMClient, MockLLMClient
from ceap.llm.models import LLMRequest, LLMResponse
from ceap.llm.pins import load_pins
from ceap.llm.router import (
    DEFAULT_TASK_BUDGET_USD,
    DEFAULT_TASK_TOKEN_BUDGET,
    LLMBudgetExceededError,
    LLMCircuitOpenError,
    LLMRouter,
    _env_cap,
    build_router,
    task_scope,
)

# ---------------------------------------------------------------- G1 dataset store


class _CountingGenerator:
    """Stands in for the synthetic generator: slow, counts calls, returns a prebuilt dataset."""

    def __init__(self, dataset, delay: float = 0.3) -> None:
        self.dataset = dataset
        self.delay = delay
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def generate(self, spec):
        with self._lock:
            self.calls.append(spec.id)
        time.sleep(self.delay)
        return self.dataset


def test_g1_same_key_generates_once_under_concurrency(dataset_t01):
    gen = _CountingGenerator(dataset_t01)
    store = DatasetStore(generator=gen)  # type: ignore[arg-type]
    threads = [threading.Thread(target=store.get, args=("T01",)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert gen.calls == ["T01"]
    assert store.is_cached("T01")


def test_g1_different_keys_do_not_wait_for_each_other(dataset_t01):
    gen = _CountingGenerator(dataset_t01, delay=0.6)
    store = DatasetStore(generator=gen)  # type: ignore[arg-type]
    started = time.perf_counter()
    threads = [threading.Thread(target=store.get, args=(s,)) for s in ("T01", "T02", "T03")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - started
    assert sorted(gen.calls) == ["T01", "T02", "T03"]
    assert elapsed < 1.5, f"per-key locks should overlap generation, took {elapsed:.2f}s"  # serial = 1.8 s


async def test_g1_aget_keeps_the_event_loop_free(dataset_t01):
    gen = _CountingGenerator(dataset_t01, delay=0.8)
    store = DatasetStore(generator=gen)  # type: ignore[arg-type]
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    t = asyncio.create_task(ticker())
    ds = await store.aget(get_scenario("T01"))
    t.cancel()
    assert ds is dataset_t01
    assert ticks >= 8, "the loop was blocked while the dataset generated"
    assert await store.aget("T01") is dataset_t01 and gen.calls == ["T01"]  # cache hit


# ---------------------------------------------------------------- G3 metadata


def test_g3_cpu_bound_metadata_reaches_the_adapter(history):
    from ceap.mcp.adapter import MCPToolAdapter
    from ceap.mcp.backtest.server import build_server

    server = build_server(history)
    run = server.get("run_backtest")
    assert run.cpu_bound and run.descriptor()["annotations"]["cpuBound"] is True
    assert not server.get("trial_count").cpu_bound
    adapter = MCPToolAdapter(None, "backtest", run.descriptor())  # type: ignore[arg-type]
    assert adapter.metadata.cpu_bound is True


# ---------------------------------------------------------------- A10 budget


def _req(text: str = "x", **kw) -> LLMRequest:
    return LLMRequest("s", [{"role": "user", "content": text}], purpose="narrative", **kw)


class _SlowClient(MockLLMClient):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        await asyncio.sleep(0.05)
        return await super().complete(request)


async def test_a10_reservations_stop_concurrent_calls_overshooting_the_cap():
    router = LLMRouter(_SlowClient(model="claude-opus-5-5"), pins=load_pins(), max_calls_per_task=3)
    with task_scope("T-RACE"):
        results = await asyncio.gather(*(router.complete(_req()) for _ in range(6)), return_exceptions=True)
    ok = [r for r in results if isinstance(r, LLMResponse)]
    refused = [r for r in results if isinstance(r, LLMBudgetExceededError)]
    assert (len(ok), len(refused)) == (3, 3)
    meter = router.cost_for("T-RACE")
    assert meter.calls == 3 and meter.reserved_calls == 0 and meter.reserved_usd == 0.0  # snapped to zero


async def test_a10_usd_reservation_is_race_free():
    pins = load_pins()
    probe = LLMRouter(MockLLMClient(model="claude-opus-5-5"), pins=pins)
    est_usd, _ = probe._estimate(_req("x" * 4000), None)  # the model the router will pick
    assert est_usd > 0
    # room for exactly two upper-bound estimates; without reservations all five would pass the check
    router = LLMRouter(_SlowClient(model="claude-opus-5-5"), pins=pins, task_budget_usd=est_usd * 2.5)
    with task_scope("T-USD"):
        results = await asyncio.gather(*(router.complete(_req("x" * 4000)) for _ in range(5)), return_exceptions=True)
    assert sum(isinstance(r, LLMResponse) for r in results) == 2
    assert router.cost_for("T-USD").usd <= est_usd * 2.5


async def test_a10_token_cap_refuses_before_the_call():
    primary = MockLLMClient(model="claude-opus-5-5")
    router = LLMRouter(primary, pins=load_pins(), task_token_budget=50)
    with task_scope("T-TOK"), pytest.raises(LLMBudgetExceededError, match="token budget"):
        await router.complete(_req("y" * 1000, max_tokens=100))
    assert len(primary.calls) == 0


async def test_a10_failed_call_releases_its_reservation():
    class Boom(LLMClient):
        name = "boom"

        async def complete(self, request):
            raise RuntimeError("down")

    router = LLMRouter(Boom(), pins=load_pins(), max_calls_per_task=2)
    with task_scope("T-REL"):
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await router.complete(_req())
    meter = router.cost_for("T-REL")
    assert meter.reserved_calls == 0 and meter.calls == 0


async def test_a10_unattributed_calls_are_not_capped():
    router = LLMRouter(MockLLMClient(), pins=load_pins(), max_calls_per_task=1)
    for _ in range(3):
        await router.complete(_req())
    assert router.usage["calls"] == 3


async def test_a10_circuit_breaker_opens_after_n_fallbacks_and_closes_after_cooldown():
    class Flaky(LLMClient):
        name = "flaky"

        def __init__(self) -> None:
            self.calls = 0
            self.fail = True

        async def complete(self, request):
            self.calls += 1
            if self.fail:
                raise RuntimeError("503")
            return await MockLLMClient().complete(request)

    primary = Flaky()
    router = LLMRouter(primary, fallback=MockLLMClient(), breaker_fallbacks=3, breaker_cooldown_s=0.2)
    for _ in range(3):
        await router.complete(_req())
    assert primary.calls == 3 and router.circuit_open
    r = await router.complete(_req())
    assert primary.calls == 3  # skipped while open
    assert "LLMCircuitOpenError" in (r.fallback_reason or "")
    await asyncio.sleep(0.25)
    primary.fail = False
    await router.complete(_req())
    assert primary.calls == 4 and not router.circuit_open


async def test_a10_open_circuit_without_fallback_raises():
    class Down(LLMClient):
        name = "down"

        async def complete(self, request):
            raise RuntimeError("503")

    router = LLMRouter(Down(), breaker_fallbacks=1, breaker_cooldown_s=60)
    with pytest.raises(RuntimeError):
        await router.complete(_req())
    with pytest.raises(LLMCircuitOpenError):
        await router.complete(_req())


def test_a10_budget_is_on_by_default(monkeypatch, settings):
    for name in ("CEAP_TASK_BUDGET_USD", "CEAP_TASK_TOKEN_BUDGET"):
        monkeypatch.delenv(name, raising=False)
    router = build_router(settings)
    assert router.task_budget_usd == DEFAULT_TASK_BUDGET_USD
    assert router.task_token_budget == DEFAULT_TASK_TOKEN_BUDGET
    monkeypatch.setenv("CEAP_TASK_BUDGET_USD", "off")
    assert build_router(settings).task_budget_usd is None


@pytest.mark.parametrize("raw", ["0", "-1", "lots"])
def test_a10_bad_cap_values_fail_closed(monkeypatch, raw):
    monkeypatch.setenv("CEAP_TASK_BUDGET_USD", raw)
    with pytest.raises(SettingsError):
        _env_cap("CEAP_TASK_BUDGET_USD", 1.0)


# ---------------------------------------------------------------- A10 rate limit


def test_a10_token_bucket_allows_burst_then_refills():
    now = [0.0]
    rl = RateLimiter(per_minute=60, burst=3, clock=lambda: now[0])
    assert [rl.acquire("alice") for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = rl.acquire("alice")
    assert 0.9 < wait <= 1.0 and retry_after_header(wait) == "1"
    assert rl.acquire("bob") == 0.0  # per principal
    now[0] += 1.0
    assert rl.acquire("alice") == 0.0


def test_a10_rate_limiter_rejects_bad_settings(monkeypatch):
    from ceap.api import ratelimit

    monkeypatch.setenv(ratelimit.PER_MINUTE_ENV, "off")
    assert ratelimit.from_env() is None
    monkeypatch.setenv(ratelimit.PER_MINUTE_ENV, "0")
    with pytest.raises(SettingsError):
        ratelimit.from_env()
    with pytest.raises(ValueError):
        RateLimiter(10, 0)
