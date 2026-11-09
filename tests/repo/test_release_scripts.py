"""Release gate and contract checker scripts (moved from tests/unit/test_r0_foundations.py).

Repository-integrity tier (H5): these tests read files outside ``src`` and ``tests`` (scripts, docs,
committed data), so they live here and resolve the repository root explicitly via ``repo_root``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from repo_root import load_script as _load_script


def test_contract_checker_flags_signature_changes() -> None:
    mod = _load_script("check_contracts")
    before = {"d.py": "class A:\n    x: int\n"}
    after = {"d.py": "class A:\n    x: int\n    y: str = ''\n"}
    assert mod.signatures(before) != mod.signatures(after)
    assert mod.signatures(before) == mod.signatures({"d.py": "class A:\n    x: int\n    _p = 1\n"})


def test_release_gate_refuses_red_or_missing_checks() -> None:
    mod = _load_script("release_check")

    def run(name, conclusion="success", status="completed", created="2026-11-08T10:00:00Z", attempt=1):
        return {
            "name": name,
            "id": 1,
            "event": "push",
            "status": status,
            "conclusion": conclusion,
            "created_at": created,
            "run_attempt": attempt,
        }

    ci = run("CI")
    # only CI gates: a refused daily paper session or a red nightly on the same commit does not block
    assert mod.evaluate_workflow_runs([ci, run("paper-daily", "failure"), run("Nightly", "failure")])
    # the latest CI run decides (a green re-run after a red one releases; a red latest refuses)
    assert mod.evaluate_workflow_runs([run("CI", "failure"), run("CI", created="2026-11-08T11:00:00Z")])
    for runs in (
        [],
        [run("paper-daily")],
        [run("CI", "failure")],
        [run("CI", None, "in_progress")],
        [ci, run("CI", "failure", created="2026-11-08T11:00:00Z")],
    ):
        with pytest.raises(mod.ReleaseRefusedError):
            mod.evaluate_workflow_runs(runs)


def test_release_gate_checks_versions_and_changelog(tmp_path: Path) -> None:
    mod = _load_script("release_check")
    (tmp_path / "src" / "ceap").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.4.0"\n')
    (tmp_path / "src" / "ceap" / "__init__.py").write_text('__version__ = "0.4.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [0.4.0] - 2026-11-29\n")
    assert mod.versions("v0.4.0", tmp_path) == "0.4.0"
    mod.changelog("0.4.0", tmp_path)
    with pytest.raises(mod.ReleaseRefusedError):
        mod.versions("v0.4.1", tmp_path)
    with pytest.raises(mod.ReleaseRefusedError):
        mod.changelog("0.4.1", tmp_path)


def test_version_agrees_in_repo() -> None:
    mod = _load_script("release_check")
    import ceap

    assert mod.versions(f"v{ceap.__version__}") == ceap.__version__
