"""E2 (R-25, E-13): the nightly can go green, its gates bite, and a release needs it green."""

from __future__ import annotations

import contextvars
import json
import re
import tomllib
from datetime import date

import pytest
from repo_root import ROOT
from repo_root import load_script as _load_script


def test_ratchet_fails_on_null_after_the_set_date_and_on_an_empty_run(monkeypatch, capsys):
    mod = _load_script("mutation_ratchet")
    base = json.loads(mod.BASELINE.read_text(encoding="utf-8"))
    until = date.fromisoformat(base["null_allowed_until"])
    monkeypatch.setattr(mod, "results", lambda: {"m1": "killed", "m2": "survived"})
    if base["survived"] is None:
        assert mod.main([], today=until) == 0  # still inside the grace period: reports only
        assert mod.main([], today=date.fromordinal(until.toordinal() + 1)) == 1
        assert "null_allowed_until" in capsys.readouterr().out
    assert mod.null_expired({"survived": None}, until)  # no date at all counts as expired
    monkeypatch.setattr(mod, "results", lambda: {})  # mutmut crashed before testing a mutant
    assert mod.main([], today=until) == 1
    assert "recorded no mutants" in capsys.readouterr().out


def test_mutmut_config_copies_the_whole_package_and_its_data():
    """The nightly mutation job crashed in its stats run: only the two sub-packages were copied."""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mutmut"]
    assert cfg["source_paths"] == ["src/"] and "paths_to_mutate" not in cfg and "tests_dir" not in cfg
    assert cfg["only_mutate"] == ["src/ceap/analytics/*", "src/ceap/domain/*"]
    assert {"data/", "docs/"} <= set(cfg["also_copy"])


def test_hypothesis_is_randomised_in_the_nightly_profile_only():
    from hypothesis import settings

    assert settings.get_profile("nightly").derandomize is False
    assert settings.get_profile("pr").derandomize is True
    nightly = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    assert "HYPOTHESIS_PROFILE: nightly" in nightly
    assert not re.search(r"derandomize\s*=\s*True", (ROOT / "tests" / "unit" / "paper" / "test_fills.py").read_text())


def test_nightly_sweeps_run_in_their_own_job_and_cause_recall_is_split():
    nightly = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    full, sweeps = nightly.split("\n  sweeps:\n")[0], nightly.split("\n  sweeps:\n")[1].split("\n  mutation:\n")[0]
    assert '-m "not nightly"' in full and "CEAP_NIGHTLY" not in full.split("steps:")[0]
    assert "-m nightly --no-cov" in sweeps and 'CEAP_NIGHTLY: "1"' in sweeps
    assert int(re.search(r"timeout-minutes: (\d+)", sweeps).group(1)) >= 120
    src = (ROOT / "tests" / "evaluation" / "test_r2t_acceptance.py").read_text(encoding="utf-8")
    assert re.search(r'parametrize\("template", \["T05", "T06", "T07", "T08", "T09", "T10"\]\)\s*\ndef test_cause_recall_200_seeds', src)


def test_release_gate_requires_the_last_three_nightlies_green():
    mod = _load_script("release_check")

    def run(day, conclusion="success", status="completed"):
        return {"id": day, "status": status, "conclusion": conclusion, "created_at": f"2026-11-{day:02d}T03:17:00Z"}

    green = [run(10), run(11), run(12)]
    assert "green" in mod.evaluate_nightly_runs(green)
    assert mod.evaluate_nightly_runs([run(9, "failure"), *green, run(13, None, "in_progress")])
    for runs in (
        green[:2],  # too few completed runs
        [*green, run(13, "failure")],  # latest red
        [run(10), run(11, "cancelled"), run(12)],  # a time-out shows as cancelled
    ):
        with pytest.raises(mod.ReleaseRefusedError):
            mod.evaluate_nightly_runs(runs)
    assert mod.NIGHTLY_GREEN_STREAK == 3 and mod.NIGHTLY_WORKFLOW == "nightly.yml"


def test_task_scope_finalised_in_another_context_does_not_raise():
    """The nightly of 2026-11-13 logged ValueError from task_scope when an abandoned task was GC'd."""
    from ceap.llm.router import _current_task, task_scope

    def enter_here_exit_elsewhere() -> str | None:
        cm = task_scope("t-1")
        cm.__enter__()
        contextvars.Context().run(cm.__exit__, None, None, None)  # raised ValueError before the fix
        return _current_task.get()

    assert contextvars.copy_context().run(enter_here_exit_elsewhere) == "t-1"
