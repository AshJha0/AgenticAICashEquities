"""G2: the vectorised ``_trades`` reproduces v0.7 bit for bit.

The pinned digest was computed with the v0.7.1 per-trade loop (``origin/main`` at ebc9dfb) on the
inputs below, then reproduced by the vectorised code; any change to the random-draw order, the
rounding or the field formatting changes it. The scenario goldens in ``tests/golden`` pin the same
thing end to end.
"""

from __future__ import annotations

import hashlib

import numpy as np

from ceap.data.synthetic import SyntheticMarketGenerator, intraday_profile

V071_DIGEST = "b4e4fa171676f7650eaf52941ff23f34ccf5b41f812ad5fbee741f07afbba5c6"


def test_trades_match_the_v071_digest() -> None:
    ts = 1.79e9 + np.arange(3600.0)
    profile = intraday_profile(np.arange(3600.0) + 16200)
    bids = np.round(200.0 + np.cumsum(np.random.default_rng(0).normal(0, 0.01, 3600)), 2)
    asks = bids + 0.02
    trades = SyntheticMarketGenerator()._trades(np.random.default_rng(42), "MSFT", ts, bids, asks, profile)
    assert len(trades) == 5108
    assert hashlib.sha256("\n".join(repr(t) for t in trades).encode()).hexdigest() == V071_DIGEST


def test_no_prints_when_the_profile_is_zero() -> None:
    ts = 1.79e9 + np.arange(10.0)
    z = np.zeros(10)
    assert SyntheticMarketGenerator()._trades(np.random.default_rng(1), "X", ts, z + 1, z + 1.02, z) == []
