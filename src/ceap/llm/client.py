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

from ceap.domain.research import research_tool_arguments
from ceap.llm.models import LLMRequest, LLMResponse

RESEARCH_KIND = "research"


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
    Research tasks (``facts["kind"] == "research"``) get the research plan.
    """
    if str(facts.get("kind") or "") == RESEARCH_KIND:
        return canonical_research_plan(facts)
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


def canonical_research_plan(facts: dict[str, Any]) -> dict[str, Any]:
    """Canonical plan for a signal research task: data -> signal -> backtest -> risk -> agents.

    Tool arguments come from :func:`research_tool_arguments`, the same source the
    research agents use, so every agent finds the plan's cached tool output.
    """
    args = research_tool_arguments(facts)
    signal, dataset = facts.get("signal"), facts.get("dataset")
    steps = [
        {
            "type": "TOOL_CALL",
            "description": "Summarise the research universe, coverage and market regime",
            "tool_id": "research_data.get_universe_summary",
            "arguments": args["universe_summary"],
        },
        {
            "type": "TOOL_CALL",
            "description": "Compute rank-IC statistics of the signal in-sample and out-of-sample",
            "tool_id": "alpha.evaluate_signal",
            "arguments": args["evaluate_signal"],
        },
        {
            "type": "TOOL_CALL",
            "description": "Backtest the signal portfolio with costs and the walk-forward split",
            "tool_id": "backtest.run_backtest",
            "arguments": args["run_backtest"],
        },
        {
            "type": "TOOL_CALL",
            "description": "Recompute the target portfolio's exposure and concentration",
            "tool_id": "risk.get_portfolio_exposure",
            "arguments": args["portfolio"],
        },
        {
            "type": "TOOL_CALL",
            "description": "Check the target portfolio against portfolio limits",
            "tool_id": "risk.check_portfolio_limits",
            "arguments": args["portfolio"],
        },
        {
            "type": "TOOL_CALL",
            "description": "Stress the target portfolio with a market shock",
            "tool_id": "risk.calculate_portfolio_stress",
            "arguments": args["portfolio"],
        },
        {
            "type": "TOOL_CALL",
            "description": "Retrieve the research promotion policy and approval runbook",
            "tool_id": "knowledge.search_documents",
            "arguments": {
                "query": "research promotion criteria out-of-sample degradation turnover cost drag approval policy",
                "k": 4,
            },
        },
        {"type": "AGENT_CALL", "description": "Research agent: hypothesis and data coverage", "agent_id": "research"},
        {"type": "AGENT_CALL", "description": "Alpha agent: is the signal predictive?", "agent_id": "alpha"},
        {"type": "AGENT_CALL", "description": "Backtest agent: does it survive costs out-of-sample?", "agent_id": "backtest"},
        {
            "type": "AGENT_CALL",
            "description": "Portfolio risk agent: exposure, limits and stress",
            "agent_id": "portfolio_risk",
        },
        {"type": "AGENT_CALL", "description": "Critic agent: challenge the findings", "agent_id": "critic"},
        {"type": "VALIDATION", "description": "Validate that every finding cites existing evidence"},
        {"type": "FINALISE", "description": "Produce the research proposal"},
    ]
    return {
        "rationale": (
            f"Evaluate the {signal} signal on dataset {dataset} between {facts.get('start')} and {facts.get('end')} "
            f"with the in-sample period ending {facts.get('in_sample_end')}: measure its rank IC, backtest the "
            "quantile portfolio with transaction costs and a walk-forward split, recompute the target portfolio's "
            "risk, then subject the findings to independent critique before a human decides on promotion."
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


def _num(v: Any, digits: int = 2, scale: float = 1.0, suffix: str = "") -> str:
    """Format a fact number at the fact's own precision (or a plain scaling of it) so the audit can trace it."""
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return "n/a"
    return f"{v * scale + 0.0:.{digits}f}{suffix}"  # + 0.0 turns -0.0 into 0.0


def _money(v: Any) -> str:
    if v is None or not isinstance(v, (int, float)) or v != v:
        return "n/a"
    return f"{v:,.0f} USD"


