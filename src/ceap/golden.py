"""Golden set governance (PLAN 3E.1).

``tests/golden/golden_set.json`` holds 30 investigation and 15 research questions with expected
findings written against the scenario specifications:

* investigation: the acceptable primary causes, the evidence that must be cited (by tool), and the
  causes that must not be claimed as material;
* research: the expected verdict, the flags that must be raised and the flags that must not.

The file is frozen: ``version`` plus ``items_sha256`` (sha256 of the canonical JSON of ``items``).
Any edit changes the hash and fails ``tests/golden/test_golden_set.py`` until the version is bumped
and the hash updated, and CI requires the ``golden-change`` label on such a PR. The owner signs off
the ``signoff_sample`` items in that PR (recorded in ``signoff``).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_SET_PATH = REPO_ROOT / "tests" / "golden" / "golden_set.json"


def items_sha256(items: list[dict[str, Any]]) -> str:
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_golden_set(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or GOLDEN_SET_PATH).read_text(encoding="utf-8"))


def golden_set_problems(doc: dict[str, Any]) -> list[str]:
    """Governance check: frozen hash, counts, ids unique, sign-off sample drawn from the set."""
    problems: list[str] = []
    items = doc.get("items") or []
    if items_sha256(items) != doc.get("items_sha256"):
        problems.append("golden set items changed without a version bump and a new items_sha256")
    kinds = [i.get("kind") for i in items]
    if kinds.count("investigation") != 30 or kinds.count("research") != 15:
        problems.append(
            f"expected 30 investigation and 15 research items, found {kinds.count('investigation')}/{kinds.count('research')}"
        )
    ids = [i.get("id") for i in items]
    if len(set(ids)) != len(ids):
        problems.append("duplicate golden item ids")
    sample = doc.get("signoff_sample") or []
    if len(sample) != 10 or not set(sample) <= set(ids):
        problems.append("signoff_sample must name 10 items of the set")
    return problems


def score_investigation(item: dict[str, Any], result: Any) -> dict[str, Any]:
    report = result.report
    att = report.attribution if report else {}
    material = {r["cause"] for r in att.get("ranked", []) if r.get("score", 0) >= 0.35}
    cited_tools = {
        str(e.attributes.get("tool_id"))
        for e in (report.evidence if report else ())
        if e.attributes.get("tool_id")
    }
    must_mention = set(item["must_mention_tools"])
    must_not = set(item["must_not_claim"])
    return {
        "id": item["id"],
        "primary_ok": att.get("primary") in item["primary"],
        "mentions_ok": must_mention <= cited_tools,
        "missing_mentions": sorted(must_mention - cited_tools),
        "no_forbidden_claim": not (material & must_not),
        "forbidden_claims": sorted(material & must_not),
    }


def score_research(item: dict[str, Any], result: Any) -> dict[str, Any]:
    proposal = result.report.proposal if result.report else {}
    flags = set(proposal.get("flags") or [])
    return {
        "id": item["id"],
        "verdict_ok": proposal.get("verdict") == item["verdict"],
        "flags_ok": set(item["must_flag"]) <= flags,
        "no_forbidden_flag": not (flags & set(item["must_not_flag"])),
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    inv = [r for r in rows if "primary_ok" in r]
    res = [r for r in rows if "verdict_ok" in r]

    def rate(xs: list[dict[str, Any]], key: str) -> float | None:
        return round(sum(bool(x[key]) for x in xs) / len(xs), 4) if xs else None

    return {
        "investigation_items": len(inv),
        "research_items": len(res),
        "primary_cause_accuracy": rate(inv, "primary_ok"),
        "must_mention_rate": rate(inv, "mentions_ok"),
        "no_forbidden_claim_rate": rate(inv, "no_forbidden_claim"),
        "verdict_accuracy": rate(res, "verdict_ok"),
        "must_flag_rate": rate(res, "flags_ok"),
        "no_forbidden_flag_rate": rate(res, "no_forbidden_flag"),
    }
