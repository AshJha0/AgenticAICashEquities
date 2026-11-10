"""Local LLM-as-judge for the pinned ``judge`` purpose, with a calibration gate (0.9.0 B5).

Plain idea: a second model reads a report narrative next to the structured facts it was written
from and answers three yes/no questions. Before anyone may use those answers, they are compared with
50 reference labels; a question whose agreement with the labels is not clearly better than chance
stays switched off.

Formal statement.

* Rubric (:data:`RUBRIC`), each a boolean:
  - ``faithful``: every number and causal statement in the narrative matches the facts;
  - ``complete``: the narrative states the implementation shortfall figure *and* the primary cause;
  - ``hedged``: confidence language matches the evidence (no claim of certainty or proof).
* Reference set (``judge_reference_v1.json``, :func:`build_reference`): 10 fact sets x 5 variants
  (clean; one number changed; primary cause omitted; certainty overclaimed; number changed *and*
  overclaimed) = 50 items. The labels were assigned by the author when each variant was written, one
  label per dimension, and are fixed in the file with its sha256; they are hand-built labels, not
  independent annotator labels, which is the main limit of this calibration.
* Gate: Cohen's kappa ``k = (p_o - p_e) / (1 - p_e)`` between judge and reference, per dimension.
  A dimension is enabled only when ``k > 0.6`` (bar fixed before the run). The verdict is written to
  ``docs/evaluation/local_model/judge_calibration.json``; :func:`enabled_dimensions` reads it, and
  returns nothing when the file is missing.

Worked example: on 50 items with 30 reference "yes", if the judge agrees on 44 (p_o = 0.88) and
says "yes" 32 times, p_e = 0.6*0.64 + 0.4*0.36 = 0.528 and k = (0.88 - 0.528)/(0.472) = 0.746 > 0.6:
the dimension is enabled.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ceap.llm.client import LLMClient
from ceap.llm.models import LLMRequest
from ceap.llm.prompts import DATA_BLOCK_NOTE, data_block

REFERENCE_PATH = Path(__file__).with_name("judge_reference_v1.json")
CALIBRATION_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "evaluation" / "local_model" / "judge_calibration.json"
)
DIMENSIONS = ("faithful", "complete", "hedged")
KAPPA_BAR = 0.6

RUBRIC = """You are an auditor of execution-quality reports. You receive FACTS (structured, trusted) and a
NARRATIVE written from them. Answer three yes/no questions about the NARRATIVE only.

faithful: true if every number and every causal statement in the narrative matches the facts;
false if any number differs from the facts or a cause is stated that the facts do not give.
complete: true if the narrative states BOTH the implementation shortfall figure AND the primary cause
from the facts; false if either is missing.
hedged: true if the confidence language fits the evidence (words like "likely", "consistent with",
or a stated confidence); false if it claims certainty, proof, or that no other explanation exists.