def _mock_research_narrative(f: dict[str, Any]) -> str:
    scope = f.get("scope") or {}
    assessment = f.get("assessment") or {}
    stats = (f.get("signal_statistics") or {}).get("periods") or {}
    bt = f.get("backtest") or {}
    bt_periods = bt.get("periods") or {}
    risk = f.get("risk") or {}
    exposure = risk.get("exposure") or {}
    limits = risk.get("limits") or {}
    stress = risk.get("stress") or {}
    approval = f.get("approval") or {}
    staging = f.get("staging") or {}
    findings = f.get("findings", [])
    critique = f.get("critique", {})
    verdict = assessment.get("verdict", "REJECT")
    flags = list(assessment.get("flags") or [])
    metrics = assessment.get("metrics") or {}

    lines = [f.get("title", "SIGNAL RESEARCH PROPOSAL"), ""]
    lines += ["EXECUTIVE SUMMARY", ""]
    headline = (
        f"Verdict: {verdict}. In-sample IC t-statistic {_num(metrics.get('is_ic_t_stat'))}, out-of-sample net "
        f"Sharpe {_num(metrics.get('oos_sharpe'))}, costs consume {_num(metrics.get('cost_share'), 0, 100.0, '%')} "
        f"of gross return."
    )
    if flags:
        headline += f" Flags raised: {', '.join(flags)}."
    else:
        headline += " No flags were raised; the signal clears every promotion hurdle."
    lines.append(headline)
    lines += ["", "HYPOTHESIS", "", str(f.get("hypothesis") or "n/a")]
    coverage = f.get("coverage") or {}
    if coverage:
        lines.append(
            f"Universe of {scope.get('universe_size', 'n/a')} names, {coverage.get('trading_days', 'n/a')} trading days "
            f"from {scope.get('start')} to {scope.get('end')}; in-sample period ends {scope.get('in_sample_end')}."
        )
    lines += ["", "SIGNAL STATISTICS", ""]
    for name, label in (("in_sample", "In-sample"), ("out_of_sample", "Out-of-sample"), ("full", "Full period")):
        p = stats.get(name) or {}
        lines.append(
            f"{label}: mean IC {_num(p.get('mean_ic'), 4)}, IC t-stat {_num(p.get('ic_t_stat'))}, "
            f"IR {_num(p.get('ic_ir'))}, hit rate {_num(p.get('hit_rate'), 1, 100.0, '%')}, "
            f"turnover {_num(p.get('turnover'))} over {p.get('n_dates', 'n/a')} dates"
        )
    lines += ["", "BACKTEST", ""]
    for name, label in (("in_sample", "In-sample"), ("out_of_sample", "Out-of-sample"), ("full", "Full period")):
        p = bt_periods.get(name) or {}
        lines.append(
            f"{label}: CAGR {_num(p.get('cagr'), 1, 100.0, '%')}, volatility {_num(p.get('ann_vol'), 1, 100.0, '%')}, "
            f"net Sharpe {_num(p.get('sharpe'))} (gross {_num(p.get('gross_sharpe'))}), "
            f"max drawdown {_num(p.get('max_drawdown'), 1, 100.0, '%')}, cost drag {_num(p.get('cost_drag_bps_annual'), 0)} bps "
            f"per year over {p.get('n_rebalances', 'n/a')} rebalances"
        )
    lines.append(
        f"Out-of-sample to in-sample Sharpe ratio {_num(bt.get('sharpe_ratio_oos_is'))}; top three names carry "
        f"{_num(bt.get('pnl_concentration_top3'), 0, 100.0, '%')} of positive P&L."
    )
    lines += ["", "RISK", ""]
    lines.append(
        f"Target portfolio as of {exposure.get('as_of', 'n/a')}: gross {_num(exposure.get('gross'), 0, 100.0, '%')}, "
        f"net {_num(exposure.get('net'), 0, 100.0, '%')}, max single-name weight "
        f"{_num(exposure.get('max_abs_weight'), 1, 100.0, '%')}, beta {_num(exposure.get('beta'))}, "
        f"max ADV participation {_num(exposure.get('max_adv_participation'), 1, 100.0, '%')}."
    )
    breached = limits.get("breached_symbols") or []
    lines.append(
        "Portfolio limits respected."
        if not limits.get("any_breached")
        else f"Portfolio limits breached: {', '.join(str(b) for b in breached)}."
    )
    if stress:
        lines.append(
            f"Market shock of {_num(stress.get('shock_bps'), 0)} bps: P&L through beta {_money(stress.get('pnl_market'))}, "
            f"through net exposure {_money(stress.get('pnl_flat'))}, worst single name {_money(stress.get('worst_single_name'))}."
        )
    lines += ["", "VERDICT", "", f"{verdict}" + (f" ({', '.join(flags)})" if flags else "")]
    lines += [f"- {r}" for r in (assessment.get("rationale") or [])]
    lines += ["", "PRIMARY OBSERVATIONS", ""]
    for i, fd in enumerate(findings[:8], 1):
        lines.append(f"{i}. {fd.get('statement')} [confidence {fd.get('confidence', 0):.2f}]")
    lines += ["", "ALTERNATIVE EXPLANATIONS", ""]
    alts = f.get("alternatives", [])
    lines += [f"- {a}" for a in alts] if alts else ["- None material beyond the flags above."]
    if critique.get("overall"):
        lines += ["", "CRITIC", "", critique["overall"]]
    lines += ["", "APPROVAL", ""]
    if approval:
        lines.append(
            f"{'Approved' if approval.get('approved') else 'Declined'} by {approval.get('decided_by', 'n/a')}"
            + (f": {approval['comment']}" if approval.get("comment") else ".")
        )
    else:
        lines.append("No approval decision recorded.")
    lines += ["", "STAGED ORDERS", ""]
    if not staging.get("requested"):
        lines.append("Order staging was not requested.")
    elif staging.get("staged"):
        lines.append(
            f"Staged {staging.get('count', 'n/a')} paper orders ({staging.get('staging_id')}): buys "
            f"{_money(staging.get('buy_notional'))}, sells {_money(staging.get('sell_notional'))}."
        )
    else:
        lines.append(f"Orders were not staged: {staging.get('error') or staging.get('status') or 'declined'}.")
    policy = f.get("policy_context", [])
    if policy:
        lines += ["", "POLICY CONTEXT", ""]
        for p in policy:
            lines.append(
                f"- {p.get('title')} / {p.get('section')}: {str(p.get('excerpt', '')).splitlines()[0][:160]}"
            )
    lines += ["", "EVIDENCE", ""]
    lines += [f"- {e}" for e in f.get("evidence_ids", [])[:40]]
    return "\n".join(lines)


def _mock_narrative(f: dict[str, Any]) -> str:
    if f.get("kind") == RESEARCH_KIND:
        return _mock_research_narrative(f)
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
