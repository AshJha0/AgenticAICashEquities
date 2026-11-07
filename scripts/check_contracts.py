"""Report public-signature changes in ``ceap.domain`` between two git revisions.

CI runs this on pull requests: if any public class, field, enum member or function signature in
``src/ceap/domain`` changed and the PR does not carry the ``contracts-change`` label, the job fails.
Usage: python scripts/check_contracts.py <base-ref> [<head-ref>]   (head defaults to the work tree)
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

DOMAIN = "src/ceap/domain"


def _class_sigs(node: ast.ClassDef) -> list[str]:
    bases = ",".join(ast.unparse(b) for b in node.bases)
    out = [f"class {node.name}({bases})"]
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            default = "" if item.value is None else "=" + ast.unparse(item.value)
            out.append(f"  {node.name}.{item.target.id}: {ast.unparse(item.annotation)}{default}")
        elif isinstance(item, ast.Assign):
            for t in item.targets:
                if isinstance(t, ast.Name) and not t.id.startswith("_"):
                    out.append(f"  {node.name}.{t.id} = {ast.unparse(item.value)}")
        elif isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
            if not item.name.startswith("_") or item.name == "__init__":
                out.append(f"  {node.name}.{item.name}({ast.unparse(item.args)})")
    return out


def signatures(sources: dict[str, str]) -> set[str]:
    sigs: set[str] = set()
    for name, text in sources.items():
        for node in ast.parse(text).body:
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                sigs.update(f"{name}: {s}" for s in _class_sigs(node))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_"):
                sigs.add(f"{name}: def {node.name}({ast.unparse(node.args)})")
    return sigs


def _git_sources(ref: str) -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref, DOMAIN], capture_output=True, text=True, check=True
    ).stdout.split()
    return {
        f: subprocess.run(["git", "show", f"{ref}:{f}"], capture_output=True, text=True, check=True).stdout
        for f in files
        if f.endswith(".py")
    }


def _tree_sources() -> dict[str, str]:
    return {p.as_posix(): p.read_text(encoding="utf-8") for p in sorted(Path(DOMAIN).glob("*.py"))}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    base = signatures(_git_sources(argv[1]))
    head = signatures(_git_sources(argv[2]) if len(argv) > 2 else _tree_sources())
    removed, added = sorted(base - head), sorted(head - base)
    for s in removed:
        print(f"- {s}")
    for s in added:
        print(f"+ {s}")
    if removed or added:
        print(f"contracts: {len(removed)} removed, {len(added)} added", file=sys.stderr)
        return 1
    print("contracts: no public domain signature changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
