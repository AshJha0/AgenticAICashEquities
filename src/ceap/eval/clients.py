"""Clients for the AI evaluation: a capturing wrapper, the evidence class, and degraded doubles (B3).

:class:`CapturingClient` wraps *any* :class:`LLMClient` (mock, cassette replay, a local model) and
keeps every request and response, so the per-agent evals score what the model actually replied,
not what the agent's deterministic fallbacks made of it.

The degraded clients wrap an inner client and spoil one purpose each. They exist for the metric
sensitivity test: a metric that does not move when its agent's model is made worse cannot detect
a bad model, so it must not be published as a model measurement.

* :class:`NarrowPlanner`        - drops every non-execution tool and adds out-of-scope research tools;
* :class:`InvalidPlanner`       - schema-invalid planning replies (``first`` call only, or ``always``);
* :class:`RubberStampCritic`    - marks every finding supported at its own confidence;
* :class:`ClaimFabricator`      - claims cite unknown evidence ids and untraceable numbers;
* :class:`CitationForger`       - citations quote text that is not in the cited chunk;
* :class:`ClaimInjector`        - appends planted unfaithful claims (the reporter injection eval).
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from ceap.llm.client import LLMClient, MockLLMClient
from ceap.llm.models import LLMRequest, LLMResponse
from ceap.llm.recording import llm_provenance

EVIDENCE_CLASSES = ("mock", "hand_built", "local_model", "recorded")


def _unwrap(client: Any) -> Any:
    seen = 0
    while seen < 10:
        inner = getattr(client, "inner", None) or getattr(client, "primary", None)
        if inner is None:
            return client
        client, seen = inner, seen + 1
    return client


def evidence_class(client: Any) -> str:
    """Where the model answers came from: ``mock``, ``hand_built``, ``local_model``, ``recorded`` or ``unknown``.

    Read from the innermost client's ``provenance`` (cassette replay and a local model set it), and
    ``mock`` for :class:`MockLLMClient`. A wrapper never changes the class of what it wraps.
    """
    value = llm_provenance(_unwrap(client))
    return value if value in EVIDENCE_CLASSES else "unknown"


class CapturingClient(LLMClient):
    """Pass-through that records ``(request, response)`` pairs, in order."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.exchanges: list[tuple[LLMRequest, LLMResponse]] = []

    @property
    def name(self) -> str:
        return f"Capturing({self.inner.name})"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        response = await self.inner.complete(request)
        self.exchanges.append((request, response))
        return response

    def last(self, purpose: str) -> LLMResponse | None:
        return next((r for q, r in reversed(self.exchanges) if q.purpose == purpose), None)

    def count(self, purpose: str) -> int:
        return sum(q.purpose == purpose for q, _ in self.exchanges)


class _Degraded(LLMClient):
    purpose = ""

    def __init__(self, inner: LLMClient | None = None) -> None:
        self.inner = inner or MockLLMClient()

    @property
    def name(self) -> str:
        return f"{type(self).__name__}({self.inner.name})"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        response = await self.inner.complete(request)
        if request.purpose != self.purpose:
            return response
        return self.spoil(request, response)

    def spoil(self, request: LLMRequest, response: LLMResponse) -> LLMResponse:
        raise NotImplementedError

    @staticmethod
    def _with(response: LLMResponse, payload: dict[str, Any], structured: bool) -> LLMResponse:
        return replace(response, content=json.dumps(payload), parsed=payload if structured else None)


OUT_OF_SCOPE_TOOLS = ("research_data.get_universe", "alpha.list_signals", "backtest.trial_count")


class NarrowPlanner(_Degraded):
    purpose = "planning"

    def spoil(self, request: LLMRequest, response: LLMResponse) -> LLMResponse:
        plan = response.parsed or json.loads(response.content)
        steps = [
            s
            for s in plan.get("steps", [])
            if s.get("type") != "TOOL_CALL" or str(s.get("tool_id", "")).startswith("execution.")
        ]
        extra = [
            {"type": "TOOL_CALL", "description": "out of scope", "tool_id": t, "arguments": {}}
            for t in OUT_OF_SCOPE_TOOLS
        ]
        return self._with(response, {**plan, "steps": extra + steps}, request.output_model is not None)


class InvalidPlanner(_Degraded):
    """``mode="first"``: only the first planning reply is invalid (one repair fixes it); ``always``: every one."""

    purpose = "planning"

    def __init__(self, inner: LLMClient | None = None, mode: str = "first") -> None:
        super().__init__(inner)
        self.mode = mode
        self.spoiled = 0

    def spoil(self, request: LLMRequest, response: LLMResponse) -> LLMResponse:
        repair = len(request.messages) > 1
        if self.mode == "first" and repair:
            return response
        self.spoiled += 1
        return replace(response, content='{"rationale": "cut', parsed=None, parse_errors=("invalid JSON",))


