"""Pre-registered hypotheses and their deterministic tests (PLAN 3B.2, 3B.3).

The model *proposes and ranks* hypotheses (a ranked list of names from :data:`HYPOTHESES`); it never
produces a verdict. Every verdict here comes from a fixed, pre-registered test over numbers the tools
and agents computed:

* :func:`evaluate_hypotheses` marks each hypothesis ``SUPPORTED``, ``REFUTED`` or ``UNTESTED``;
* :func:`run_alternative_check` re-measures one finding against an *alternative* baseline window and
  says whether the result contradicts the finding (the only way the critic can lower a confidence).

Nothing in this module reads model text.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ceap.analytics.attribution import MATERIAL_SCORE, Cause
from ceap.domain.findings import Finding, HypothesisRow, HypothesisTable


class Verdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    UNTESTED = "UNTESTED"


#: the pre-registered hypothesis names the planner may propose (one per attribution cause)
HYPOTHESES: tuple[str, ...] = tuple(c.value for c in Cause if c is not Cause.NORMAL)
TEST_ID = f"attribution_score>={MATERIAL_SCORE}"
CORRECTION = "none: fixed pre-registered thresholds, no p-values"


@dataclass(frozen=True)
class HypothesisVerdict:
    name: str
    verdict: Verdict
    statistic: float
    evidence_ids: tuple[str, ...]
    rank: int | None
    reason: str

    def row(self) -> HypothesisRow:
        return HypothesisRow(
            name=self.name,
            statistic=self.statistic,
            verdict=self.verdict.value,
            test=TEST_ID,
            evidence_ids=self.evidence_ids,
            rank=self.rank,
            reason=self.reason,
        )


def rank_proposal(proposed: Iterable[Any]) -> tuple[list[str], list[str]]:
    """(ranked known names without duplicates, rejected names). Unknown names never become rows."""
    ranked: list[str] = []
    rejected: list[str] = []
    for name in proposed:
        text = str(name)
        if text not in HYPOTHESES:
            rejected.append(text[:80])
        elif text not in ranked:
            ranked.append(text)
    return ranked, rejected


def evaluate_hypotheses(
    proposed: Sequence[str],
    attribution: Mapping[str, Any] | None,
    findings: Sequence[Finding],
    attribution_evidence_id: str | None = None,
) -> HypothesisTable:
    """Test every pre-registered hypothesis; the planner's proposal only sets the order and the rank.

    Rules (pre-registered, deterministic):

    * no attribution (quant did not run, or no executions) -> every hypothesis ``UNTESTED``;
    * the rules do not depend on the deterioration flag; a note says when IS did not deteriorate;
    * a cause without a score (its input data were missing) -> ``UNTESTED``;
    * score >= ``MATERIAL_SCORE`` -> ``SUPPORTED``, otherwise ``REFUTED``.

    Evidence for a hypothesis is the supporting evidence of the findings that assert that anomaly,
    plus the attribution record, so two causes in one run (T10) carry separate evidence.
    """
    ranked, _ = rank_proposal(proposed)
    order = ranked + [h for h in HYPOTHESES if h not in ranked]
    attribution = attribution or {}
    scores: dict[str, float] = {}
    for item in attribution.get("ranked") or ():
        if isinstance(item, Mapping) and item.get("cause") in HYPOTHESES:
            try:
                scores[str(item["cause"])] = float(item.get("score"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
    deteriorated = bool(attribution.get("deteriorated"))
    verdicts: list[HypothesisVerdict] = []
    for name in order:
        evidence = _evidence_for(name, findings, attribution_evidence_id)
        rank = ranked.index(name) + 1 if name in ranked else None
        score = scores.get(name)
        note = "" if deteriorated else "; execution quality did not deteriorate past the threshold"
        if not attribution.get("ranked"):
            v, reason, stat = (
                Verdict.UNTESTED,
                "no attribution available (quant did not run or no executions)",
                math.nan,
            )
        elif score is None or not math.isfinite(score):
            v, reason, stat = Verdict.UNTESTED, "no score: the test's input data were not available", math.nan
        elif score >= MATERIAL_SCORE:
            v, reason, stat = Verdict.SUPPORTED, f"attribution score {score:.2f} >= {MATERIAL_SCORE}{note}", score
        else:
            v, reason, stat = Verdict.REFUTED, f"attribution score {score:.2f} < {MATERIAL_SCORE}{note}", score
        verdicts.append(
            HypothesisVerdict(name, v, stat, evidence if v is not Verdict.UNTESTED else (), rank, reason)
        )
    return HypothesisTable(
        rows=tuple(v.row() for v in verdicts), trials_counted=len(verdicts), correction=CORRECTION
    )


def _evidence_for(
    name: str, findings: Sequence[Finding], attribution_evidence_id: str | None
) -> tuple[str, ...]:
    out: list[str] = []
    for f in findings:
        if f.attributes.get("anomaly") == name and f.attributes.get("role") != "rejected":
            out.extend(e for e in f.supporting_evidence if e not in out)
    if attribution_evidence_id and attribution_evidence_id not in out:
        out.append(attribution_evidence_id)
    return tuple(out)


def table_groups(table: HypothesisTable | None) -> dict[str, list[dict[str, Any]]]:
    """The three report groups, in the planner's rank order within each group."""
    groups: dict[str, list[dict[str, Any]]] = {v.value: [] for v in Verdict}
    for r in table.rows if table else ():
        groups.setdefault(r.verdict, []).append(
            {
                "name": r.name,
                "statistic": None if not math.isfinite(r.statistic) else round(r.statistic, 4),
                "rank": r.rank,
                "evidence_ids": list(r.evidence_ids),
                "reason": r.reason,
                "test": r.test,
            }
        )
    return groups


