"""LLM clients.

* :class:`MockLLMClient` - deterministic, offline. Produces a canonical plan
  for planning requests and a template narrative for report requests from
  the *structured facts* it is given. It exists so the whole platform
  (tests, evaluation, CI) runs without network access or API keys.
* :class:`AnthropicLLMClient` - production adapter over the Anthropic SDK.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections import deque
from typing import Any

from ceap.llm.models import LLMRequest, LLMResponse


class LLMClient(ABC):
    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    @property
    def name(self) -> str:
        return type(self).__name__


def extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of the first JSON object in ``text``."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [fence.group(1)] if fence else []
    start = text.find("{")
    if start >= 0:
        candidates.append(text[start:])
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            # trim trailing garbage progressively
            end = cand.rfind("}")
            while end > 0:
                try:
                    return json.loads(cand[: end + 1])
                except json.JSONDecodeError:
                    end = cand.rfind("}", 0, end)
    return None


class MockLLMClient(LLMClient):
    """Deterministic stand-in used when no model is configured."""

    def __init__(self, model: str = "mock-deterministic-v1", keep_calls: int = 50) -> None:
        self.model = model
        self.calls: deque[LLMRequest] = deque(maxlen=keep_calls)  # bounded: this client is long-lived

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        last = request.messages[-1]["content"] if request.messages else ""
        if request.purpose == "planning":
            # Structured parameters arrive out-of-band; never parse them out of the prose,
            # which contains untrusted user text (a question containing "{}" must not win).
            facts = dict(request.metadata.get("parameters") or {})
            content = json.dumps(canonical_plan(facts))
        elif request.purpose == "narrative":
            content = _mock_narrative(extract_json(last) or {})
        elif request.purpose == "critique":
            content = json.dumps(_mock_critique(extract_json(last) or {}))
        else:
            content = "OK"
        return LLMResponse(
            content=content,
            tool_calls=[],
            input_tokens=len(last) // 4,
            output_tokens=len(content) // 4,
            model=self.model,
            stop_reason="end_turn",
        )


def canonical_plan(facts: dict[str, Any]) -> dict[str, Any]:
    """Canonical plan for an execution-quality investigation.

    Mirrors what a well-prompted model returns for the MVP use case; the
    harness validates it exactly like a model-generated plan. The planner
    also uses it as the fallback when a model returns unparsable output.
    """
    sym = facts.get("symbol", "AAPL")
    ws, we = facts.get("window_start"), facts.get("window_end")
    bs, be = facts.get("baseline_start"), facts.get("baseline_end")
    ds = facts.get("dataset")
    base = {"symbol": sym, "dataset": ds}
    win = {**base, "start": ws, "end": we}
    bl = {**base, "start": bs, "end": be}
    steps = [
        {
            "type": "TOOL_CALL",
            "description": "Retrieve parent orders in the investigation window",
            "tool_id": "execution.get_parent_orders",
            "arguments": win,
        },
        {
            "type": "TOOL_CALL",
            "description": "Retrieve child orders in the investigation window",
            "tool_id": "execution.get_child_orders",
            "arguments": {**win, "limit": 500},
        },
        {
            "type": "TOOL_CALL",
            "description": "Retrieve executions in the investigation window",
            "tool_id": "execution.get_executions",
            "arguments": {**win, "limit": 1000},
        },
        {
            "type": "TOOL_CALL",
            "description": "Retrieve market statistics for the window",
            "tool_id": "market_data.get_market_statistics",
            "arguments": win,
        },
        {
            "type": "TOOL_CALL",
            "description": "Retrieve market statistics for the baseline",
            "tool_id": "market_data.get_market_statistics",
            "arguments": bl,
        },
        {
            "type": "TOOL_CALL",
            "description": "Retrieve order-book depth statistics for the window",
            "tool_id": "market_data.get_order_book_statistics",
            "arguments": win,
        },
        {
            "type": "TOOL_CALL",
            "description": "Retrieve order-book depth statistics for the baseline",
            "tool_id": "market_data.get_order_book_statistics",
            "arguments": bl,
        },
        {
            "type": "TOOL_CALL",
            "description": "Compute execution metrics (arrival, VWAP, IS, slippage) for the window",
            "tool_id": "execution.get_execution_metrics",
            "arguments": win,
        },
        {
            "type": "TOOL_CALL",
            "description": "Compute execution metrics for the baseline",
            "tool_id": "execution.get_execution_metrics",
            "arguments": bl,
        },
        {
            "type": "TOOL_CALL",
            "description": "Analyse venue performance in the window",
            "tool_id": "execution.get_venue_statistics",
            "arguments": win,
        },
        {
            "type": "TOOL_CALL",
            "description": "Check order-gateway latency against baseline",
            "tool_id": "engineering.get_latency_metrics",
            "arguments": {
                "service": "order-gateway",
                "start": ws,
                "end": we,
                "baseline_start": bs,
                "baseline_end": be,
                "dataset": ds,
            },
        },
        {
            "type": "TOOL_CALL",
            "description": "Check deployments around the window",
            "tool_id": "engineering.get_deployments",
            "arguments": {"start": bs, "end": we, "dataset": ds},
        },
        {
            "type": "TOOL_CALL",
            "description": "Search error/warning logs in the window",
            "tool_id": "engineering.search_logs",
            "arguments": {"start": ws, "end": we, "level": "ERROR", "dataset": ds},
        },
        {
            "type": "TOOL_CALL",
            "description": "Retrieve the VWAP strategy configuration in force",
            "tool_id": "execution.get_strategy_configuration",
            "arguments": {"symbol": sym, "strategy": "VWAP"},
        },
        {
            "type": "TOOL_CALL",
            "description": "Retrieve relevant runbook / policy knowledge",
            "tool_id": "knowledge.search_documents",
            "arguments": {
                "query": "VWAP configuration participation ceiling behaviour under wide spreads and low liquidity; escalation policy",
                "k": 4,
            },
        },
        {
            "type": "AGENT_CALL",
            "description": "Market agent: what happened in the market?",
            "agent_id": "market",
        },
        {
            "type": "AGENT_CALL",
            "description": "Execution agent: what happened to our orders?",
            "agent_id": "execution",
        },
        {
            "type": "AGENT_CALL",
            "description": "Engineering agent: did technology behave normally?",
            "agent_id": "engineering",
        },
        {
            "type": "AGENT_CALL",
            "description": "Risk agent: any unusual exposure or limit usage?",
            "agent_id": "risk",
        },
        {
            "type": "AGENT_CALL",
            "description": "Quant agent: statistical comparison and cause attribution",
            "agent_id": "quant",
        },
        {"type": "AGENT_CALL", "description": "Critic agent: challenge the findings", "agent_id": "critic"},
        {"type": "VALIDATION", "description": "Validate that every finding cites existing evidence"},
        {"type": "FINALISE", "description": "Produce the investigation report"},
    ]
    return {
        "rationale": (
            f"Investigate {sym} execution quality between {ws} and {we} by comparing the window with the baseline "
            f"{bs}-{be}: retrieve orders, fills and market data, compute deterministic TCA metrics, analyse venues, "
            "market conditions and technology, then attribute causes and subject the findings to independent critique."
        ),
        "steps": steps,
    }


def _fmt(v: Any, digits: int = 1, suffix: str = "") -> str:
    if v is None:
        return "n/a"
    if isinstance(v, (int, float)):
        if v != v:  # nan
            return "n/a"
        return f"{v:+.{digits}f}{suffix}" if suffix == " bps" else f"{v:.{digits}f}{suffix}"
    return str(v)


def _mock_narrative(f: dict[str, Any]) -> str:
    m = f.get("metrics", {})
    w, b = m.get("window", {}), m.get("baseline", {})
    mk = f.get("market", {})
    mw, mb = mk.get("window", {}), mk.get("baseline", {})
    att = f.get("attribution", {})
    eng = f.get("engineering", {})
    findings = f.get("findings", [])
    critique = f.get("critique", {})
    title = f.get("title", "EXECUTION INVESTIGATION")
    ranked = att.get("ranked", [])
    material = [r for r in ranked if r.get("score", 0) >= 0.35]
    primary = att.get("primary", "NORMAL")

    lines = [title, ""]
    lines += ["EXECUTIVE SUMMARY", ""]
    is_delta = att.get("is_delta_bps")
    vw_delta = att.get("vwap_slippage_delta_bps")
    deteriorated = bool(att.get("deteriorated"))
    contributing = ", ".join(r["cause"].replace("_", " ").lower() for r in material[1:])
    if primary == "NORMAL":
        lines.append(
            f"Execution quality in the window was consistent with the baseline: implementation shortfall moved by "
            f"{_fmt(is_delta, 1, ' bps')} and slippage versus interval VWAP by {_fmt(vw_delta, 1, ' bps')}. "
            "No market, venue or technology anomaly of material size was identified."
        )
    elif deteriorated:
        lines.append(
            f"Execution quality deteriorated: implementation shortfall changed by {_fmt(is_delta, 1, ' bps')} relative to the "
            f"baseline and slippage versus interval VWAP by {_fmt(vw_delta, 1, ' bps')}. The evidence attributes the "
            f"deterioration primarily to {primary.replace('_', ' ').lower()}"
            + (f", with {contributing} as contributing factors." if contributing else ".")
        )
    else:
        lines.append(
            f"Headline execution cost did not deteriorate versus the baseline (implementation shortfall delta "
            f"{_fmt(is_delta, 1, ' bps')}, slippage versus interval VWAP delta {_fmt(vw_delta, 1, ' bps')}); however the "
            f"evidence identifies {primary.replace('_', ' ').lower()} as a material anomaly during the window"
            + (f", alongside {contributing}." if contributing else ".")
            + " The favourable headline is explained by price drift rather than by execution quality."
        )
    lines += ["", "PRIMARY OBSERVATIONS", ""]
    for i, fd in enumerate(findings[:8], 1):
        lines.append(f"{i}. {fd.get('statement')} [confidence {fd.get('confidence', 0):.2f}]")
    lines += ["", "EXECUTION", ""]
    lines += [
        f"VWAP: {_fmt(w.get('vwap'), 4)} (market VWAP {_fmt(w.get('market_vwap'), 4)})",
        f"Arrival price: {_fmt(w.get('arrival_price'), 4)}",
        f"Implementation shortfall: {_fmt(w.get('implementation_shortfall_bps'), 1, ' bps')} (baseline {_fmt(b.get('implementation_shortfall_bps'), 1, ' bps')})",
        f"Slippage vs interval VWAP: {_fmt(w.get('slippage_vs_vwap_bps'), 1, ' bps')} (baseline {_fmt(b.get('slippage_vs_vwap_bps'), 1, ' bps')})",
        f"Average spread: {_fmt(w.get('average_spread_bps'), 2)} bps",
        f"Fill rate: {_fmt((w.get('fill_rate') or 0) * 100, 1)}%",
        f"Participation rate: {_fmt((w.get('participation_rate') or 0) * 100, 1)}%",
        f"Market impact: {_fmt(w.get('market_impact_bps'), 1, ' bps')}",
    ]
    lines += ["", "MARKET CONDITIONS", ""]
    vol_ratio = _ratio(mw.get("realised_volatility_bps"), mb.get("realised_volatility_bps"))
    depth_ratio = _ratio(mw.get("average_displayed_depth"), mb.get("average_displayed_depth"))
    spread_ratio = _ratio(mw.get("average_spread_bps"), mb.get("average_spread_bps"))
    lines += [
        f"Volatility: {_pct(vol_ratio)} vs baseline ({_fmt(mw.get('realised_volatility_bps'), 2)} bps/min)",
        f"Displayed depth: {_pct(depth_ratio)} vs baseline",
        f"Quoted spread: {_pct(spread_ratio)} vs baseline",
        f"Price drift in window: {_fmt(mw.get('price_drift_bps'), 1, ' bps')}",
        f"Stale quotes: {_fmt((mw.get('stale_quote_fraction') or 0) * 100, 1)}%; crossed quotes: {mw.get('crossed_quote_count', 0)}",
    ]
    lines += ["", "TECHNOLOGY", ""]
    lr = eng.get("latency_ratio")
    lines += [
        f"Execution latency: {'Elevated' if (lr or 0) > 3 else 'Normal'} (ratio {_fmt(lr, 2)} vs baseline)",
        f"Reject rate: {'Elevated' if (w.get('reject_rate') or 0) > 0.05 else 'Normal'} ({_fmt((w.get('reject_rate') or 0) * 100, 1)}%)",
        f"Deployments in scope: {eng.get('deployments', 0)}; error logs: {eng.get('error_logs', 0)}",
    ]
    lines += ["", "CONCLUSION", ""]
    if primary == "NORMAL":
        lines.append(
            "The evidence does not indicate a material deterioration attributable to market conditions, venue behaviour or technology."
        )
    else:
        top = material[0] if material else None
        lines.append(
            f"The evidence indicates that {primary.replace('_', ' ').lower()} was the material "
            f"{'contributor to the deterioration' if deteriorated else 'anomaly in the window'} "
            f"({top.get('rationale') if top else ''})."
        )
        if (lr or 0) <= 3 and primary != "TECHNOLOGY_LATENCY":
            lines.append("Technology metrics do not show a corresponding abnormality.")
    lines += ["", "ALTERNATIVE EXPLANATIONS", ""]
    alts = f.get("alternatives", [])
    lines += [f"- {a}" for a in alts] if alts else ["- None material beyond the ranked causes above."]
    policy = f.get("policy_context", [])
    if policy:
        lines += ["", "POLICY CONTEXT", ""]
        for p in policy:
            lines.append(
                f"- {p.get('title')} / {p.get('section')}: {str(p.get('excerpt', '')).splitlines()[0][:160]}"
            )
    if critique.get("overall"):
        lines += ["", "CRITIC", "", critique["overall"]]
    lines += ["", "EVIDENCE", ""]
    lines += [f"- {e}" for e in f.get("evidence_ids", [])[:40]]
    return "\n".join(lines)


def _ratio(a: Any, b: Any) -> float | None:
    try:
        if a is None or b is None or b == 0:
            return None
        return float(a) / float(b)
    except (TypeError, ValueError):
        return None


def _pct(ratio: float | None) -> str:
    if ratio is None or ratio != ratio:
        return "n/a"
    return f"{(ratio - 1) * 100:+.0f}%"


def _mock_critique(facts: dict[str, Any]) -> dict[str, Any]:
    out = []
    for fd in facts.get("findings", []):
        supported = bool(fd.get("supporting_evidence")) and fd.get("evidence_resolved", True)
        out.append(
            {
                "finding_id": fd.get("id"),
                "supported": supported,
                "adjusted_confidence": fd.get("confidence", 0.5) if supported else 0.1,
                "comment": "Cited evidence resolves and metrics are consistent."
                if supported
                else "Cited evidence missing.",
            }
        )
    return {
        "assessments": out,
        "overall": "Findings are consistent with the deterministic evidence; confidence adjustments applied where evidence was thin.",
    }


class AnthropicLLMClient(LLMClient):
    """Adapter over the official Anthropic SDK (``pip install .[llm]``)."""

    def __init__(
        self, api_key: str | None = None, model: str = "claude-sonnet-4-5", max_retries: int = 2
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "anthropic package not installed; pip install 'cash-equities-agentic-platform[llm]'"
            ) from exc
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=max_retries)
        self.model = model

    async def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": request.model or self.model,
            "system": request.system_prompt,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.tools:
            kwargs["tools"] = request.tools
        response = await self._client.messages.create(**kwargs)
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.content:
            if getattr(block, "type", "") == "text":
                text_parts.append(block.text)
            elif getattr(block, "type", "") == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "arguments": block.input})
        return LLMResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
            stop_reason=response.stop_reason,
        )
