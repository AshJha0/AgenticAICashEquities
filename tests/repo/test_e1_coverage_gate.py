"""E1 / C-P2-1: per-package branch coverage gate."""

from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _mod():
    spec = importlib.util.spec_from_file_location("coverage_by_package", ROOT / "scripts" / "coverage_by_package.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def _file(stmts, lines, branches, cov_branches):
    return {"summary": {"num_statements": stmts, "covered_lines": lines,
                        "num_branches": branches, "covered_branches": cov_branches}}


def _report(**files):
    return {"files": {k.replace("__", "/"): v for k, v in files.items()}}


def test_files_are_grouped_by_first_package_under_ceap():
    m = _mod()
    assert m.package_of("src/ceap/api/routes.py") == "api"
    assert m.package_of("C:\\x\\src\\ceap\\analytics\\tca\\core.py".replace("\\", "/")) == "analytics"
    assert m.package_of("src/ceap/cli.py") == "ceap (top-level)"


def test_percent_is_combined_line_and_branch_figure():
    m = _mod()
    totals = m.per_package({"files": {"src/ceap/api/a.py": _file(80, 72, 20, 18),
                                      "src/ceap/api/b.py": _file(20, 20, 0, 0)}})
    assert totals["api"] == {"statements": 100, "covered_lines": 92, "branches": 20, "covered_branches": 18}
    assert abs(m.percent(totals["api"]) - 100 * 110 / 120) < 1e-9


def test_floor_is_90_and_95_for_analytics_and_domain():
    m = _mod()
    totals = {"api": {"statements": 100, "covered_lines": 91, "branches": 0, "covered_branches": 0},
              "analytics": {"statements": 100, "covered_lines": 94, "branches": 0, "covered_branches": 0}}
    rows = {r["package"]: r for r in m.evaluate(totals, baseline={})}
    assert rows["api"]["status"] == "ok" and rows["api"]["floor"] == 90
    assert rows["analytics"]["status"] == "FAIL" and rows["analytics"]["floor"] == 95


def test_baseline_ratchets_and_a_drop_fails(tmp_path, capsys):
    m = _mod()
    t = {"api": {"statements": 100, "covered_lines": 80, "branches": 0, "covered_branches": 0}}
    assert m.evaluate(t, baseline={"api": 80.0})[0]["status"] == "under floor (baseline)"
    assert m.evaluate(t, baseline={"api": 81.0})[0]["status"] == "FAIL"
    report = tmp_path / "c.json"
    report.write_text(json.dumps({"files": {"src/ceap/zzz_new/a.py": _file(10, 5, 0, 0)}}))
    md = tmp_path / "t.md"
    assert m.main([str(report), "--markdown", str(md)]) == 1
    assert "`zzz_new`" in md.read_text() and "FAIL" in md.read_text()


def test_every_baseline_entry_is_under_its_floor():
    m = _mod()
    for pkg, base in m.BASELINE.items():
        assert base < m.FLOORS.get(pkg, m.DEFAULT_FLOOR), f"{pkg}: baseline at or above floor, delete it"


def test_coverage_config_measures_branches_of_ceap():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["coverage"]
    assert cfg["run"]["branch"] is True and cfg["run"]["source"] == ["ceap"]


def test_ci_prints_the_table_and_runs_the_gate():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--cov-report=json" in ci and "scripts/coverage_by_package.py coverage.json" in ci


def test_measured_table_is_committed():
    doc = (ROOT / "docs" / "testing" / "coverage-by-package.md").read_text(encoding="utf-8")
    assert "| Package |" in doc and "`api`" in doc
