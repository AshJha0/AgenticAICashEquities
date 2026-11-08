"""Docs check (PLAN 3E.3). CI fails when a documented number or command does not match reality.

1. **Annotated metrics.** ``<!-- metric: capability.primary_accuracy -->0.98`` - the number after
   the annotation must equal the value at that path in ``docs/evaluation/latest.json`` (the
   committed output of ``ceap evaluation-report --json``) at the precision shown.
2. **Landing pages.** On README, LEARN, COOKBOOK, the architecture overview and the AI page, every
   line that states a measured result (accuracy, coverage, false-positive/flag/PROMOTE rate, recall,
   MRR, power, "scores", "verdicts") with a number must carry a metric annotation, a
   ``<!-- source: path -->`` annotation naming an existing artefact, or the label UNKNOWN /
   NOT RUN LIVE. Targets and floors (lines with "target", "floor", "bar", ">=", "≥") are not results.
3. **Generated table.** The block between ``<!-- evaluation-report:begin -->`` and
   ``<!-- evaluation-report:end -->`` in ``docs/evaluation/evaluation.md`` equals
   ``render_markdown(latest.json)``.
4. **Documented commands.** Every ``ceap ...`` line inside a fenced shell block parses with the real
   CLI parser (flags and subcommands exist). Commands are parsed, not executed: executing them would
   take hours and some start servers.

Usage: ``python scripts/check_docs.py`` (exit 1 on any problem).
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LANDING = (
    "README.md",
    "LEARN.md",
    "COOKBOOK.md",
    "docs/architecture/overview.md",
    "docs/ai-agents/ai-agents.md",
)
LATEST = ROOT / "docs" / "evaluation" / "latest.json"
EVAL_PAGE = ROOT / "docs" / "evaluation" / "evaluation.md"
METRIC = re.compile(r"<!-- metric: ([\w.]+) -->\s*(-?\d+(?:\.\d+)?)")
SOURCE = re.compile(r"<!-- source: ([^ ]+) -->")
RESULT_WORDS = re.compile(
    r"(?i)\b(accuracy|coverage|false[- ](?:positive|flag|promote)|recall|mrr|power|scores?|verdicts?)\b"
)
NUMBER = re.compile(r"\b\d+(?:\.\d+)?\s*%|\b0\.\d+\b|\b\d+/\d+\b")
EXEMPT = re.compile(r"(?i)UNKNOWN|NOT RUN LIVE|\btarget|\bfloor|\bbar\b|>=|≥|\bthreshold")
FENCE = re.compile(r"^```(\w*)\s*$")


def lookup(doc: dict[str, Any], path: str) -> Any:
    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(path)
        node = node[part]
    return node


def check_metrics(text: str, name: str, latest: dict[str, Any]) -> list[str]:
    problems = []
    for m in METRIC.finditer(text):
        path, shown = m.group(1), m.group(2)
        try:
            value = lookup(latest, path)
        except KeyError:
            problems.append(f"{name}: metric {path} is not in latest.json")
            continue
        digits = len(shown.split(".")[1]) if "." in shown else 0
        if not isinstance(value, (int, float)) or round(float(value), digits) != round(float(shown), digits):
            problems.append(f"{name}: metric {path} shows {shown}, latest.json has {value}")
    return problems


def check_landing(text: str, name: str) -> list[str]:
    problems = []
    in_fence = False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence or not RESULT_WORDS.search(line) or not NUMBER.search(line):
            continue
        if METRIC.search(line) or EXEMPT.search(line):
            continue
        src = SOURCE.search(line)
        if src:
            if not (ROOT / src.group(1)).exists():
                problems.append(f"{name}:{n}: source {src.group(1)} does not exist")
            continue
        problems.append(f"{name}:{n}: a measured number without a metric/source annotation or UNKNOWN label")
    return problems


def check_generated(latest: dict[str, Any]) -> list[str]:
    from ceap.evaluation_report import render_markdown

    text = EVAL_PAGE.read_text(encoding="utf-8")
    m = re.search(r"<!-- evaluation-report:begin -->\n(.*?)\n<!-- evaluation-report:end -->", text, re.S)
    if m is None:
        return ["docs/evaluation/evaluation.md: evaluation-report block missing"]
    if m.group(1).strip() != render_markdown(latest).strip():
        return [
            "docs/evaluation/evaluation.md: evaluation-report block differs from latest.json (regenerate it)"
        ]
    return []


def documented_commands(text: str) -> list[str]:
    out, lang = [], None
    for line in text.splitlines():
        f = FENCE.match(line.strip())
        if f:
            lang = None if lang is not None else (f.group(1) or "text")
            continue
        if lang in ("bash", "sh", "shell", "console", "text") and line.strip().startswith("ceap "):
            out.append(line.strip().split(" #")[0].strip())
    return out


def check_commands(files: list[Path]) -> list[str]:
    from ceap.cli import build_parser

    parser = build_parser()
    problems = []
    for path in files:
        for cmd in documented_commands(path.read_text(encoding="utf-8")):
            argv = [a for a in shlex.split(cmd.replace("\\", ""))[1:] if a not in ("|", ">")]
            try:
                parser.parse_args(argv)
            except SystemExit:
                problems.append(f"{path.relative_to(ROOT)}: documented command does not parse: {cmd}")
    return problems


def main() -> int:
    latest = json.loads(LATEST.read_text(encoding="utf-8"))
    problems: list[str] = []
    md_files = sorted({*(ROOT / p for p in LANDING), *(ROOT / "docs").rglob("*.md")})
    md_files = [p for p in md_files if p.exists() and "review" not in p.parts]
    for path in md_files:
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        problems += check_metrics(text, rel, latest)
        if rel in LANDING:
            problems += check_landing(text, rel)
    problems += check_generated(latest)
    problems += check_commands(md_files)
    for p in problems:
        print(p)
    print(f"check_docs: {len(problems)} problem(s) in {len(md_files)} files")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
