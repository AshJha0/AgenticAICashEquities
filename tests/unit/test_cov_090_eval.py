"""Coverage for the 0.9.0 eval modules: judge calibration, challenger agreement, injection strata.

Fake clients only (no Ollama, no network); every number below is worked by hand in the comment.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ceap.eval import challenger, injection, judge
from ceap.llm.client import LLMClient
from ceap.llm.models import LLMRequest, LLMResponse


class _Fixed(LLMClient):
    """Answers every request with the next parsed dict from ``replies`` (``None``: free text)."""

    model = "fake:1b"

    def __init__(self, replies: list[dict[str, Any] | None]) -> None:
        self.replies = list(replies)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        parsed = self.replies.pop(0)
        return LLMResponse(
            content="no json here" if parsed is None else json.dumps(parsed),
            tool_calls=[],
            input_tokens=1,
            output_tokens=1,
            model=self.model,
            parsed=parsed,
        )


# ------------------------------------------------------------------------------------ judge


def test_cohen_kappa_worked_example_and_degenerate_inputs():
    # po = 3/4; pa = 1/2, pb = 1/4; pe = 1/2*1/4 + 1/2*3/4 = 1/2; kappa = (3/4 - 1/2) / (1/2) = 0.5
    assert judge.cohen_kappa([True, True, False, False], [True, False, False, False]) == 0.5
    assert judge.cohen_kappa([], []) is None
    assert judge.cohen_kappa([True], [True, False]) is None
    # both raters constant and equal: chance agreement is 1, kappa undefined
    assert judge.cohen_kappa([True, True], [True, True]) is None
    # perfect disagreement on a balanced pair: po = 0, pe = 1/2, kappa = -1
    assert judge.cohen_kappa([True, False], [False, True]) == -1.0


async def test_judge_scores_an_invalid_verdict_as_missing():
    good = {"faithful": True, "complete": True, "hedged": False, "reason": "ok"}
    assert await judge.judge(_Fixed([good]), {"symbol": "AAPL"}, "n") == good
    assert await judge.judge(_Fixed([{"faithful": "maybe"}]), {"symbol": "AAPL"}, "n") is None
    assert await judge.judge(_Fixed([None]), {"symbol": "AAPL"}, "n") is None


async def test_calibrate_counts_unusable_verdict_as_disagreement():
    labels = [
        {"faithful": True, "complete": True, "hedged": False},
        {"faithful": False, "complete": True, "hedged": True},
        {"faithful": True, "complete": False, "hedged": False},
        {"faithful": False, "complete": False, "hedged": True},
    ]
    reference = {
        "version": "t",
        "sha256": "s",
        "items": [
            {"id": f"i{n}", "facts": {"symbol": "AAPL"}, "narrative": "n", "labels": lb}
            for n, lb in enumerate(labels)
        ],
    }
    # the judge copies the labels for items 0-2 and returns no usable verdict for item 3
    replies: list[dict[str, Any] | None] = [{**lb, "reason": "r"} for lb in labels[:3]] + [None]
    out = await judge.calibrate(_Fixed(replies), reference)
    assert (out["items"], out["usable"], out["model"]) == (4, 3, "fake:1b")
    # faithful: got [T,F,T,T] vs ref [T,F,T,F]: po = 3/4, pa = 3/4, pb = 1/2, pe = 1/2, kappa = 0.5
    f = out["dimensions"]["faithful"]
    assert (f["kappa"], f["agreement"], f["enabled"]) == (0.5, 0.75, False)
    assert out["enabled"] == []


def test_enabled_dimensions_reads_only_dimensions_over_the_bar(tmp_path):
    assert judge.enabled_dimensions(tmp_path / "missing.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert judge.enabled_dimensions(bad) == []
    doc = {
        "labels_by": "independent human raters",
        "enabled": ["faithful", "complete", "hedged", "made_up"],
        "dimensions": {"faithful": {"kappa": 0.8}, "complete": {"kappa": 0.6}, "hedged": {"kappa": None}},
    }
    p = tmp_path / "cal.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    # 0.6 is not strictly above the bar; None counts as 0; unknown names are dropped
    assert judge.enabled_dimensions(p) == ["faithful"]


def test_t10_model_written_labels_enable_nothing(tmp_path):
    """0.10.0 T10 (R-07): labels written by a model cannot validate the judge."""
    doc = {"labels_by": "Claude", "enabled": ["hedged"], "dimensions": {"hedged": {"kappa": 1.0}}}
    p = tmp_path / "cal.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert judge.enabled_dimensions(p) == []
    assert judge.enabled_dimensions() == []  # the committed file is labelled by Claude


# ------------------------------------------------------------------------------------ challenger


def _plan(*tools: str) -> dict[str, Any]:
    return {"steps": [{"type": "TOOL_CALL", "tool_id": t} for t in tools]}


def test_tool_sequence_ignores_malformed_steps():
    plan = {"steps": [{"type": "TOOL_CALL", "tool_id": "a"}, {"type": "NOTE"}, "junk", {"type": "TOOL_CALL"}]}
    assert challenger.tool_sequence(plan) == ["a"]
    assert challenger.tool_sequence({"steps": "nope"}) == []
    assert challenger.tool_sequence(None) == []


def test_approver_view_levels_and_order():
    # same set, reversed order: jaccard 1 (agree), LCS of [a,b,c] vs [c,b,a] is 1, order 1/3
    v = challenger.approver_challenger_view(_plan("a", "b", "c"), _plan("c", "b", "a"), "m")
    assert (v["level"], v["jaccard"], v["order_agreement"]) == ("agree", 1.0, 0.3333)
    # disjoint: jaccard 0
    assert challenger.approver_challenger_view(_plan("a"), _plan("b"))["level"] == "disagree"
    # both empty: defined as full agreement
    e = challenger.approver_challenger_view(_plan(), _plan())
    assert (e["jaccard"], e["order_agreement"], e["level"]) == (1.0, 1.0, "agree")
    # no challenger plan overrides the level whatever the score
    n = challenger.approver_challenger_view(_plan("a"), None)
    assert n["level"] == "no_challenger_plan" and n["only_primary"] == ["a"]


async def test_plan_agreement_eval_pools_cases(monkeypatch):
    import ceap.eval.agents as agents_mod

    cases = [{"id": "g1", "question": "q1"}, {"id": "g2", "question": "q2"}]
    monkeypatch.setattr(agents_mod, "load_gold_plans", lambda: {"cases": cases})
    plans = {
        ("P", "q1"): _plan("a", "b"),
        ("C", "q1"): _plan("a", "b"),  # agree: jaccard 1, order 1
        ("P", "q2"): _plan("a", "b"),
        ("C", "q2"): _plan("c"),  # disagree: jaccard 0, order 0
    }

    async def fake_reply(client: Any, question: str) -> dict[str, Any] | None:
        return plans[(client.model, question)]

    monkeypatch.setattr(injection, "_planner_reply", fake_reply)

    class M:
        def __init__(self, model: str) -> None:
            self.model = model

    out = await challenger.plan_agreement_eval(M("P"), M("C"))  # type: ignore[arg-type]
    assert (out["primary"], out["challenger"], out["cases"]) == ("P", "C", 2)
    assert out["agree"]["k"] == 1 and out["disagree"]["k"] == 1
    assert (out["mean_jaccard"], out["mean_order_agreement"]) == (0.5, 0.5)
    assert [r["id"] for r in out["rows"]] == ["g1", "g2"]


def test_challenger_main_live_needs_the_gate(monkeypatch, tmp_path):
    import ceap.eval.local_model as lm
    import ceap.llm.ollama as ol

    monkeypatch.setattr(lm, "cassette_path", lambda *a, **k: tmp_path / "x.json")
    ol.write_local_cassette(
        tmp_path / "x.json",
        ol.LocalCassette(
            provenance=ol.LocalProvenance(model=ol.PRIMARY_LOCAL_MODEL, seed=1, temperature=0.0)
        ),
    )
    monkeypatch.setenv(ol.LOCAL_MODEL_ENV, "0")
    with pytest.raises(SystemExit, match="CEAP_LOCAL_MODEL"):
        challenger.main(["--seed", "1"])


# ------------------------------------------------------------------------------------ injection


def test_stratified_none_returns_everything_and_n_caps_each_surface():
    attacks = injection.load_corpus()["attacks"]
    assert injection.stratified(attacks, None) is attacks
    two = injection.stratified(attacks, 2)
    surfaces = [a["surface"] for a in two]
    assert len(two) == 2 * len(injection.SURFACES)
    assert all(surfaces.count(s) == 2 for s in injection.SURFACES)


def test_obeyed_none_output_is_not_obeying():
    attack = injection.load_corpus()["attacks"][0]
    assert injection.obeyed(attack, None) is False
