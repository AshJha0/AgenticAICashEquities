"""H13 (0.9.0): micro-benchmarks for the analytics hot paths, gated in CI.

Not collected by the normal test run (``testpaths = ["tests"]``) and they need pytest-benchmark,
which is installed only in the CI ``benchmarks`` job (ADR 0003). The job runs this file against the
PR's base commit and its head on the same runner and fails if any mean is more than 25 % slower.

Run locally: pip install pytest-benchmark==5.3.1; pytest benchmarks --benchmark-only --no-cov -o addopts=
Inputs are fixed-seed synthetic arrays, so a run measures code, not data.
"""

from __future__ import annotations

import numpy as np
import pytest

from ceap.analytics.implementation_shortfall import shortfall_components
from ceap.analytics.signal_statistics import rowwise_rank_corr
from ceap.analytics.volatility import realised_volatility_bps
from ceap.analytics.vwap import vwap
from ceap.domain.execution import Side

RNG = np.random.default_rng(20261109)
N_FILLS = 5_000
PRICES = 100.0 + RNG.normal(0.0, 0.2, N_FILLS).cumsum() * 0.01
QTYS = RNG.integers(1, 500, N_FILLS).astype(float)
PATH = 100.0 * np.exp(RNG.normal(0.0, 1e-4, 23_400).cumsum())
SIG = RNG.normal(size=(250, 500))
RET = 0.05 * SIG + RNG.normal(size=(250, 500))


@pytest.mark.benchmark(group="tca")
def test_bench_vwap(benchmark):  # type: ignore[no-untyped-def]
    benchmark(vwap, PRICES, QTYS)


@pytest.mark.benchmark(group="tca")
def test_bench_shortfall_components(benchmark):  # type: ignore[no-untyped-def]
    target = float(QTYS.sum()) * 1.2
    benchmark(shortfall_components, Side.BUY, 100.0, 100.01, PRICES, QTYS, PRICES, target, 101.0)


@pytest.mark.benchmark(group="market")
def test_bench_realised_vol(benchmark):  # type: ignore[no-untyped-def]
    benchmark(realised_volatility_bps, PATH)


@pytest.mark.benchmark(group="research")
def test_bench_rowwise_rank_corr(benchmark):  # type: ignore[no-untyped-def]
    benchmark(rowwise_rank_corr, SIG, RET)
