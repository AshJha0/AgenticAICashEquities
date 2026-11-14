"""0.10.0 T5 (finding R-05): the claim audit binds each number to the claim's own fact keys.

The 0.9.0 audit tested set membership: a number passed if it equalled any fact under any key, so
a real figure attached to the wrong metric, a wrong direction word or a wrong cause all passed. One
test per acceptance item of T5 in docs/review/2026-11-12/PLAN_0.10.0.md.
"""

from __future__ import annotations

import json

import pytest

from ceap.agents.reporter import drop_reason_kind, validate_claims
from ceap.eval.agents import reporter_cases
from ceap.eval.clients import PLANTED_AUDIT_KINDS, planted_audit_claims


def _claim(text: str, keys: list[str]) -> dict:
    return {"section": "FINDINGS", "text": text, "fact_keys": keys, "evidence_ids": [],
            "needs_policy_support": False}


def _kinds(dropped: dict) -> set[str]:
    return {drop_reason_kind(r) for r in dropped["reasons"]}


# The four strings from the AI-3 reviewer report (AI-3-03), run on reporter_cases()[0]: facts
# is_bps 9.8, is_change_bps 6.2, volatility_ratio 2.3, primary cause MARKET_VOLATILITY. The 0.9.0
# audit kept every one of them.
AI3_090_EXAMPLES = [
    ("Implementation shortfall was 2.3 bps", ["metrics"], "wrong_metric"),
    ("Shortfall improved by 6.2 bps versus baseline", ["metrics.is_change_bps"], "wrong_direction"),
    ("Volatility was 6.2 times the baseline", ["metrics"], "wrong_metric"),
    (
        "Implementation shortfall fell by 2.3 bps; primary cause is VENUE_DEGRADATION",
        ["metrics.is_change_bps"],
        "wrong_cause",
    ),
]


@pytest.mark.parametrize(("text", "keys", "kind"), AI3_090_EXAMPLES)
def test_ai3_090_examples_are_dropped_for_their_own_fault(text, keys, kind):
    facts = reporter_cases()[0]["facts"]
    kept, dropped = validate_claims([_claim(text, keys)], facts)
    assert kept == [] and len(dropped) == 1
    assert kind in _kinds(dropped[0]), dropped[0]["reasons"]


def test_planted_wrong_metric_sign_and_cause_claims_all_rejected():
    """Acceptance: the three planted kinds are rejected, 0 of N pass, each for its own reason."""
    expected = {"wrong_metric": "wrong_metric", "wrong_sign": "wrong_direction", "wrong_cause": "wrong_cause"}
    seen = dict.fromkeys(PLANTED_AUDIT_KINDS, 0)
    passed = []
    for case in reporter_cases():
        for kind, claim in planted_audit_claims(case["facts"]):
            kept, dropped = validate_claims([claim], case["facts"])
            seen[kind] += 1
            passed += kept
            if dropped:
                assert expected[kind] in _kinds(dropped[0]), (case["id"], dropped[0]["reasons"])
    assert passed == []  # 0 of N pass
    assert seen == {"wrong_metric": 4, "wrong_sign": 4, "wrong_cause": 4}


def test_honest_counterparts_of_the_planted_claims_are_kept():
    """The audit is not a blanket drop: the right metric, sign and cause still pass."""
    facts = reporter_cases()[0]["facts"]
    honest = [
        _claim("Volatility was 2.3 times the baseline.", ["metrics"]),
        _claim("Implementation shortfall rose by 6.2 bps.", ["metrics.is_change_bps"]),
        _claim("Implementation shortfall worsened by 6.2 bps.", ["metrics.is_change_bps"]),
        _claim("The primary cause was MARKET_VOLATILITY.", ["attribution.primary"]),
        # a claim that repeats a finding statement binds to the figure quoted in it
        _claim(facts["findings"][0]["statement"], ["findings"]),
    ]
    kept, dropped = validate_claims(honest, facts)
    assert dropped == [] and len(kept) == len(honest)


def test_a_number_must_come_from_the_claims_own_fact_keys():
    """A real figure cited under the wrong key is not traceable (0.9.0 searched every fact)."""
    facts = reporter_cases()[0]["facts"]
    kept, dropped = validate_claims([_claim("The window ratio was 2.3.", ["metrics.is_bps"])], facts)
    assert kept == [] and _kinds(dropped[0]) == {"number_not_traceable"}


def test_metrics_are_renamed_to_what_they_check():
    from ceap.eval.local_model import RATE_PATHS

    assert "reporter.claims_kept_by_audit" in RATE_PATHS and "claims.number_not_in_own_facts" in RATE_PATHS
    assert "reporter.faithfulness" not in RATE_PATHS and "claims.untraceable_number" not in RATE_PATHS


async def test_published_claims_audit_reproduces_from_the_recorded_cassettes():
    """Drop reasons are published, and the published file is what a replay of the cassettes gives."""
    from ceap.eval.local_model import RESULTS, cassette_path, claims_audit_replay, slug
    from ceap.llm.ollama import PRIMARY_LOCAL_MODEL

    if not cassette_path(PRIMARY_LOCAL_MODEL, 1).exists():
        pytest.skip("cassette not recorded")
    path = RESULTS / f"{slug(PRIMARY_LOCAL_MODEL)}_claims_audit.json"
    published = json.loads(path.read_text(encoding="utf-8"))
    replayed = json.loads(json.dumps(await claims_audit_replay(PRIMARY_LOCAL_MODEL, (1, 2, 3))))
    assert replayed == published
    reasons = published["pooled"]["drop_reasons"]
    assert sum(reasons.values()) > 0 and "other" not in reasons
    for seed in published["per_seed"].values():
        assert all(r["k"] == r["n"] for r in seed["planted_rejected"].values())
