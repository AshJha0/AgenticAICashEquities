"""Eval harness behaviour in the unit tier: B3 sensitivity, T5 audit replay, R-01 refusal (0.10.0 E1)."""

from __future__ import annotations

import pytest

from ceap.eval import local_model as lm
from ceap.eval.stats import rate, wilson


def test_wilson_rejects_impossible_counts_and_bounds_the_edges() -> None:
    with pytest.raises(ValueError, match="outside"):
        wilson(3, 2)
    with pytest.raises(ValueError, match="outside"):
        wilson(-1, 2)
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and 0.70 < lo < 0.75
    assert rate(0, 0) == {"k": 0, "n": 0, "rate": None, "ci95": [0.0, 1.0]}


class _Client:
    def __init__(self, misses: int = 0) -> None:
        self.misses = misses


def test_refuse_fallback_names_misses_and_fallbacks() -> None:
    lm.refuse_fallback({"fallbacks": 0}, _Client())
    with pytest.raises(lm.EvaluationFallbackError, match="2 cassette miss"):
        lm.refuse_fallback({"fallbacks": 0}, _Client(misses=2))
    with pytest.raises(lm.EvaluationFallbackError, match="1 fallback answer"):
        lm.refuse_fallback({"fallbacks": 1}, object())


def test_cassette_path_slugs_the_model_name() -> None:
    assert lm.slug("qwen2.5:7b/instruct") == "qwen2.5_7b_instruct"
    assert lm.cassette_path("a:b", 3, "judge").name == "a_b_judge_s3.json"


@pytest.mark.asyncio
async def test_claims_audit_replay_pools_the_recorded_seed() -> None:
    from ceap.eval.local_model import PRIMARY_LOCAL_MODEL

    out = await lm.claims_audit_replay(PRIMARY_LOCAL_MODEL, (1,))
    assert out["evidence_class"] == "local_model" and out["seeds"] == [1]
    pooled = out["pooled"]["claims_kept_by_audit"]
    seed = out["per_seed"]["1"]["claims_kept_by_audit"]
    assert (pooled["k"], pooled["n"]) == (seed["k"], seed["n"])
    assert pooled["per_seed"] == {"1": seed["rate"]}
    assert out["pooled"]["drop_reasons"] == dict(sorted(out["per_seed"]["1"]["drop_reasons"].items()))


@pytest.mark.asyncio
async def test_every_metric_moves_under_its_degraded_client() -> None:
    from ceap.eval.agents import SENSITIVITY, sensitivity

    out = await sensitivity()
    assert out["evidence_class"] == "mock"
    assert [r["metric"] for r in out["rows"]] == [s[0] for s in SENSITIVITY]
    assert out["all_moved"] is True, [r for r in out["rows"] if not r["moved"]]
    for r in out["rows"]:
        if r["direction"] == "down":
            assert r["degraded"] < r["baseline"]
        else:
            assert r["degraded"] > r["baseline"]
