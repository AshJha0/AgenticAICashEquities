"""E5 (R-34): every module is wired into the product path or labelled in the docs table."""

from __future__ import annotations

import shutil

from repo_root import ROOT, load_script

w = load_script("wired_or_labelled")


def test_every_module_is_wired_or_labelled():
    assert w.gaps(ROOT) == {"unlabelled": [], "stale": []}


def test_execution_simulator_is_counted_library_only():
    rows = w.labelled(ROOT / w.TABLE_DOC)
    assert {"ceap.execution.algo", "ceap.execution.rules", "ceap.execution.cost_loop"} <= rows
    text = (ROOT / w.TABLE_DOC).read_text(encoding="utf-8")
    assert "| `ceap.execution.rules` | library-only |" in text


def _tree(tmp_path):
    src = tmp_path / "src" / "ceap"
    (src / "mcp" / "demo").mkdir(parents=True)
    for f in ("__init__.py", "mcp/__init__.py", "mcp/demo/__init__.py"):
        (src / f).write_text("", encoding="utf-8")
    (src / "cli.py").write_text(
        "def main():\n    from ceap import used  # lazy import counts\n", encoding="utf-8"
    )
    (src / "used.py").write_text("from .helper import x\n", encoding="utf-8")
    (src / "helper.py").write_text("x = 1\n", encoding="utf-8")
    (src / "mcp" / "demo" / "__main__.py").write_text("import ceap.served\n", encoding="utf-8")
    (src / "served.py").write_text("", encoding="utf-8")
    doc = tmp_path / w.TABLE_DOC
    doc.parent.mkdir(parents=True)
    doc.write_text(f"{w.BEGIN}\n| Module | Status | Why |\n|---|---|---|\n{w.END}\n", encoding="utf-8")
    return src, doc


def test_a_new_uncalled_module_fails_until_labelled(tmp_path):
    src, doc = _tree(tmp_path)
    assert w.gaps(tmp_path) == {"unlabelled": [], "stale": []}  # imports followed, relative and lazy
    (src / "orphan.py").write_text("def unused():\n    return 1\n", encoding="utf-8")
    assert w.gaps(tmp_path)["unlabelled"] == ["ceap.orphan"]
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(w.END, f"| `ceap.orphan` | library-only | x |\n{w.END}")
    )
    assert w.gaps(tmp_path) == {"unlabelled": [], "stale": []}


def test_a_row_for_a_wired_or_deleted_module_is_stale(tmp_path):
    src, doc = _tree(tmp_path)
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            w.END, f"| `ceap.helper` | library-only | x |\n| `ceap.gone` | library-only | x |\n{w.END}"
        )
    )
    assert w.gaps(tmp_path)["stale"] == ["ceap.gone", "ceap.helper"]
    shutil.rmtree(src / "mcp")
    assert "ceap.served" in w.gaps(tmp_path)["unlabelled"]  # an MCP server is an entry point
