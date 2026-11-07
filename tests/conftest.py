"""Shared fixtures.

Immutable, expensive inputs (dataset stores, the in-process MCP client) are session-scoped.
Anything that holds mutable state (``platform``: task table, approvals queue, caches) is
function-scoped so no test can see another test's leftovers; the random-order nightly run checks it.
"""

from __future__ import annotations

import pytest

from ceap.config import Settings
from ceap.data.historical import HistoricalStore
from ceap.data.repositories import DatasetStore
from ceap.data.scenarios import get_scenario
from ceap.llm.client import MockLLMClient
from ceap.mcp.registry import build_in_process_client, build_tool_registry
from ceap.platform import Platform


@pytest.fixture(scope="session")
def store() -> DatasetStore:
    return DatasetStore()


@pytest.fixture(scope="session")
def history() -> HistoricalStore:
    return HistoricalStore()


@pytest.fixture(scope="session")
def dataset_t01(store):
    return store.get(get_scenario("T01"))


@pytest.fixture(scope="session")
def dataset_t06(store):
    return store.get(get_scenario("T06"))


@pytest.fixture(scope="session")
def mcp_client(store, history):
    return build_in_process_client(store, history=history)


@pytest.fixture
async def registry(mcp_client):
    return await build_tool_registry(mcp_client)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(llm_provider="mock", auto_approve=True)  # CEAP_ENV=dev: dev API keys apply


@pytest.fixture
def platform(store, settings) -> Platform:
    return Platform(settings=settings, llm=MockLLMClient(), store=store)


try:  # hypothesis profiles: 100 examples on PR, 1000 nightly (HYPOTHESIS_PROFILE=nightly)
    import os

    from hypothesis import settings as hsettings

    hsettings.register_profile("pr", max_examples=100, derandomize=True, deadline=None)
    hsettings.register_profile("nightly", max_examples=1000, derandomize=True, deadline=None)
    hsettings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "pr"))
except ImportError:  # pragma: no cover - hypothesis is in the dev extra
    pass
