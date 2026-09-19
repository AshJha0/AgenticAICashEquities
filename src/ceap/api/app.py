"""Application factory: ``uvicorn ceap.api.app:app``.

Hardening expectations (not done here on purpose, keep the app portable):
run behind a reverse proxy that terminates TLS, adds security headers and
rate limits; restrict CORS at the proxy or add ``CORSMiddleware`` with an
explicit origin list; scrape ``/metrics`` from the internal network only.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

from ceap import __version__
from ceap.api.routes import router
from ceap.observability.logging import configure_logging
from ceap.platform import Platform

log = logging.getLogger(__name__)


class AppState:
    """Typed container for what the routes need from ``app.state``."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.running: dict[str, asyncio.Task[object]] = {}
        self.semaphore = asyncio.Semaphore(platform.settings.max_concurrent_investigations)


def create_app(platform: Platform | None = None) -> FastAPI:
    configure_logging()

    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        p = platform or Platform()
        p.settings.validate_for_serving()
        state = AppState(p)
        app.state.platform = p
        app.state.ceap = state
        await p.registry()  # discover MCP tools eagerly
        if not p.settings.is_dev:
            log.warning(
                "serving with CEAP_ENV=%s: auto_approve=%s, %d API keys",
                p.settings.environment,
                p.settings.auto_approve,
                len(p.settings.api_keys),
            )
        yield
        pending = [t for t in state.running.values() if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await p.mcp_client.close()

    from contextlib import asynccontextmanager

    app = FastAPI(
        title="Cash Equities Agentic Platform",
        version=__version__,
        description="Agentic AI platform that investigates, analyses and explains cash-equity execution quality.",
        lifespan=asynccontextmanager(lifespan),
    )
    app.include_router(router)
    return app


app = create_app()
