"""Application factory: ``uvicorn ceap.api.app:app``.

Hardening expectations (not done here on purpose, keep the app portable):
run behind a reverse proxy that terminates TLS, adds security headers and
rate limits; restrict CORS at the proxy or add ``CORSMiddleware`` with an
explicit origin list; scrape ``/metrics`` from the internal network only.
"""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import FastAPI

from ceap import __version__
from ceap.api import ratelimit
from ceap.api.auth import check_key_strength
from ceap.api.loopmon import LoopLagMonitor, approval_sweeper, sweep_interval_s
from ceap.api.middleware import RequestContextMiddleware
from ceap.api.routes import router
from ceap.mcp.cpu_pool import default_pool, enable_process_pool, shutdown_default_pool
from ceap.observability.logging import configure_logging
from ceap.platform import Platform

log = logging.getLogger(__name__)


class AppState:
    """Typed container for what the routes need from ``app.state``."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.running: dict[str, asyncio.Task[object]] = {}
        self.semaphore = asyncio.Semaphore(platform.settings.max_concurrent_investigations)
        self.prewarm: asyncio.Task[None] | None = None
        self.loop_lag = LoopLagMonitor()  # G9
        self.background: list[asyncio.Task[None]] = []  # G8 sweep, G9 lag monitor
        self.rate_limiter = ratelimit.from_env()  # per-principal token bucket (A10)


#: comma-separated scenario ids generated at start-up, off the event loop (e.g. ``T01,R01``)
PREWARM_ENV = "CEAP_PREWARM_DATASETS"


def _prewarm_datasets() -> list[str]:
    return [s.strip() for s in (os.getenv(PREWARM_ENV) or "").split(",") if s.strip()]


async def _prewarm(p: Platform, scenarios: list[str]) -> None:
    for scenario in scenarios:
        try:
            if scenario.upper().startswith("R"):
                await asyncio.to_thread(p.history.get, scenario)
            else:
                await p.store.aget(scenario)
            log.info("pre-generated dataset %s", scenario)
        except KeyError:
            log.warning("%s: unknown dataset %s, skipped", PREWARM_ENV, scenario)


def create_app(platform: Platform | None = None) -> FastAPI:
    configure_logging()

    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        p = platform or Platform()
        p.settings.validate_for_serving()
        check_key_strength(p.settings)  # H11: outside dev, refuse keys under 256 estimated bits
        state = AppState(p)
        app.state.platform = p
        app.state.ceap = state
        await p.registry()  # discover MCP tools eagerly
        # G3: serve CPU tools from worker processes (unless CEAP_CPU_TOOL_PROCESSES says otherwise);
        # start the workers now so their start-up cost is never inside a step timeout
        enable_process_pool()
        pool = default_pool()
        if pool is not None:
            await asyncio.to_thread(pool.warm)
        warm = _prewarm_datasets()
        if warm:  # G1: generate in a worker thread so /health answers while the cache fills
            state.prewarm = asyncio.create_task(_prewarm(p, warm), name="dataset-prewarm")
        state.background = [
            asyncio.create_task(state.loop_lag.run(), name="event-loop-lag"),
            asyncio.create_task(approval_sweeper(p, sweep_interval_s()), name="approval-sweep"),
        ]
        if not p.settings.is_dev:
            log.warning(
                "serving with CEAP_ENV=%s: auto_approve=%s, %d API keys",
                p.settings.environment,
                p.settings.auto_approve,
                len(p.settings.api_keys),
            )
        yield
        for bg in state.background:
            bg.cancel()
        if state.background:
            await asyncio.gather(*state.background, return_exceptions=True)
        if state.prewarm is not None and not state.prewarm.done():
            state.prewarm.cancel()
        pending = [t for t in state.running.values() if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await p.mcp_client.close()
        shutdown_default_pool()  # terminate CPU-tool worker processes (G3)
        enable_process_pool(0)  # back to threads for whatever runs in this process next
        if platform is None:  # the app built it, so the app closes it (releases the SQLite file)
            p.close()

    from contextlib import asynccontextmanager

    app = FastAPI(
        title="Cash Equities Agentic Platform",
        version=__version__,
        description="Agentic AI platform that investigates, analyses and explains cash-equity execution quality.",
        lifespan=asynccontextmanager(lifespan),
    )
    app.include_router(router)
    app.add_middleware(RequestContextMiddleware)
    return app


app = create_app()
