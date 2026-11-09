"""I5 (0.9.0): CLI tests. The CLI is a thin argparse layer; these pin its contract cheaply.

Each test calls ``ceap.cli.main(argv)`` in-process (no subprocess, no network, no LLM key) and
checks the exit code and a stable fragment of the output.
"""

from __future__ import annotations

import pytest

from ceap.cli import build_parser, main


def test_help_lists_every_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("investigate", "research", "scenarios", "evaluate", "tca-report", "serve"):
        assert cmd in out


def test_missing_subcommand_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["investigate", "why?", "--role", "superuser"])
    assert exc.value.code == 2


def test_scenarios_lists_templates(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scenarios"]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) >= 10
    assert all("truth=" in ln for ln in lines)


def test_scenarios_all_lists_fifty(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scenarios", "--all"]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 50


def test_research_scenarios_all_lists_thirty(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["research-scenarios", "--all"]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 30
