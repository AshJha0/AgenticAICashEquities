"""3E.4 drift monitors: each is clean on the repository and reports on a failing fixture."""

from __future__ import annotations

import copy
import dataclasses

from ceap import drift
from ceap.golden import load_golden_set
from ceap.llm.pins import load_pins


def _cassettes():
    return drift.repository_cassettes()


def test_drift_cassette_staleness():
    cassettes = _cassettes()
    assert cassettes and drift.cassette_staleness(cassettes) == []
    name, c = cassettes[0]
    stale = c.model_copy(deep=True)
    stale.provenance.prompt_sha["planner"] = "0" * 64  # as if the planner prompt had changed
    assert any("STALE" in p for p in drift.cassette_staleness([(name, stale)]))


def test_drift_rag_recall_floor():
    from ceap.rag.retrieval import KnowledgeBase

    assert drift.rag_recall_floor() == []
    assert drift.rag_recall_floor(KnowledgeBase([])) != []  # an emptied corpus breaks the floor


def test_drift_golden_set_version():
    doc = load_golden_set()
    assert drift.golden_set_version(doc) == []
    edited = copy.deepcopy(doc)
    edited["items"][0]["primary"] = ["PRICE_MOVEMENT"]  # an edit without a version bump
    assert drift.golden_set_version(edited) != []


def test_drift_model_pins_vs_cassettes():
    pins = load_pins()
    assert drift.model_pins_vs_cassettes(_cassettes(), pins) == []
    unpinned = dataclasses.replace(pins, allowed=frozenset({"claude-haiku-5-5"}))
    assert drift.model_pins_vs_cassettes(_cassettes(), unpinned) != []


def test_drift_sdk_version_vs_cassettes():
    pins = load_pins()
    assert drift.sdk_vs_cassettes(_cassettes(), pins, installed=pins.sdk_version) == []
    bumped = dataclasses.replace(pins, sdk_version="9.9.9")  # a Dependabot bump of the pin
    problems = drift.sdk_vs_cassettes(_cassettes(), bumped, installed="9.9.9")
    assert problems and all("re-author" in p for p in problems)
    assert drift.sdk_vs_cassettes([], pins, installed="9.9.9") != []  # installed SDK drifted from the pin