def hypothesis_table_dict(table: HypothesisTable | None) -> dict[str, Any] | None:
    if table is None:
        return None
    return {
        "trials_counted": table.trials_counted,
        "correction": table.correction,
        "rows": [
            {
                "name": r.name,
                "statistic": None if not math.isfinite(r.statistic) else r.statistic,
                "verdict": r.verdict,
                "test": r.test,
                "evidence_ids": list(r.evidence_ids),
                "rank": r.rank,
                "reason": r.reason,
            }
            for r in table.rows
        ],
    }


# ----------------------------------------------------------------- baselines (3B.1) and checks (3B.3)

#: the enumerated baseline choices a plan may make; each distinct choice is a trial in the ledger
BASELINE_CHOICES: tuple[str, ...] = ("task_baseline", "prior_equal_length", "prior_double_length")
DEFAULT_BASELINE = "task_baseline"


def baseline_window(choice: str, task_input: Mapping[str, Any]) -> tuple[str, str]:
    """The (start, end) of an enumerated baseline. Raises ``ValueError`` for a choice outside the set."""
    if choice not in BASELINE_CHOICES:
        raise ValueError(f"baseline {choice!r} is not one of {list(BASELINE_CHOICES)}")
    if choice == "task_baseline":
        return str(task_input["baseline_start"]), str(task_input["baseline_end"])
    start = datetime.fromisoformat(str(task_input["window_start"]))
    end = datetime.fromisoformat(str(task_input["window_end"]))
    length = (end - start) * (1 if choice == "prior_equal_length" else 2)
    return _iso(start - length, task_input["window_start"]), _iso(start, task_input["window_start"])


def _iso(value: datetime, like: Any) -> str:
    text = value.isoformat()
    return text if "T" in str(like) or ":" in str(like) else text[:10]


#: alternative checks the critic may name (3B.3); each maps to an alternative baseline
ALTERNATIVE_CHECKS: dict[str, str | None] = {
    "alternative_baseline": "prior_equal_length",
    "different_horizon": "prior_double_length",
    "peer_set": None,  # investigations carry one symbol: no peer set exists, so the check is unavailable
}


