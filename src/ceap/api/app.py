"""Application factory: ``uvicorn ceap.api.app:app``."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ceap import __version__
from ceap.api.routes import router
from ceap.observability.logging import configure_logging
from ceap.platform import Platform


def create_app(platform: Platform | None = None) -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.platform = platform or Platform()
        app.state.running: dict[str, asyncio.Task] = {}
        await app.state.platform.registry()  # discover MCP tools eagerly
        yield
        for task in list(app.state.running.values()):
            if not task.done():
                task.cancel()
        await app.state.platform.mcp_client.close()

    app = FastAPI(
        title="Cash Equities Agentic Platform",
        version=__version__,
        description="Agentic AI platform that investigates, analyses and explains cash-equity execution quality.",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()