Return JSON with keys faithful, complete, hedged (booleans) and reason (one short sentence)."""


class JudgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    faithful: bool
    complete: bool
    hedged: bool
    reason: str


def judge_request(facts: dict[str, Any], narrative: str) -> LLMRequest:
    return LLMRequest(
        system_prompt=RUBRIC,
        messages=[
            {
                "role": "user",
                # the narrative is model-written and so untrusted; both go in nonce-delimited data blocks
                "content": f"{DATA_BLOCK_NOTE}\nFACTS:\n"
                + data_block("evidence", "facts", facts)
                + "\n\nNARRATIVE:\n"
                + data_block("evidence", "narrative", narrative),
            }
        ],
        purpose="judge",
        metadata={
            "prompt": {
                "id": "judge_rubric",
                "version": 1,
                "sha256": hashlib.sha256(RUBRIC.encode()).hexdigest(),
            }
        },
        output_model=JudgeOutput,
    )


# ------------------------------------------------------------------------------------ reference set

_FACTS = [
    ("AAPL", 6.2, "MARKET_VOLATILITY", "volatility ratio 2.3x the 20-day norm", 0.70),
    ("MSFT", 11.8, "SPREAD_WIDENING", "quoted spread 3.1x its baseline", 0.65),
    ("NVDA", 18.4, "PARTICIPATION_TOO_HIGH", "participation 24% against a 10% target", 0.75),
    ("AMZN", 4.1, "ADVERSE_SELECTION", "post-fill markouts of -3.2 bps at 60 s", 0.60),
    ("JPM", 9.7, "LATE_START", "arrival 41 minutes after the order was released", 0.70),
    ("XOM", 7.3, "VENUE_TOXICITY", "dark fills with -4.8 bps markouts on one venue", 0.55),
    ("TSLA", 23.6, "MARKET_VOLATILITY", "volatility ratio 3.4x the 20-day norm", 0.80),
    ("META", 5.5, "LIQUIDITY_SHORTFALL", "displayed depth 45% below its 20-day median", 0.60),
    ("KO", 3.2, "SPREAD_WIDENING", "quoted spread 2.2x its baseline", 0.65),
    ("BAC", 13.9, "PARTICIPATION_TOO_HIGH", "participation 31% against a 12% target", 0.70),
]
_CAUSE_TEXT = {
    "MARKET_VOLATILITY": "elevated market volatility",
    "SPREAD_WIDENING": "spread widening",
    "PARTICIPATION_TOO_HIGH": "participation above target",
    "ADVERSE_SELECTION": "adverse selection",
    "LATE_START": "a late start",
    "VENUE_TOXICITY": "toxic venue flow",
    "LIQUIDITY_SHORTFALL": "thin displayed liquidity",
}


def _variants(
    sym: str, bps: float, cause: str, ev: str, conf: float
) -> list[tuple[str, str, dict[str, bool]]]:
    c = _CAUSE_TEXT[cause]
    wrong = round(bps * 2 + 1.5, 1)
    clean = (
        f"{sym} implementation shortfall was {bps} bps. The evidence ({ev}) is consistent with {c} as the "
        f"likely primary cause, at confidence {conf:.2f}; other contributors cannot be ruled out."
    )
    unfaithful = clean.replace(f"{bps} bps", f"{wrong} bps")
    incomplete = (
        f"{sym} implementation shortfall was {bps} bps. Several market conditions in the window are "
        f"worth reviewing; confidence in any single driver is {conf:.2f}."
    )
    overclaim = (
        f"{sym} implementation shortfall was {bps} bps. The evidence ({ev}) proves beyond doubt that {c} "
        f"caused it; no other explanation is possible."
    )
    both = overclaim.replace(f"{bps} bps", f"{wrong} bps")
    return [
        ("clean", clean, {"faithful": True, "complete": True, "hedged": True}),
        ("unfaithful", unfaithful, {"faithful": False, "complete": True, "hedged": True}),
        ("incomplete", incomplete, {"faithful": True, "complete": False, "hedged": True}),
        ("overclaim", overclaim, {"faithful": True, "complete": True, "hedged": False}),
        ("unfaithful_overclaim", both, {"faithful": False, "complete": True, "hedged": False}),
    ]


def build_reference() -> dict[str, Any]:
    items = []
    for sym, bps, cause, ev, conf in _FACTS:
        facts = {
            "symbol": sym,
            "implementation_shortfall_bps": bps,
            "primary_cause": cause,
            "key_evidence": ev,
            "confidence": conf,
        }
        for kind, text, labels in _variants(sym, bps, cause, ev, conf):
            items.append(
                {
                    "id": f"J-{sym}-{kind}",
                    "variant": kind,
                    "facts": facts,
                    "narrative": text,
                    "labels": labels,
                }
            )
    return {
        "version": "judge_reference_v1",
        "labels_source": "hand_built: assigned by the author per variant when written (CEAP 0.9.0 B5)",
        "sha256": hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest(),
        "items": items,
    }


def load_reference(path: Path = REFERENCE_PATH) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return doc


# ------------------------------------------------------------------------------------ calibration


def cohen_kappa(a: list[bool], b: list[bool]) -> float | None:
    n = len(a)
    if n == 0 or n != len(b):
        return None
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else round((po - pe) / (1 - pe), 4)


async def judge(client: LLMClient, facts: dict[str, Any], narrative: str) -> dict[str, Any] | None:
    from ceap.eval.agents import _reply

    raw = _reply(await client.complete(judge_request(facts, narrative)))
    try:
        return JudgeOutput.model_validate(raw).model_dump() if raw is not None else None
    except Exception:  # noqa: BLE001 - an unusable verdict is scored as missing
        return None


async def calibrate(client: LLMClient, reference: dict[str, Any] | None = None) -> dict[str, Any]:
    from ceap.eval.clients import evidence_class

    reference = reference or load_reference()
    rows = []
    for item in reference["items"]:
        verdict = await judge(client, item["facts"], item["narrative"])
        rows.append({"id": item["id"], "labels": item["labels"], "verdict": verdict})
    usable = [r for r in rows if r["verdict"] is not None]
    dims: dict[str, Any] = {}
    for d in DIMENSIONS:
        # an unusable verdict counts as a disagreement: score it as the opposite of the label
        ref = [r["labels"][d] for r in rows]
        got = [r["verdict"][d] if r["verdict"] is not None else not r["labels"][d] for r in rows]
        k = cohen_kappa(got, ref)
        dims[d] = {
            "kappa": k,
            "agreement": round(sum(x == y for x, y in zip(got, ref, strict=True)) / len(rows), 4),
            "enabled": k is not None and k > KAPPA_BAR,
        }
    return {
        "evidence_class": evidence_class(client),
        "model": getattr(client, "model", None),
        "reference": reference["version"],
        "reference_sha256": reference["sha256"],
        "items": len(rows),
        "usable": len(usable),
        "kappa_bar": KAPPA_BAR,
        "dimensions": dims,
        "enabled": [d for d in DIMENSIONS if dims[d]["enabled"]],
        "rows": rows,
    }


def enabled_dimensions(path: Path = CALIBRATION_PATH) -> list[str]:
    """The judge dimensions that passed calibration (empty when not calibrated)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [
        d
        for d in doc.get("enabled", [])
        if d in DIMENSIONS and (doc["dimensions"][d]["kappa"] or 0) > KAPPA_BAR
    ]


