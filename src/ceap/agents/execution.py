"""Execution agent: what happened to our orders?"""

from __future__ import annotations

from typing import Any

from ceap.agents.base import BaseAgent, confidence_from_excess, finite
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.findings import Finding


class ExecutionAgent(BaseAgent):
    agent_id = "execution"

    async def execute(self, context: AgentContext) -> AgentResult:
        w = self.window(context)
        win, win_ev = await self.ensure(context, "execution.get_execution_metrics", w.window_args)
        base, base_ev = await self.ensure(context, "execution.get_execution_metrics", w.baseline_args)
        venues, venue_ev = await self.ensure(context, "execution.get_venue_statistics", w.window_args)
        parents, parent_ev = await self.ensure(context, "execution.get_parent_orders", w.window_args)

        is_w, is_b = win.get("implementation_shortfall_bps"), base.get("implementation_shortfall_bps")
        vs_w, vs_b = win.get("slippage_vs_vwap_bps"), base.get("slippage_vs_vwap_bps")
        is_delta = (is_w - is_b) if finite(is_w) and finite(is_b) else float("nan")
        vs_delta = (vs_w - vs_b) if finite(vs_w) and finite(vs_b) else float("nan")

        calc = self.calc_evidence(
            context,
            "Window vs baseline execution deltas (IS, VWAP slippage, fill rate, participation)",
            {
                "is_delta_bps": is_delta,
                "vwap_slippage_delta_bps": vs_delta,
                "fill_rate": win.get("fill_rate"),
                "participation_rate": win.get("participation_rate"),
                "reject_rate": win.get("reject_rate"),
            },
            (*win_ev, *base_ev),
        )
        ev = (*win_ev, *base_ev, calc.id)
        findings: list[Finding] = []

        def add(statement: str, conf: float, evidence: tuple[str, ...], **attrs: object) -> None:
            findings.append(
                Finding.create(
                    statement,
                    evidence,
                    conf,
                    category="EXECUTION",
                    produced_by=self.id,
                    attributes=dict(attrs),
                )
            )

        parent = (parents.get("items") or [None])[0]
        if parent:
            add(
                f"Parent order {parent['order_id']} ({parent['side']} {parent['quantity']:,} {parent['symbol']} via {parent['strategy']}) "
                f"executed {win.get('executed_quantity', 0):,} shares across {win.get('execution_count', 0)} fills on {len(venues.get('venues', {}))} venues.",
                0.9,
                (*parent_ev, *win_ev),
                order_id=parent["order_id"],
            )
        if finite(is_w):
            direction = (
                "increased"
                if finite(is_delta) and is_delta > 0
                else ("decreased" if finite(is_delta) else "changed")
            )
            add(
                f"Implementation shortfall was {is_w:+.1f} bps versus arrival {win['arrival_price']:.4f} "
                f"({direction} by {abs(is_delta):.1f} bps relative to the baseline's {_fmt_bps(is_b)}).",
                0.9 if finite(is_delta) else 0.7,
                ev,
                is_bps=is_w,
                is_delta_bps=is_delta,
            )
        if finite(vs_w):
            add(
                f"Execution VWAP {win['vwap']:.4f} versus interval VWAP {win['market_vwap']:.4f}: slippage {vs_w:+.1f} bps "
                f"(baseline {_fmt_bps(vs_b)}, delta {_fmt_bps(vs_delta)}).",
                0.9,
                ev,
                vwap_slippage_bps=vs_w,
                vwap_slippage_delta_bps=vs_delta,
            )
        fr, part = win.get("fill_rate"), win.get("participation_rate")
        if finite(fr):
            add(
                f"Fill rate was {fr:.1%} of the parent quantity with participation of {_fmt_pct(part)} of market volume.",
                0.9,
                ev,
                fill_rate=fr,
                participation_rate=part,
            )
        if finite(part) and part > 0.20:
            add(
                f"Participation of {part:.1%} exceeded the 20% VWAP ceiling - the order was large relative to available volume "
                f"(market impact {_fmt_bps(win.get('market_impact_bps'))}).",
                confidence_from_excess(part - 0.20, 0.1),
                ev,
                anomaly="LARGE_ORDER_IMPACT",
                participation_rate=part,
            )
        rr = win.get("reject_rate", 0.0) or 0.0
        if rr > 0.05:
            add(
                f"Child-order reject rate was elevated at {rr:.1%}.",
                confidence_from_excess(rr - 0.05, 0.05),
                ev,
                anomaly="TECHNOLOGY_LATENCY",
                reject_rate=rr,
            )

        # venue outlier analysis (deterministic peer comparison)
        stats = {k: v for k, v in (venues.get("venues") or {}).items() if v.get("child_orders", 0) >= 3}
        worst = None
        for name, v in stats.items():
            peers = [p for k, p in stats.items() if k != name]
            if not peers:
                continue
            finite_fills = [p["fill_rate"] for p in peers if finite(p.get("fill_rate"))]
            peer_fill = sum(finite_fills) / len(finite_fills) if finite_fills else float("nan")
            peer_slip = [p["average_slippage_bps"] for p in peers if finite(p.get("average_slippage_bps"))]
            slip_excess = (
                (v["average_slippage_bps"] - sum(peer_slip) / len(peer_slip))
                if peer_slip and finite(v.get("average_slippage_bps"))
                else 0.0
            )
            fill_gap = (
                (peer_fill - v["fill_rate"]) if finite(v.get("fill_rate")) and finite(peer_fill) else 0.0
            )
            score = max(fill_gap / 0.25, slip_excess / 2.0)
            if worst is None or score > worst[1]:
                worst = (name, score, fill_gap, slip_excess, peer_fill, v)
        venue_calc = self.calc_evidence(
            context,
            "Per-venue peer comparison (fill-rate gap, slippage excess)",
            {
                "worst_venue": worst[0] if worst else None,
                "score": worst[1] if worst else None,
                "fill_gap": worst[2] if worst else None,
                "slippage_excess_bps": worst[3] if worst else None,
            },
            venue_ev,
        )
        if worst and worst[1] >= 1.0:
            name, _, fill_gap, slip_excess, peer_fill, v = worst
            add(
                f"Venue {name} underperformed its peers: fill rate {v['fill_rate']:.0%} vs {peer_fill:.0%} elsewhere and slippage "
                f"{slip_excess:+.1f} bps worse than peers over {v['child_orders']} child orders.",
                confidence_from_excess(worst[1] - 1.0, 1.0),
                (*venue_ev, venue_calc.id),
                anomaly="VENUE_DEGRADATION",
                venue=name,
            )
        elif stats:
            add(
                "No venue was materially worse than its peers on fill rate or slippage.",
                0.75,
                (*venue_ev, venue_calc.id),
            )

        # configuration / policy consistency (deterministic comparison, document-backed)
        config = self.output(
            context, "execution.get_strategy_configuration", symbol=w.symbol, strategy="VWAP"
        )
        knowledge = self.output(context, "knowledge.search_documents")
        if config and finite(part):
            ceiling = config.data.get("max_participation_rate")
            target = config.data.get("target_participation_rate")
            doc_ev = tuple(knowledge.evidence_ids) if knowledge else ()
            if finite(ceiling):
                within = part <= ceiling
                add(
                    f"Observed participation {part:.1%} was {'within' if within else 'above'} the configured VWAP ceiling of {ceiling:.0%} "
                    f"(target {_fmt_pct(target)}) - behaviour {'consistent' if within else 'inconsistent'} with the strategy configuration and runbook.",
                    0.85,
                    (*config.evidence_ids, *doc_ev, *win_ev),
                    participation_rate=part,
                    max_participation_rate=ceiling,
                    consistent_with_configuration=within,
                )

        return AgentResult(
            agent_id=self.id,
            success=True,
            findings=tuple(findings),
            output={
                "window": win,
                "baseline": base,
                "venues": venues.get("venues", {}),
                "is_delta_bps": is_delta,
                "vwap_slippage_delta_bps": vs_delta,
            },
            summary=f"IS {is_w:+.1f} bps (delta {is_delta:+.1f}); fill {fr:.0%}"
            if finite(is_w) and finite(fr)
            else "no execution metrics",
        )


def _fmt_bps(x: Any) -> str:
    return f"{float(x):+.1f} bps" if finite(x) else "n/a"


def _fmt_pct(x: Any) -> str:
    return f"{float(x):.1%}" if finite(x) else "n/a"