@dataclass(frozen=True)
class RatioCheck:
    """How to re-measure one window/baseline ratio a finding cites."""

    tool_id: str
    field: str
    threshold: float
    anomaly_when_above: bool  # True: ratio >= threshold is the anomaly; False: ratio <= threshold

    def anomalous(self, value: float) -> bool:
        return value >= self.threshold if self.anomaly_when_above else value <= self.threshold


#: finding attribute (the cited evidence path) -> how to recompute it against another baseline
RATIO_CHECKS: dict[str, RatioCheck] = {
    "volatility_ratio": RatioCheck("market_data.get_market_statistics", "realised_volatility_bps", 1.6, True),
    "spread_ratio": RatioCheck("market_data.get_market_statistics", "average_spread_bps", 1.4, True),
    "depth_ratio": RatioCheck(
        "market_data.get_order_book_statistics", "average_displayed_depth", 0.65, False
    ),
}


@dataclass(frozen=True)
class CheckOutcome:
    finding_id: str
    check: str
    status: str  # CONTRADICTS | CONSISTENT | UNAVAILABLE
    path: str | None = None
    cited_value: float | None = None
    alternative_value: float | None = None
    baseline: tuple[str, str] | None = None
    reason: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def contradicts(self) -> bool:
        return self.status == "CONTRADICTS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "check": self.check,
            "status": self.status,
            "path": self.path,
            "cited_value": self.cited_value,
            "alternative_value": self.alternative_value,
            "baseline": list(self.baseline) if self.baseline else None,
            "reason": self.reason,
        }


def check_plan(
    finding: Finding, check: str, task_input: Mapping[str, Any]
) -> tuple[RatioCheck, str, tuple[str, str]] | str:
    """What a named check needs (ratio spec, cited path, baseline window), or why it cannot run."""
    if check not in ALTERNATIVE_CHECKS:
        return f"unknown check {check!r}"
    choice = ALTERNATIVE_CHECKS[check]
    if choice is None:
        return f"check {check} is not available for single-symbol investigations"
    refs = [r for r in finding.evidence_refs if r.path in RATIO_CHECKS]
    if not refs:
        return "the finding cites no re-measurable ratio (volatility, spread or depth)"
    path = refs[0].path
    return RATIO_CHECKS[path], path, baseline_window(choice, task_input)


def judge_alternative(
    finding: Finding,
    check: str,
    spec: RatioCheck,
    path: str,
    window_value: Any,
    alternative_baseline_value: Any,
    baseline: tuple[str, str],
) -> CheckOutcome:
    """Contradiction rule: the finding asserts an anomaly on ``path`` (its cited ratio is past the
    threshold) but the same ratio against the alternative baseline is not; or the finding says the
    metric was normal and the alternative ratio is past the threshold."""
    cited = next((r.value for r in finding.evidence_refs if r.path == path), None)
    try:
        cited_f = float(cited)  # type: ignore[arg-type]
        alt = float(window_value) / float(alternative_baseline_value)
    except (TypeError, ValueError, ZeroDivisionError):
        return CheckOutcome(
            finding.id, check, "UNAVAILABLE", path, reason="alternative ratio is not computable"
        )
    if not (math.isfinite(cited_f) and math.isfinite(alt)):
        return CheckOutcome(finding.id, check, "UNAVAILABLE", path, reason="alternative ratio is not finite")
    contradicts = spec.anomalous(cited_f) != spec.anomalous(alt)
    return CheckOutcome(
        finding.id,
        check,
        "CONTRADICTS" if contradicts else "CONSISTENT",
        path,
        round(cited_f, 6),
        round(alt, 6),
        baseline,
        reason=(
            f"{path} is {cited_f:.3f} against the task baseline and {alt:.3f} against the {check} baseline "
            f"(threshold {spec.threshold})"
        ),
    )
