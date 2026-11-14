"""Per-agent evals (B2) and the metric sensitivity test (B3).

Only three agents call the model: the planner, the critic and the reporter. Each eval drives the
agent's own model interface with hand-built cases and scores the model's reply:

* **planner**  - runs :class:`PlannerAgent` on ten gold-plan questions and scores the tool ids in
  the model's *own* last planning reply (not the canonical fallback) for recall against the
  question's required tools and precision against the investigation scope; plus plan-valid and
  repair rates read from the agent's degradation record;
* **critic**   - sends findings with planted contradictions (uncited, unresolved, contradicted by
  evidence, numeric mismatch, direction mismatch) and clean controls in the critic's request
  format; detection = the model marks a planted finding unsupported or lowers its confidence;
* **reporter** - sends hand-built report facts in the reporter's claims format and validates the
  claims with :func:`validate_claims` (faithfulness = share kept, claim-drop = share dropped); then
  appends planted unfaithful claims and counts how many the guardrail drops; then sends the prose
  call with the policy documents and audits every citation span (citation-verified rate).

The cases are ``hand_built``; the model answers carry the client's evidence class (``mock`` until
B1 plugs in a local model). Every rate has a 95 % Wilson interval. With the mock, several rates are
1.0 by construction: the mock was written to pass these checks, so a mock 1.0 says the plumbing is
right, not that a model is good. The sensitivity test is what shows each metric can fall.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ceap.agents.reporter import (
    REPORTER_CLAIMS_SYSTEM_PROMPT,
    ClaimsOutput,
    audit_citations,
    prose_request,
    report_request_content,
    validate_claims,
)
from ceap.eval.clients import (
    CapturingClient,
    CitationForger,
    ClaimFabricator,
    ClaimInjector,
    InvalidPlanner,
    NarrowPlanner,
    RubberStampCritic,
    evidence_class,
)
from ceap.eval.stats import rate
from ceap.llm.client import LLMClient, MockLLMClient
from ceap.llm.models import LLMRequest
from ceap.llm.prompts import DATA_BLOCK_NOTE, data_block, fit_context, get_prompt
from ceap.llm.schemas import CritiqueOutput, critique_semantic_errors, parse_json_object, validate_model

GOLD_PLANS_PATH = Path(__file__).with_name("gold_plans.json")
CASES_SOURCE = "hand_built"
CRITIC_LEAD = "Assess these findings. To dispute one, name an alternative check.\n"
DETECTION_DROP = 0.05  # a confidence cut of more than this counts as a dispute


def load_gold_plans(path: Path = GOLD_PLANS_PATH) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return doc


def _reply(response: Any) -> dict[str, Any] | None:
    if response is None:
        return None
    if response.parsed is not None:
        return dict(response.parsed)
    raw, _ = parse_json_object(response.content)
    return raw


# ------------------------------------------------------------------------------------ planner


async def planner_eval(client: LLMClient, platform: Any = None) -> dict[str, Any]:
    from ceap.agents.planner import PlannerAgent
    from ceap.domain.agents import AgentContext
    from ceap.harness.memory import CaseMemory
    from ceap.platform import Platform, default_request

    platform = platform or Platform(llm=MockLLMClient(), case_memory=CaseMemory(enabled=False))
    base = await platform.harness()
    gold = load_gold_plans()
    allowed = set(gold["allowed"])
    rows = []
    for case in gold["cases"]:
        req = default_request("AAPL", "T01", question=case["question"])
        task = platform.build_task(req)
        ctx = AgentContext(
            task.id,
            "eval",
            task.created_at,
            dict(task.input),
            None,
            (),
            {"task": task, "tool_catalogue": base.tools.list(), "memory": None},
            base.tools,
            platform.policy_context(req, task),
            None,
            None,  # type: ignore[arg-type]  # the planner never invokes a tool
        )
        cap = CapturingClient(client)
        result = await PlannerAgent(cap).execute(ctx)
        raw = _reply(cap.last("planning"))
        steps = raw.get("steps") if isinstance(raw, dict) else None
        planned = {
            str(s.get("tool_id"))
            for s in (steps if isinstance(steps, list) else [])
            if isinstance(s, dict) and s.get("type") == "TOOL_CALL"
        }
        required = set(case["required"])
        source = result.output["plan"].generated_by
        rows.append(
            {
                "id": case["id"],
                "planned": sorted(planned),
                "missing": sorted(required - planned),
                "out_of_scope": sorted(planned - allowed),
                "recall_hits": len(planned & required),
                "required": len(required),
                "in_scope": len(planned & allowed),
                "planned_n": len(planned),
                "source": source,
                "repairs": int(result.output.get("repairs", 0)),
                "validation_errors": list(result.output.get("validation_errors", [])),
                "plan_valid": source.startswith("llm:") and not result.output.get("repairs"),
                "codes": [d["code"] for d in result.output.get("degradations", [])],
            }
        )
    n = len(rows)
    return {
        "evidence_class": evidence_class(client),
        "cases_source": CASES_SOURCE,
        "gold_version": gold["version"],
        "cases": n,
        "tool_recall": rate(sum(r["recall_hits"] for r in rows), sum(r["required"] for r in rows)),
        "tool_precision": rate(sum(r["in_scope"] for r in rows), sum(r["planned_n"] for r in rows)),
        "plan_valid": rate(sum(r["plan_valid"] for r in rows), n),
        "repair": rate(sum(r["repairs"] > 0 for r in rows), n),
        "canonical_fallback": rate(sum(r["source"] == "canonical-fallback" for r in rows), n),
        "note": "tool counts are pooled over cases; tools within one plan are not independent, so the interval is indicative",
        "rows": rows,
    }


# ------------------------------------------------------------------------------------ critic


def _ev(eid: str, etype: str, description: str, **attributes: Any) -> dict[str, Any]:
    return {"id": eid, "type": etype, "description": description, "attributes": attributes}


def critic_cases() -> list[dict[str, Any]]:
    """Five batches of five findings; each batch plants three faults and keeps two clean controls.

    Built from a small table so every batch differs in symbol, cause and numbers. ``planted`` is the
    fault kind, or ``None`` for a control.
    """
    table = [
        ("AAPL", "MARKET_VOLATILITY", "volatility_ratio", 2.31, 1.02),
        ("MSFT", "WIDE_SPREADS", "spread_ratio", 1.87, 0.98),
        ("NVDA", "LOW_LIQUIDITY", "depth_ratio", 0.41, 1.05),
        ("AMZN", "TECHNOLOGY_LATENCY", "latency_ratio", 4.6, 1.1),
        ("GOOGL", "VENUE_DEGRADATION", "fill_rate_gap", 0.33, 0.02),
    ]
    batches = []
    for b, (sym, cause, metric, high, normal) in enumerate(table, 1):
        e_calc = f"EV-C{b}-CALC"
        e_mkt = f"EV-C{b}-MKT"
        e_exe = f"EV-C{b}-EXE"
        calc = _ev(e_calc, "CALCULATION", f"attribution for {sym}", **{metric: normal, "is_change_bps": 6.4})
        hot = _ev(e_mkt, "MARKET_DATA", f"{sym} window statistics", **{metric: high})
        exe = _ev(
            e_exe, "EXECUTION_DATA", f"{sym} execution metrics", is_change_bps=6.4, vwap_slippage_bps=2.1
        )
        findings = [
            {  # control: the statement matches the evidence
                "id": f"F{b}-OK1",
                "statement": f"{metric} was {high} in the window",
                "confidence": 0.7,
                "supporting_evidence": [hot],
                "contradicting_evidence": [],
                "evidence_resolved": True,
                "planted": None,
            },
            {  # control
                "id": f"F{b}-OK2",
                "statement": "Implementation shortfall rose by 6.4 bps against the baseline",
                "confidence": 0.8,
                "supporting_evidence": [exe],
                "contradicting_evidence": [],
                "evidence_resolved": True,
                "planted": None,
            },
        ]
        planted = [
            {
                "id": f"F{b}-UNCITED",
                "statement": f"{cause} was the primary cause",
                "confidence": 0.75,
                "supporting_evidence": [],
                "contradicting_evidence": [],
                "evidence_resolved": True,
                "planted": "uncited",
            },
            {
                "id": f"F{b}-UNRESOLVED",
                "statement": f"{cause} explains the deterioration",
                "confidence": 0.7,
                "supporting_evidence": [_ev(f"EV-C{b}-GONE", "CALCULATION", "missing record")],
                "contradicting_evidence": [],
                "evidence_resolved": False,
                "planted": "unresolved",
            },
            {
                "id": f"F{b}-CONTRA",
                "statement": f"{cause} was material in the window",
                "confidence": 0.7,
                "supporting_evidence": [hot],
                "contradicting_evidence": [calc],
                "evidence_resolved": True,
                "planted": "contradicted",
            },
            {
                "id": f"F{b}-NUM",
                "statement": f"{metric} reached {high} in the window",
                "confidence": 0.7,
                "supporting_evidence": [calc],
                "contradicting_evidence": [],
                "evidence_resolved": True,
                "planted": "numeric_mismatch",
            },
            {
                "id": f"F{b}-DIR",
                "statement": "Implementation shortfall improved by 6.4 bps against the baseline",
                "confidence": 0.7,
                "supporting_evidence": [exe],
                "contradicting_evidence": [],
                "evidence_resolved": True,
                "planted": "direction_mismatch",
            },
        ]
        # three planted per batch, rotating through the five kinds so each kind appears three times
        chosen = [planted[(b - 1 + k) % 5] for k in range(3)]
        batches.append({"id": f"CB-{b:02d}", "findings": findings[:1] + chosen + findings[1:]})
    return batches


def critic_request(findings: list[dict[str, Any]]) -> LLMRequest:
    """The critic's model request for ``findings`` (same prompt, lead, data block and schema as the agent)."""
    payload, _ = fit_context({"findings": [{k: v for k, v in f.items() if k != "planted"} for f in findings]})
    prompt = get_prompt("critic")
    return LLMRequest(
        system_prompt=prompt.text,
        messages=[
            {
                "role": "user",
                "content": f"{DATA_BLOCK_NOTE}\n{CRITIC_LEAD}" + data_block("evidence", "findings", payload),
            }
        ],
        purpose="critique",
        metadata={"prompt": prompt.ref()},
        output_model=CritiqueOutput,
    )