def main(argv: list[str] | None = None) -> None:
    from ceap.eval.local_model import _write, cassette_path
    from ceap.llm.ollama import (
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

    ap = argparse.ArgumentParser(description="B5 judge calibration")
    ap.add_argument("--model", default=PRIMARY_LOCAL_MODEL)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--write-reference", action="store_true")
    ap.add_argument("--replay", action="store_true")
    a = ap.parse_args(argv)
    if a.write_reference:
        REFERENCE_PATH.write_text(json.dumps(build_reference(), indent=1) + "\n", encoding="utf-8")
        return
    path = cassette_path(a.model, a.seed, "judge")
    if a.replay:
        client: Any = LocalCassetteClient(load_local_cassette(path))
    else:
        if not live_enabled():
            raise SystemExit("set CEAP_LOCAL_MODEL=1 to call the local model")
        # the judge runs at temperature 0: a grader should be as repeatable as the build allows
        cas = LocalCassette(
            provenance=LocalProvenance(
                model=a.model, digest=model_digest(a.model), seed=a.seed, temperature=0.0
            )
        )
        client = OllamaLLMClient(a.model, seed=a.seed, temperature=0.0, recorder=cas, checkpoint=path)
    doc = asyncio.run(calibrate(client))
    if not a.replay:
        write_local_cassette(path, cas)
        _write(CALIBRATION_PATH, doc)
    print(json.dumps({k: v for k, v in doc.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
