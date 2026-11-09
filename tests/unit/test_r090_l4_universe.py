"""D5 (0.9.0): the 500-name synthetic research universe."""

from __future__ import annotations

import numpy as np

from ceap.data.generator_params import (
    RESEARCH_UNIVERSE,
    RESEARCH_UNIVERSE_500,
    TIER_OF,
    UNIVERSES,
    is_nasdaq,
    synthetic_universe,
    tier_of,
)
from ceap.data.historical import HistoricalGenerator
from ceap.data.research_scenarios import get_research_scenario


def test_universe_shape_and_tiers() -> None:
    u = RESEARCH_UNIVERSE_500
    assert len(u) == 500 and len(set(u)) == 500
    counts = {t: sum(tier_of(s) == t for s in u) for t in "ABC"}
    assert counts == {"A": 200, "B": 200, "C": 100}
    assert not set(u) & set(RESEARCH_UNIVERSE)
    assert UNIVERSES["synthetic500"] is u and UNIVERSES["core30"] is RESEARCH_UNIVERSE
    assert 200 <= sum(is_nasdaq(s) for s in u) <= 300
    assert len(synthetic_universe(31)) == 31


def test_core_names_keep_their_tiers() -> None:
    assert all(tier_of(s) == t for s, t in TIER_OF.items())
    assert tier_of("ZZZ") == "C"


def test_generate_500_names_short_history() -> None:
    gen = HistoricalGenerator(n_days=700, in_sample_days=300, symbols=RESEARCH_UNIVERSE_500)
    ds = gen.generate(get_research_scenario("R01"))
    assert ds.close.shape == (700, 500)
    assert set(ds.tiers) == {"A", "B", "C"}
    # every decile of the cross-section now holds 50 names on a day where everyone is a member
    t = 650
    live = np.flatnonzero(ds.member_mask[t] & np.isfinite(ds.close[t]))
    assert len(live) >= 490
    assert len(np.array_split(live, 10)[0]) >= 49
