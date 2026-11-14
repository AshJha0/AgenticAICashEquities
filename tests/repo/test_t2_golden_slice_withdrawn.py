"""0.10.0 T2 (finding R-01): the local-model golden-slice rows stay withdrawn.

The 0.9.0 golden slice (primary cause 6/9, must-mention 9/9, verdict 3/3) was answered by the mock
fallback, not the model. Acceptance: ``git grep "golden slice cause 6/9"`` finds nothing outside the
review folder and the CHANGELOG correction note. The second test keeps out the explanation the
review showed to be false ("the model's replies are identical" live and on replay).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from repo_root import ROOT

# Where the withdrawn wording may still appear: the review that found it and the correction notes.
ALLOWED_PREFIXES = ("docs/review/", "CHANGELOG.md", "tests/repo/test_t2_golden_slice_withdrawn.py")


def _git_grep(phrase: str) -> list[str]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(
        ["git", "grep", "-n", "-i", "-F", phrase],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert out.returncode in (0, 1), out.stderr  # 1 means no match
    return [line for line in out.stdout.splitlines() if not line.startswith(ALLOWED_PREFIXES)]


def test_golden_slice_cause_6_of_9_is_not_published():
    assert _git_grep("golden slice cause 6/9") == []


@pytest.mark.parametrize(
    "phrase",
    [
        "replay of the same cassette 2/3",  # the replayed golden answers were mock output
        "Model replies are identical; replay is the",
    ],
)
def test_false_replay_explanation_is_not_published(phrase):
    assert _git_grep(phrase) == []