async def critic_eval(client: LLMClient) -> dict[str, Any]:
    rows = []
    valid = 0
    batches = critic_cases()
    for batch in batches:
        findings = batch["findings"]
        response = await client.complete(critic_request(findings))
        raw = _reply(response)
        errors = (
            ["no structured reply"]
            if raw is None
            else validate_model(CritiqueOutput, raw)
            + critique_semantic_errors(raw, [f["id"] for f in findings])
        )
        valid += not errors
        by_id = {
            a.get("finding_id"): a for a in ((raw or {}).get("assessments") or []) if isinstance(a, dict)
        }
        for f in findings:
            a = by_id.get(f["id"]) if not errors else None
            try:
                proposed = float(a.get("adjusted_confidence", f["confidence"])) if a else f["confidence"]
            except (TypeError, ValueError):
                proposed = f["confidence"]
            disputed = a is not None and (
                a.get("supported") is False or proposed < f["confidence"] - DETECTION_DROP
            )
            rows.append(
                {"batch": batch["id"], "finding": f["id"], "planted": f["planted"], "disputed": disputed}
            )
    planted = [r for r in rows if r["planted"]]
    controls = [r for r in rows if not r["planted"]]
    kinds = sorted({str(r["planted"]) for r in planted})
    return {
        "evidence_class": evidence_class(client),
        "cases_source": CASES_SOURCE,
        "batches": len(batches),
        "critique_valid": rate(valid, len(batches)),
        "detection": rate(sum(r["disputed"] for r in planted), len(planted)),
        "false_alarm": rate(sum(r["disputed"] for r in controls), len(controls)),
        "detection_by_kind": {
            k: rate(
                sum(r["disputed"] for r in planted if r["planted"] == k),
                sum(r["planted"] == k for r in planted),
            )
            for k in kinds
        },
        "rows": rows,
    }


