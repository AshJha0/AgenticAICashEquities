"""A challenger model that plans the same task independently, and the agreement the approver sees (0.9.0 B7).

Plain idea: one model's plan is easier to trust when a different model, given the same request,
picks the same tools. Where they differ, the approver is shown exactly which tools only one of them
wanted, so disagreement becomes a review prompt instead of a silent risk.

Formal statement. For primary tool set ``A`` and challenger tool set ``B`` (TOOL_CALL steps only):

* ``jaccard = |A n B| / |A u B|`` (1.0 when both are empty);
* ``order = LCS(a, b) / max(len(a), len(b))`` over the two tool sequences in plan order;
* ``level``: ``agree`` when jaccard >= 0.8, ``partial`` when >= 0.5, else ``disagree``
  (thresholds fixed before any run).

:func:`approver_challenger_view` is the isolated hook the approver view calls; it takes two raw plan
dicts and returns the block to render. It has no dependency on the harness. The challenger is
``llama3.1:8b-instruct-q4_K_M`` (a different family from the ``qwen2.5`` primary), run through
:class:`ceap.llm.ollama.OllamaLLMClient`.

Worked example: primary ``[market.get_bars, tca.compute, venue.stats]``, challenger
``[market.get_bars, tca.compute, news.search]``: intersection 2, union 4, jaccard 0.5 (``partial``);
the view lists ``only_primary = [venue.stats]`` and ``only_challenger = [news.search]``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from ceap.eval.stats import rate
from ceap.llm.client import LLMClient

AGREE, PARTIAL = 0.8, 0.5


def tool_sequence(plan: dict[str, Any] | None) -> list[str]:
    steps = (plan or {}).get("steps")
    return [
        str(s.get("tool_id"))
        for s in (steps if isinstance(steps, list) else [])
        if isinstance(s, dict) and s.get("type") == "TOOL_CALL" and s.get("tool_id")
    ]


def _lcs(a: list[str], b: list[str]) -> int:
    dp = [0] * (len(b) + 1)
    for x in a:
        prev = 0
        for j, y in enumerate(b, 1):
            cur = dp[j]
            dp[j] = prev + 1 if x == y else max(dp[j], dp[j - 1])
            prev = cur
    return dp[-1]


def approver_challenger_view(
    primary: dict[str, Any] | None, challenger: dict[str, Any] | None, challenger_model: str = ""
) -> dict[str, Any]:
    """Agreement block for the approver: level, scores and the tools only one model chose."""
    a, b = tool_sequence(primary), tool_sequence(challenger)
    sa, sb = set(a), set(b)
    union = sa | sb
    jaccard = 1.0 if not union else len(sa & sb) / len(union)
    order = 1.0 if not (a or b) else _lcs(a, b) / max(len(a), len(b))
    level = "agree" if jaccard >= AGREE else "partial" if jaccard >= PARTIAL else "disagree"
    if challenger is None:
        level = "no_challenger_plan"
    return {
        "challenger_model": challenger_model,
        "level": level,
        "jaccard": round(jaccard, 4),
        "order_agreement": round(order, 4),
        "shared": sorted(sa & sb),
        "only_primary": sorted(sa - sb),
        "only_challenger": sorted(sb - sa),
        "note": "advisory: the challenger never changes the plan; disagreement asks the approver to look",
    }


async def plan_agreement_eval(primary: LLMClient, challenger: LLMClient) -> dict[str, Any]:
    """Both models plan every gold case; agreement per case and pooled."""
    from ceap.eval.agents import load_gold_plans
    from ceap.eval.injection import _planner_reply

    rows = []
    for case in load_gold_plans()["cases"]:
        p = await _planner_reply(primary, case["question"])
        c = await _planner_reply(challenger, case["question"])
        view = approver_challenger_view(p, c, getattr(challenger, "model", ""))
        rows.append({"id": case["id"], **view})
    n = len(rows)
    return {
        "primary": getattr(primary, "model", None),
        "challenger": getattr(challenger, "model", None),
        "cases": n,
        "agree": rate(sum(r["level"] == "agree" for r in rows), n),
        "disagree": rate(sum(r["level"] == "disagree" for r in rows), n),
        "mean_jaccard": round(sum(r["jaccard"] for r in rows) / n, 4) if n else None,
        "mean_order_agreement": round(sum(r["order_agreement"] for r in rows) / n, 4) if n else None,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> None:
    from ceap.eval.local_model import RESULTS, TEMPERATURE, _write, cassette_path
    from ceap.llm.ollama import (
        CHALLENGER_LOCAL_MODEL,
        PRIMARY_LOCAL_MODEL,
        LocalCassette,
        LocalCassetteClient,
        LocalProvenance,
        OllamaLLMClient,
        live_enabled,
        load_local_cassette,
        model_digest,
        write_local_cassette,
    )

    ap = argparse.ArgumentParser(description="B7 challenger plan agreement")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--replay", action="store_true")
    a = ap.parse_args(argv)
    # the primary's planner replies for these questions are in its eval cassette (same requests)
    prim_path = cassette_path(PRIMARY_LOCAL_MODEL, a.seed)
    chal_path = cassette_path(CHALLENGER_LOCAL_MODEL, a.seed, "challenger")
    primary: Any = LocalCassetteClient(load_local_cassette(prim_path))
    if a.replay:
        challenger: Any = LocalCassetteClient(load_local_cassette(chal_path))
    else:
        if not live_enabled():
            raise SystemExit("set CEAP_LOCAL_MODEL=1 to call the local model")
        cas = LocalCassette(
            provenance=LocalProvenance(
                model=CHALLENGER_LOCAL_MODEL,
                digest=model_digest(CHALLENGER_LOCAL_MODEL),
                seed=a.seed,
                temperature=TEMPERATURE,
            )
        )
        challenger = OllamaLLMClient(
            CHALLENGER_LOCAL_MODEL, seed=a.seed, temperature=TEMPERATURE, recorder=cas
        )
    doc = asyncio.run(plan_agreement_eval(primary, challenger))
    if not a.replay:
        write_local_cassette(chal_path, cas)
        _write(RESULTS / "challenger_agreement.json", doc)
    print(json.dumps({k: v for k, v in doc.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
