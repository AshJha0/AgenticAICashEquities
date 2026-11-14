"""Drift monitors on synthetic cassettes: exact messages and the skip rules (0.10.0 E1).

The contract tier runs each monitor on the repository; these unit tests pin the message text and
the edge rules (unknown purposes are skipped, response-side models count, no SDK installed).
"""

from __future__ import annotations

import dataclasses
import hashlib

from ceap import drift
from ceap.llm.pins import load_pins
from ceap.llm.prompts import get_prompt
from ceap.llm.recording import Cassette


def _cassette(
    prompt_sha: dict[str, str], models: tuple[str, str] = ("m-a", "m-a"), sdk: str = "x==1"
) -> Cassette:
    return Cassette.model_validate(
        {
            "provenance": {
                "provenance": "hand_built",
                "model": models[0],
                "sdk": sdk,
                "request_sha256": "0" * 64,
                "prompt_sha": prompt_sha,
            },
            "interactions": [
                {
                    "purpose": "planner",
                    "request_sha256": "0" * 64,
                    "request": {"model": models[0]},
                    "response": {"model": models[1]},
                }
            ],
        }
    )


def _sha(prompt_id: str) -> str:
    return hashlib.sha256(get_prompt(prompt_id).text.encode("utf-8")).hexdigest()


def test_staleness_names_the_changed_prompt_and_skips_unknown_purposes() -> None:
    fresh = _cassette({"planner": _sha("planner"), "reporter_claims": "f" * 64})
    assert drift.cassette_staleness([("ok.json", fresh)]) == []
    stale = _cassette({"critic": "0" * 64})
    assert drift.cassette_staleness([("old.json", stale)]) == [
        "old.json: STALE - the critic prompt changed since the cassette was authored"
    ]


def test_model_pins_check_counts_response_models() -> None:
    pins = load_pins()
    pinned = sorted(pins.allowed)[0]
    assert drift.model_pins_vs_cassettes([("a", _cassette({}, (pinned, pinned)))], pins) == []
    problems = drift.model_pins_vs_cassettes([("a", _cassette({}, (pinned, "gone-model")))], pins)
    assert problems == ["a: model gone-model is no longer pinned in model_pins.toml"]


def test_sdk_check_reports_installed_and_cassette_drift() -> None:
    pins = load_pins()
    pinned = f"{pins.sdk_package}=={pins.sdk_version}"
    assert drift.sdk_vs_cassettes([("c", _cassette({}, sdk=pinned))], pins, installed=pins.sdk_version) == []
    problems = drift.sdk_vs_cassettes([("c", _cassette({}, sdk="old==0.1"))], pins, installed="0.0.1")
    assert problems[0].startswith(f"installed {pins.sdk_package} 0.0.1 != pinned {pins.sdk_version}")
    assert problems[1] == f"c: authored with old==0.1, pinned is {pinned}; re-author it"


def test_sdk_check_uses_installed_package_when_not_given() -> None:
    pins = load_pins()
    bumped = dataclasses.replace(pins, sdk_version="0.0.0-never")
    problems = drift.sdk_vs_cassettes([], bumped)
    try:
        import anthropic  # noqa: F401
    except ImportError:
        assert problems == []
    else:
        assert len(problems) == 1 and "!= pinned 0.0.0-never" in problems[0]


def test_golden_set_version_flags_an_unbumped_edit() -> None:
    from ceap.golden import load_golden_set

    doc = load_golden_set()
    assert drift.golden_set_version() == []
    edited = {**doc, "items": [{**doc["items"][0], "primary": ["PRICE_MOVEMENT"]}, *doc["items"][1:]]}
    assert drift.golden_set_version(edited) != []


def test_rag_recall_floor_reports_both_floors_on_an_empty_corpus() -> None:
    from ceap.rag.retrieval import KnowledgeBase

    problems = drift.rag_recall_floor(KnowledgeBase([]))
    assert len(problems) == 2
    assert problems[0].startswith("held-out recall@3 0.00 below floor 0.8")
    assert problems[1].startswith("held-out MRR 0.000 below floor 0.7")


def test_repository_cassettes_load_sorted() -> None:
    names = [n for n, _ in drift.repository_cassettes()]
    assert names and names == sorted(names)
    assert all(n.startswith("investigation_") for n in names)