# ------------------------------------------------------------------------------------ reporter


def _chunk(cid: str, title: str, section: str, text: str) -> dict[str, Any]:
    return {
        "title": title,
        "section": section,
        "excerpt": text[:400],
        "evidence": ["EV-R-KNOW"],
        "chunk_id": cid,
        "document": f"{cid.lower()}.md",
        "version": "1.0",
        "effective_date": "2026-10-01",
        "text": text,
    }


def reporter_cases() -> list[dict[str, Any]]:
    """Four hand-built report fact sets in the shape :meth:`ReportAgent._investigation_facts` produces."""
    escalation = _chunk(
        "CH-ESC",
        "Execution Policy",
        "Escalation",
        "Deterioration of implementation shortfall or VWAP slippage of more than 5 bps against the baseline "
        "over a one-hour window requires a written explanation.",
    )
    latency = _chunk(
        "CH-LAT",
        "Incident Runbook: Execution Path Latency",
        "First response",
        "Compare route_latency_us with the baseline hour; a ratio above 3 confirms a technology cause.",
    )
    venue = _chunk(
        "CH-VEN",
        "Venue Configuration",
        "Known issues",
        "ARCA has experienced intermittent acknowledgement delays during venue-side maintenance windows.",
    )
    specs = [
        (
            "AAPL",
            "MARKET_VOLATILITY",
            {"is_bps": 9.8, "is_change_bps": 6.2, "volatility_ratio": 2.3},
            [escalation],
        ),
        (
            "MSFT",
            "TECHNOLOGY_LATENCY",
            {"is_bps": 7.1, "is_change_bps": 5.4, "latency_ratio": 4.2},
            [latency],
        ),
        (
            "NVDA",
            "VENUE_DEGRADATION",
            {"is_bps": 4.4, "is_change_bps": 3.1, "fill_rate_gap": 0.31},
            [venue, escalation],
        ),
        ("AMZN", "NORMAL", {"is_bps": 1.2, "is_change_bps": 0.4}, []),
    ]
    out = []
    for k, (sym, cause, metrics, policy) in enumerate(specs, 1):
        ev = [f"EV-R{k}-CALC", f"EV-R{k}-EXE", *(["EV-R-KNOW"] if policy else [])]
        statement = (
            f"{sym} implementation shortfall changed by {metrics['is_change_bps']} bps; primary cause {cause}"
        )
        out.append(
            {
                "id": f"RC-{k:02d}",
                "facts": {
                    "title": f"{sym} EXECUTION INVESTIGATION 14:00-15:00 New York",
                    "symbol": sym,
                    "metrics": metrics,
                    "market": {},
                    "attribution": {"primary": cause, "ranked": [{"cause": cause, "score": 0.8}]},
                    "engineering": {},
                    "findings": [
                        {
                            "id": f"F-R{k}",
                            "statement": statement,
                            "confidence": 0.8,
                            "category": "EXECUTION",
                            "evidence": ev[:2],
                            "contradicting": [],
                        }
                    ],
                    "critique": {"overall": "consistent", "contradictions": []},
                    "alternatives": [],
                    "policy_context": policy,
                    "knowledge_corpus_hash": "0" * 64 if policy else None,
                    "evidence_ids": sorted(ev),
                    "hypotheses": {},
                    "prior_cases": [],
                },
            }
        )
    return out


