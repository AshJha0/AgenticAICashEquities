"""Shared fixtures.

Immutable, expensive inputs (dataset stores, the in-process MCP client) are session-scoped.
Anything that holds mutable state (``platform``: task table, approvals queue, caches) is
function-scoped so no test can see another test's leftovers; the random-order nightly run checks it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# CPU-bound MCP tools run in a thread here, not in worker processes (each would cost seconds to
# start on Windows). tests/unit/test_cpu_pool.py turns the process pool on for itself.
os.environ.setdefault("CEAP_CPU_TOOL_PROCESSES", "0")
# The trial ledger persists to var/ by default (Q7); tests keep it in memory so no test sees another's trials.
os.environ.setdefault("CEAP_TRIAL_LEDGER", ":memory:")

from ceap.config import Settings
from ceap.data.historical import HistoricalStore
from ceap.data.repositories import DatasetStore
from ceap.data.scenarios import get_scenario
from ceap.llm.client import MockLLMClient
from ceap.mcp.registry import build_in_process_client, build_tool_registry
from ceap.platform import Platform

#: H8: per-tier ceilings (the 120 s default is ``timeout`` in pyproject.toml)
TIER_TIMEOUTS = {"nightly": 1800, "slow": 600}
#: Multiplier for the tier ceilings on a shared or loaded machine (default 1). A pytest-timeout
#: expiry ends the whole process on Windows (thread method), losing its coverage data, so a slow
#: machine needs a longer ceiling rather than a lost run. CI leaves it unset.
TIMEOUT_SCALE = float(os.environ.get("CEAP_TEST_TIMEOUT_SCALE", "1"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Give slow and nightly tests an explicit, longer timeout unless they set their own."""
    for item in items:
        if item.get_closest_marker("timeout") is not None:
            continue
        for marker, seconds in TIER_TIMEOUTS.items():
            if item.get_closest_marker(marker) is not None:
                item.add_marker(pytest.mark.timeout(seconds * TIMEOUT_SCALE))
                break


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

    # PR: derandomised, so a PR is never red from a fresh example. Nightly (E2, E-13): randomised, so
    # each night explores new examples; print_blob prints the @reproduce_failure line for a failure.
    hsettings.register_profile("pr", max_examples=100, derandomize=True, deadline=None)
    hsettings.register_profile("nightly", max_examples=1000, derandomize=False, deadline=None, print_blob=True)
    hsettings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "pr"))
except ImportError:  # pragma: no cover - hypothesis is in the dev extra
    pass



@pytest.fixture(autouse=True, scope="session")
def _paper_clock_after_synthetic_panel() -> Iterator[None]:
    """Synthetic paper panels run past today; move the completed-session guard's clock past them.

    Session-scoped so module-scoped fixtures see it too. Tests of the guard itself are marked
    ``real_paper_clock`` and get the wall clock back from ``_real_paper_clock``.
    """
    from datetime import UTC, datetime

    import ceap.paper.job as job

    real = job._utcnow
    job._utcnow = lambda: datetime(2100, 1, 1, tzinfo=UTC)
    job._real_utcnow = real  # type: ignore[attr-defined]
    yield
    job._utcnow = real


@pytest.fixture(autouse=True)
def _real_paper_clock(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("real_paper_clock"):
        import ceap.paper.job as job

        monkeypatch.setattr(job, "_utcnow", job._real_utcnow)
