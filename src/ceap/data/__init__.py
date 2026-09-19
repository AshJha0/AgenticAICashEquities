"""Synthetic cash-equity data: market, orders, executions, engineering, risk."""

from ceap.data.repositories import (
    DatasetStore,
    InMemoryExecutionRepository,
    InMemoryMarketDataRepository,
)
from ceap.data.scenarios import SCENARIO_TEMPLATES, ScenarioSpec, all_scenarios, get_scenario
from ceap.data.synthetic import SyntheticDataset, SyntheticMarketGenerator

__all__ = [
    "SCENARIO_TEMPLATES",
    "DatasetStore",
    "InMemoryExecutionRepository",
    "InMemoryMarketDataRepository",
    "ScenarioSpec",
    "SyntheticDataset",
    "SyntheticMarketGenerator",
    "all_scenarios",
    "get_scenario",
]