def claims_request(facts: dict[str, Any]) -> LLMRequest:
    """The reporter's claims call for ``facts`` (same system prompt, content and schema as the agent)."""
    return LLMRequest(
        system_prompt=REPORTER_CLAIMS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": report_request_content(facts)}],
        purpose="claims",
        metadata={"prompt": get_prompt("reporter_claims").ref()},
        output_model=ClaimsOutput,
    )


async def _claims(
    client: LLMClient, facts: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    response = await client.complete(claims_request(facts))
    raw = _reply(response)
    try:
        parsed = ClaimsOutput.model_validate(raw)
    except Exception:  # noqa: BLE001 - an unusable claims reply is scored as zero kept claims
        return [], [], False
    kept, dropped = validate_claims([c.model_dump() for c in parsed.claims], facts)
    return kept, dropped, True


async def reporter_eval(client: LLMClient) -> dict[str, Any]:
    cases = reporter_cases()
    kept_n = dropped_n = usable = 0
    caught = planted_n = 0
    cites = verified = 0
    rows = []
    injector = ClaimInjector(client)
    for case in cases:
        facts = case["facts"]
        kept, dropped, ok = await _claims(client, facts)
        usable += ok
        kept_n += len(kept)
        dropped_n += len(dropped)
        _, inj_dropped, _ = await _claims(injector, facts)
        planted_dropped = [c for c in inj_dropped if "[planted:" in str(c.get("text", ""))]
        caught += len(planted_dropped)
        planted_n += len(injector_kinds())
        cited: list[Any] = []
        warnings: list[str] = []
        if facts["policy_context"]:
            response = await client.complete(prose_request(kept, facts))
            cited, warnings = audit_citations(response.citations, facts)
            cites += len(cited) + len(warnings)
            verified += len(cited)
        rows.append(
            {
                "id": case["id"],
                "kept": len(kept),
                "dropped": len(dropped),
                "planted_caught": len(planted_dropped),
                "citations_verified": len(cited),
                "citation_warnings": warnings,
            }
        )
    proposed = kept_n + dropped_n
    return {
        "evidence_class": evidence_class(client),
        "cases_source": CASES_SOURCE,
        "cases": len(cases),
        "claims_usable": rate(usable, len(cases)),
        "faithfulness": rate(kept_n, proposed),
        "claim_drop": rate(dropped_n, proposed),
        "injection_caught": rate(caught, planted_n),
        "citation_verified": rate(verified, cites),
        "note": "injection_caught measures the deterministic claim guardrail, not the model; faithfulness and claim_drop measure the model's own claims",
        "rows": rows,
    }


def injector_kinds() -> list[str]:
    from ceap.eval.clients import INJECTED_CLAIMS

    return [k for k, _ in INJECTED_CLAIMS]


# ------------------------------------------------------------------------------------ all + B3


async def agent_evals(client: LLMClient | None = None, platform: Any = None) -> dict[str, Any]:
    """Planner, critic and reporter evals plus the four pipeline rates, for one client."""
    client = client or MockLLMClient()
    planner = await planner_eval(client, platform)
    critic = await critic_eval(client)
    reporter = await reporter_eval(client)
    return {
        "evidence_class": evidence_class(client),
        "cases_source": CASES_SOURCE,
        "planner": planner,
        "critic": critic,
        "reporter": reporter,
        "rates": {
            "plan_valid": planner["plan_valid"],
            "repair": planner["repair"],
            "claim_drop": reporter["claim_drop"],
            "citation_verified": reporter["citation_verified"],
        },
    }


# metric -> (path into agent_evals output, the degraded client that must move it, direction)
SENSITIVITY: tuple[tuple[str, tuple[str, str], Any, str], ...] = (
    ("planner tool_recall", ("planner", "tool_recall"), NarrowPlanner, "down"),
    ("planner tool_precision", ("planner", "tool_precision"), NarrowPlanner, "down"),
    ("plan_valid", ("planner", "plan_valid"), InvalidPlanner, "down"),
    ("repair", ("planner", "repair"), InvalidPlanner, "up"),
    ("critic detection", ("critic", "detection"), RubberStampCritic, "down"),
    ("reporter faithfulness", ("reporter", "faithfulness"), ClaimFabricator, "down"),
    ("claim_drop", ("reporter", "claim_drop"), ClaimFabricator, "up"),
    ("citation_verified", ("reporter", "citation_verified"), CitationForger, "down"),
)


async def sensitivity(platform: Any = None) -> dict[str, Any]:
    """B3: every model metric must move in the expected direction under its degraded client."""
    if platform is None:
        from ceap.harness.memory import CaseMemory
        from ceap.platform import Platform

        platform = Platform(llm=MockLLMClient(), case_memory=CaseMemory(enabled=False))
    baseline = await agent_evals(MockLLMClient(), platform)
    rows = []
    cache: dict[Any, dict[str, Any]] = {}
    for metric, (agent, key), degraded, direction in SENSITIVITY:
        if degraded not in cache:
            cache[degraded] = await agent_evals(degraded(MockLLMClient()), platform)
        before = baseline[agent][key]["rate"]
        after = cache[degraded][agent][key]["rate"]
        moved = (
            before is not None
            and after is not None
            and (after < before if direction == "down" else after > before)
        )
        rows.append(
            {
                "metric": metric,
                "degraded_client": degraded.__name__,
                "direction": direction,
                "baseline": before,
                "degraded": after,
                "moved": moved,
            }
        )
    return {
        "evidence_class": "mock",
        "all_moved": all(r["moved"] for r in rows),
        "rows": rows,
        "baseline": baseline,
    }
