"""E6 (C-P2-2): the interpreters we declare are the interpreters CI tests, and goldens are one set."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_declared_python_versions_equal_the_ci_test_matrix():
    classifiers = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["classifiers"]
    declared = {c.rsplit("::", 1)[1].strip() for c in classifiers if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c)}
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    matrix = re.search(r"^\s+python-version: \[(.+)\]$", ci, re.MULTILINE)
    assert matrix, "test job matrix not found"
    tested = {v.strip().strip('"') for v in matrix.group(1).split(",")}
    assert declared == tested and "3.13" in tested


def test_goldens_are_one_set_compared_on_every_interpreter():
    """No per-interpreter golden files and no version branches, so each matrix leg checks the same bytes."""
    golden_dir = ROOT / "tests" / "golden"
    assert not [p.name for p in golden_dir.iterdir() if re.search(r"(py|python)[-_]?3\d+|cp3\d+", p.name)]
    for path in [*golden_dir.glob("*.py"), ROOT / "src" / "ceap" / "golden.py"]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"sys\.version_info|python_version\(|sys\.version\b", text), path.name
