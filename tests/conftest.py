"""Shared fixtures."""

from __future__ import annotations

import pytest

from ceap.config import Settings
from ceap.data.repositories import DatasetStore
from ceap.data.scenarios import get_scenario
from ceap.llm.client import MockLLMClient
from ceap.mcp.registry import build_in_process_client, build_tool_registry
from ceap.platform import Platform


@pytest.fixture(scope="session")
def store() -> DatasetStore:
    return DatasetStore()


@pytest.fixture(scope="session")
def dataset_t01(store):
    return store.get(get_scenario("T01"))


@pytest.fixture(scope="session")
def dataset_t06(store):
    return store.get(get_scenario("T06"))


@pytest.fixture(scope="session")
def mcp_client(store):
    return build_in_process_client(store)


@pytest.fixture
async def registry(mcp_client):
    return await build_tool_registry(mcp_client)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(llm_provider="mock", auto_approve=True)  # CEAP_ENV=dev: dev API keys apply


@pytest.fixture(scope="session")
def platform(store, settings) -> Platform:
    return Platform(settings=settings, llm=MockLLMClient(), store=store)
