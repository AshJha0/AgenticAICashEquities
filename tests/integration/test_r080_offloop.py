"""G1 / A10 through the API: a cold dataset no longer blocks /health; the per-principal rate limit answers 429."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from ceap.api.app import create_app
from ceap.data.repositories import DatasetStore
from ceap.llm.client import MockLLMClient
from ceap.platform import Platform

TRADER = {"X-API-Key": "dev-trader-key"}
BODY = {
    "question": "Why was slippage high for AAPL between 14:00 and 15:00?",
    "symbol": "AAPL",
    "session_date": "2026-03-10",
}


class _SlowGenerator:
    """Blocks its thread like the real generator does (about 30-80 s for T01), but for 1.5 s."""

    def __init__(self, dataset) -> None:
        self.dataset = dataset

    def generate(self, spec):
        time.sleep(1.5)
        return self.dataset


@pytest.fixture
def cold_platform(settings, dataset_t01) -> Platform:
    store = DatasetStore(generator=_SlowGenerator(dataset_t01))  # type: ignore[arg-type]
    return Platform(settings=settings, llm=MockLLMClient(), store=store)


async def test_g1_health_answers_while_a_cold_dataset_generates(cold_platform, dataset_t01):
    app = create_app(cold_platform)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            body = {
                **BODY,
                "symbol": dataset_t01.symbol,
                "session_date": dataset_t01.sim_start.date().isoformat(),
            }
            post = asyncio.create_task(c.post("/investigations", json=body, headers=TRADER))
            await asyncio.sleep(0.2)  # the POST is now inside validate_request -> store.get
            started = time.perf_counter()
            health = await c.get("/health")
            health_s = time.perf_counter() - started
            r = await post
    assert health.status_code == 200
    assert health_s < 1.0, f"/health took {health_s:.2f}s while a dataset generated"
    assert r.status_code in (202, 422)  # validated off the loop either way


async def test_a10_rate_limit_answers_429_with_retry_after(platform, monkeypatch):
    monkeypatch.setenv("CEAP_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("CEAP_RATE_LIMIT_BURST", "1")
    app = create_app(platform)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            bad = {"question": "slippage?", "symbol": "ZZZZ", "session_date": "2026-03-10"}
            first = await c.post("/investigations", json=bad, headers=TRADER)
            second = await c.post("/investigations", json=bad, headers=TRADER)
            other = await c.post("/investigations", json=bad, headers={"X-API-Key": "dev-admin-key"})
    assert first.status_code != 429
    assert second.status_code == 429 and int(second.headers["Retry-After"]) >= 1
    assert other.status_code != 429  # buckets are per principal
