"""The AI concepts tutorial track (docs/learn/ai): lessons exist, are indexed, and links resolve."""

from __future__ import annotations

import re

import pytest
from repo_root import ROOT

TRACK = ROOT / "docs" / "learn" / "ai"
LESSONS = (
    "01-llms.md",
    "02-agents-and-the-harness.md",
    "03-guardrails.md",
    "04-rag.md",
    "05-classical-ml.md",
    "06-evaluating-ai.md",
    "07-local-models.md",
)
HEADINGS = (
    "## Plain idea",
    "## Formal statement",
    "## Worked example from this repo",
    "## Offline exercise",
    "## Pitfalls",
    "## Self-check",
    "## Further reading",
)
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _text(name: str) -> str:
    return (TRACK / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", LESSONS)
def test_lesson_exists_and_is_indexed(name: str) -> None:
    assert (TRACK / name).is_file()
    assert f"]({name})" in _text("README.md")


@pytest.mark.parametrize("name", LESSONS)
def test_lesson_headings_in_order(name: str) -> None:
    text = _text(name)
    positions = [text.find(h) for h in HEADINGS]
    assert -1 not in positions, name
    assert positions == sorted(positions), name
    assert "<details>" in text


@pytest.mark.parametrize("name", ("README.md", *LESSONS))
def test_relative_links_resolve(name: str) -> None:
    for target in LINK.findall(_text(name)):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        path = target.split("#", 1)[0]
        assert (TRACK / path).resolve().exists(), f"{name}: {target}"


def test_track_is_linked_from_learn_and_index() -> None:
    assert "docs/learn/ai/README.md" in (ROOT / "LEARN.md").read_text(encoding="utf-8")
    assert "learn/ai/README.md" in (ROOT / "docs" / "INDEX.md").read_text(encoding="utf-8")
    assert "docs/learn/ai/README.md" in (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