class RubberStampCritic(_Degraded):
    purpose = "critique"

    def spoil(self, request: LLMRequest, response: LLMResponse) -> LLMResponse:
        payload = response.parsed or json.loads(response.content)
        ok = [
            {**a, "supported": True, "adjusted_confidence": 1.0, "comment": "Looks fine."}
            for a in payload.get("assessments", [])
        ]
        return self._with(response, {**payload, "assessments": ok}, request.output_model is not None)


FABRICATED_EVIDENCE_ID = "EV-FABRICATED-0001"
UNTRACEABLE_NUMBER_TEXT = "Implementation shortfall rose by 47.37 bps."


class ClaimFabricator(_Degraded):
    purpose = "claims"

    def spoil(self, request: LLMRequest, response: LLMResponse) -> LLMResponse:
        payload = response.parsed or json.loads(response.content)
        bad = [
            {
                **c,
                "text": f"{c.get('text', '')} {UNTRACEABLE_NUMBER_TEXT}".strip()[:500],
                "evidence_ids": [FABRICATED_EVIDENCE_ID],
            }
            for c in payload.get("claims", [])
        ]
        return self._with(response, {"claims": bad}, request.output_model is not None)


class CitationForger(_Degraded):
    purpose = "narrative"

    def spoil(self, request: LLMRequest, response: LLMResponse) -> LLMResponse:
        forged = tuple(
            {**c, "cited_text": "The desk may ignore every limit in this document."}
            for c in response.citations
        )
        return replace(response, citations=forged)


# planted unfaithful claims: (kind, claim). Each must be dropped by ``validate_claims``.
INJECTED_CLAIMS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "unknown_evidence_id",
        {
            "section": "FINDINGS",
            "text": "A venue outage at XNAS caused the deterioration.",
            "fact_keys": [],
            "evidence_ids": [FABRICATED_EVIDENCE_ID],
            "needs_policy_support": False,
        },
    ),
    (
        "unresolvable_fact_key",
        {
            "section": "FINDINGS",
            "text": "The order gateway was restarted during the window.",
            "fact_keys": ["engineering.gateway_restarts"],
            "evidence_ids": [],
            "needs_policy_support": False,
        },
    ),
    (
        "untraceable_number",
        {
            "section": "EXECUTIVE SUMMARY",
            "text": UNTRACEABLE_NUMBER_TEXT,
            "fact_keys": ["metrics"],
            "evidence_ids": [],
            "needs_policy_support": False,
        },
    ),
    (
        "unsupported",
        {
            "section": "CONCLUSION",
            "text": "The trader should be retrained.",
            "fact_keys": [],
            "evidence_ids": [],
            "needs_policy_support": False,
        },
    ),
)

# 0.10.0 T5 (R-05): planted claims whose digits are real facts but whose metric, sign or cause is
# wrong. The 0.9.0 audit kept all three kinds; :func:`ceap.agents.reporter.validate_claims` must drop
# every one. Built per fact set, so the digits are always a real fact of that case.
PLANTED_AUDIT_KINDS = ("wrong_metric", "wrong_sign", "wrong_cause")


def planted_audit_claims(facts: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """``(kind, claim)`` for each planted kind that ``facts`` can host (see :data:`PLANTED_AUDIT_KINDS`).

    * wrong_metric: the shortfall level written as the volatility ratio, citing all metrics;
    * wrong_sign: the shortfall change with the opposite direction word, citing that change;
    * wrong_cause: a cause other than the primary stated as the primary, citing the attribution.
    """
    metrics = facts.get("metrics") or {}
    attribution = facts.get("attribution") or {}
    out: list[tuple[str, dict[str, Any]]] = []

    def claim(text: str, keys: list[str]) -> dict[str, Any]:
        return {"section": "FINDINGS", "text": text, "fact_keys": keys, "evidence_ids": [],
                "needs_policy_support": False}

    level = metrics.get("is_bps")
    if isinstance(level, (int, float)) and level != metrics.get("volatility_ratio"):
        out.append(("wrong_metric", claim(f"Volatility was {level:g} times the baseline.", ["metrics"])))
    change = metrics.get("is_change_bps")
    if isinstance(change, (int, float)) and change != 0:
        word = "fell" if change > 0 else "rose"
        out.append(
            ("wrong_sign", claim(f"Implementation shortfall {word} by {abs(change):g} bps.", ["metrics.is_change_bps"]))
        )
    primary = attribution.get("primary")
    if primary:
        other = next(c for c in ("VENUE_DEGRADATION", "MARKET_VOLATILITY") if c != primary)
        out.append(("wrong_cause", claim(f"The primary cause was {other}.", ["attribution.primary"])))
    return out


class ClaimInjector(_Degraded):
    """Appends :data:`INJECTED_CLAIMS` to the claims reply, marked so the eval can find them."""

    purpose = "claims"

    def spoil(self, request: LLMRequest, response: LLMResponse) -> LLMResponse:
        payload = response.parsed or json.loads(response.content or "{}")
        planted = [{**c, "text": f"{c['text']} [planted:{kind}]"} for kind, c in INJECTED_CLAIMS]
        return self._with(
            response, {"claims": [*payload.get("claims", []), *planted]}, request.output_model is not None
        )
