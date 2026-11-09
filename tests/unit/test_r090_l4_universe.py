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
    assert len(live) >= 500 - 34  # at most the 34 scaled delistings are gone (late listers are in by t=650)
    assert len(np.array_split(live, 10)[0]) >= 46


# Hashes computed with the 0.8.0+D5 generator (commit before the scaled membership change) on the
# same short geometry: the 30-name datasets must stay byte-identical.
V080_HASHES = {
    "RS01": "0fe114705339b7264490bd8a398c44d9ff7fa15b924ae49a9014d4e907473f4f",
    "RS03": "547b40425870c27383d1112170e2e660e18621a9a061a0cb54b896ed4cfb309c",
}


def test_core_universe_hashes_unchanged_by_scaled_membership() -> None:
    gen = HistoricalGenerator(n_days=800, in_sample_days=300)
    for sid, h in V080_HASHES.items():
        assert gen.generate(get_research_scenario(sid)).dataset_hash == h


def test_500_name_membership_events_scale() -> None:
    gen = HistoricalGenerator(n_days=1400, in_sample_days=600, symbols=RESEARCH_UNIVERSE_500)
    ds = gen.generate(get_research_scenario("R01"))
    kinds = [k for _, k in ds.delistings.values()]
    assert kinds.count("merger") == 17 and kinds.count("performance") == 17
    late = [j for j in range(500) if not ds.member_mask[0, j] and j not in ds.delistings]
    assert len(late) == 50
