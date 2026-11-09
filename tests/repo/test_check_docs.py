"""3E.3: scripts/check_docs.py passes on the repository and fails on each kind of drift."""

from __future__ import annotations

import importlib.util
import json

from repo_root import ROOT

spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
check_docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_docs)
LATEST = json.loads((ROOT / "docs/evaluation/latest.json").read_text(encoding="utf-8"))


def test_3e3_repository_docs_are_clean():
    assert check_docs.main() == 0


def test_3e3_docs_number_mismatch_fails():
    text = "accuracy <!-- metric: capability.primary_accuracy -->0.51"
    assert check_docs.check_metrics(text, "x.md", LATEST)


def test_3e3_unsourced_landing_result_fails_and_labels_pass():
    assert check_docs.check_landing("The build scores 0.97 accuracy.", "README.md")
    assert not check_docs.check_landing("Live accuracy is UNKNOWN / NOT RUN LIVE (0.0).", "README.md")
    assert not check_docs.check_landing("accuracy target 0.90", "README.md")


def test_3e3_undocumented_command_fails(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text(
        "```bash\nceap evaluate --no-such-flag\nceap evaluation-report --quick\n```\n", encoding="utf-8"
    )
    check_docs.ROOT = tmp_path
    try:
        problems = check_docs.check_commands([doc])
    finally:
        check_docs.ROOT = ROOT
    assert len(problems) == 1 and "--no-such-flag" in problems[0]
