"""Every module is wired into the product path or labelled library-only (0.10.0 item E5, R-34).

    python scripts/wired_or_labelled.py            # exit 1 and list the gaps when the table is wrong

Plain idea: a module the product never imports is not a delivered feature, whatever a document says.
This script follows imports statically (including imports inside functions) from the product entry
points: the ``ceap`` command (``ceap.cli``), the HTTP API (``ceap.api``), ``ceap.platform`` and
each MCP server's ``__main__``. A module no entry point reaches must have a row in the
"Wired or labelled" table of ``docs/architecture/overview.md``; a row for a module that is reached,
or that no longer exists, is also an error. So adding an uncalled module fails until it is either
wired or labelled, and wiring a labelled module fails until its row is removed.

Reaching a module means it is importable from the product, not that every function in it is
called; the table is a floor, not a proof of use.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLE_DOC = Path("docs/architecture/overview.md")
BEGIN, END = "<!-- wired-or-labelled:begin -->", "<!-- wired-or-labelled:end -->"
ROW = re.compile(r"^\|\s*`(ceap(?:\.[A-Za-z_0-9]+)*)`\s*\|")


def modules(src: Path) -> dict[str, Path]:
    """Dotted module name -> file, for every module under ``src/ceap`` (packages by their name)."""
    out: dict[str, Path] = {}
    for f in sorted((src / "ceap").rglob("*.py")):
        parts = f.relative_to(src).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        out[name] = f
    return out


def imports(name: str, path: Path, known: dict[str, Path]) -> set[str]:
    """Known modules that ``name`` imports anywhere in its body (relative imports resolved)."""
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    raw: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            raw.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            raw.add(mod)
            raw.update(f"{mod}.{a.name}" for a in node.names)  # ``from pkg import submodule``
    found: set[str] = set()
    for dotted in raw:
        parts = dotted.split(".")
        found.update(".".join(parts[:i]) for i in range(1, len(parts) + 1) if ".".join(parts[:i]) in known)
    return found


def entry_points(known: dict[str, Path]) -> list[str]:
    fixed = ["ceap.cli", "ceap.api", "ceap.platform"]
    servers = sorted(m for m in known if m.startswith("ceap.mcp.") and m.endswith(".__main__"))
    return [m for m in fixed if m in known] + servers


def unreached(src: Path) -> set[str]:
    known = modules(src)
    seen: set[str] = set()
    stack = entry_points(known)
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        parents = {mod.rsplit(".", i)[0] for i in range(1, mod.count(".") + 1)}  # importing runs them
        stack.extend((imports(mod, known[mod], known) | (parents & set(known))) - seen)
    return set(known) - seen


def labelled(doc: Path) -> set[str]:
    text = doc.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        raise SystemExit(f"{doc}: the wired-or-labelled table markers are missing")
    body = text.split(BEGIN, 1)[1].split(END, 1)[0]
    return {m.group(1) for line in body.splitlines() if (m := ROW.match(line.strip()))}


def gaps(root: Path = ROOT) -> dict[str, list[str]]:
    """``unlabelled``: unreached modules with no row; ``stale``: rows for reached or missing modules."""
    reached_not = unreached(root / "src")
    rows = labelled(root / TABLE_DOC)
    return {"unlabelled": sorted(reached_not - rows), "stale": sorted(rows - reached_not)}


def main() -> int:
    found = gaps()
    for kind, names in found.items():
        for n in names:
            print(f"{kind}: {n}")
    if any(found.values()):
        print(f"fix: wire the module into the product, or add/remove its row in {TABLE_DOC}")
        return 1
    print("every module is wired into the product path or labelled library-only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
