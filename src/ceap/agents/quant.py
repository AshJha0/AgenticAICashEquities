"""Quant agent: what does the data statistically show, and what caused it?

Runs the deterministic attribution model over the window/baseline metrics
gathered by the plan and turns the ranked causes into findings, each
backed by a CALCULATION evidence record that stores the full score table.
"""

from __future__ import annotations

from ceap.agents.base import BaseAgent, finite
from ceap.analytics.attribution import MATERIAL_SCORE, Cause, attribute_causes
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.findings import Finding
from ceap.domain.serialization import execution_metrics_from_dict, market_statistics_from_dict

CAUSE_TEXT = {
    Cause.MARKET_VOLATILITY: "elevated market volatility",
    Cause.WIDE_SPREADS: "wider quoted spreads",
    Cause.LOW_LIQUIDITY: "reduced displayed liquidity",
    Cause.VENUE_DEGRADATION: "degraded performance at a single venue",
    Cause.TECHNOLOGY_LATENCY: "technology latency / rejects in the execution path",
    Cause.MARKET_DATA_ANOMALY: "a market-data quality anomaly",
    Cause.LARGE_ORDER_IMPACT: "market impact from a large parent order",
    Cause.PRICE_MOVEMENT: "an adverse price move during the window",
    Cause.NORMAL: "no material anomaly",
}


class QuantAgent(BaseAgent):
    agent_id = "quant"

    async def execute(self, context: AgentContext) -> AgentResult:
        w = self.window(context)
        win_d, win_ev = await self.ensure(context, "execution.get_execution_metrics", w.window_args)
        base_d, base_ev = await self.ensure(context, "execution.get_execution_metrics", w.baseline_args)
        mkt_w, mw_ev = await self.ensure(context, "market_data.get_market_statistics", w.window_args)
        mkt_b, mb_ev = await self.ensure(context, "market_data.get_market_statistics", w.baseline_args)
        book_w, bw_ev = await self.ensure(context, "market_data.get_order_book_statistics", w.window_args)
        book_b, bb_ev = await self.ensure(context, "market_data.get_order_book_statistics", w.baseline_args)

        window = execution_metrics_from_dict(win_d)
        baseline = execution_metrics_from_dict(base_d)
        if window.execution_count == 0:
            return AgentResult(
                agent_id=self.id,
                success=True,
                findings=(
                    Finding.create(
                        f"No executions were found for {w.symbol} in the investigation window; attribution is not possible.",
                        (*win_ev,),
                        0.9,
                        category="ATTRIBUTION",
                        produced_by=self.id,
                    ),
                ),
                output={
                    "attribution": {"primary": "NORMAL", "ranked": [], "deteriorated": False},
                    "metrics": {"window": win_d, "baseline": base_d},
                    "market": {"window": mkt_w, "baseline": mkt_b},
                },
                summary="no executions in window",
            )
        market_w = market_statistics_from_dict(
            {
                **mkt_w,
                "average_displayed_depth": book_w.get(
                    "average_displayed_depth", mkt_w.get("average_displayed_depth")
                ),
            }
        )
        market_b = market_statistics_from_dict(
            {
                **mkt_b,
                "average_displayed_depth": book_b.get(
                    "average_displayed_depth", mkt_b.get("average_displayed_depth")
                ),
            }
        )

        eng_out = (context.state.get("agent_outputs") or {}).get("engineering") or {}
        engineering = {"latency_ratio": eng_out.get("latency_ratio"), "reject_rate": window.reject_rate}
        if engineering["latency_ratio"] is None:
            lat = self.output(context, "engineering.get_latency_metrics", service="order-gateway")
            engineering["latency_ratio"] = (lat.data.get("latency_ratio") if lat else None) or float("nan")

        result = attribute_causes(window, baseline, market_w, market_b, engineering)
        source_ev = (*win_ev, *base_ev, *mw_ev, *mb_ev, *bw_ev, *bb_ev)
        calc = self.calc_evidence(
            context,
            f"Cause attribution: primary={result.primary.value}, IS delta {result.is_delta_bps:+.1f} bps, VWAP-slippage delta {result.vwap_slippage_delta_bps:+.1f} bps",
            result.to_dict(),
            source_ev,
        )
        findings: list[Finding] = []

        def add(statement: str, conf: float, evidence: tuple[str, ...], **attrs: object) -> None:
            findings.append(
                Finding.create(
                    statement,
                    evidence,
                    conf,
                    category="ATTRIBUTION",
                    produced_by=self.id,
                    attributes=dict(attrs),
                )
            )

        change = "deteriorated" if result.deteriorated else "did not materially deteriorate"
        add(
            f"Execution quality {change} versus the baseline: implementation shortfall delta {result.is_delta_bps:+.1f} bps, "
            f"slippage-vs-VWAP delta {result.vwap_slippage_delta_bps:+.1f} bps.",
            0.9,
            (*win_ev, *base_ev, calc.id),
            deteriorated=result.deteriorated,
        )
        material = result.material
        if result.primary is Cause.NORMAL:
            add(
                "No candidate cause scored as material; the window is statistically consistent with the baseline.",
                0.8,
                (calc.id, *source_ev),
                cause="NORMAL",
            )
        noun = "cause" if result.deteriorated else "anomaly"
        for rank, cs in enumerate(material):
            role = "primary" if rank == 0 else "contributing"
            conf = 0.55 + 0.4 * cs.score
            add(
                f"{role.capitalize()} {noun}: {CAUSE_TEXT[cs.cause]} (score {cs.score:.2f}; {cs.rationale}).",
                conf,
                (calc.id, *source_ev),
                cause=cs.cause.value,
                score=cs.score,
                role=role,
                anomaly=cs.cause.value,
            )
        # explicitly report the strongest rejected hypotheses so the report can list alternatives
        rejected = [cs for cs in result.ranked if cs.score < MATERIAL_SCORE and cs.score > 0.1]
        for cs in rejected[:2]:
            add(
                f"Considered and not supported: {CAUSE_TEXT[cs.cause]} (score {cs.score:.2f}; {cs.rationale}).",
                0.7,
                (calc.id, *source_ev),
                cause=cs.cause.value,
                score=cs.score,
                role="rejected",
            )

        return AgentResult(
            agent_id=self.id,
            success=True,
            findings=tuple(findings),
            output={
                "attribution": result.to_dict(),
                "attribution_evidence_id": calc.id,
                "metrics": {"window": win_d, "baseline": base_d},
                "market": {"window": mkt_w, "baseline": mkt_b},
                "engineering": engineering
                if finite(engineering.get("latency_ratio"))
                else {"reject_rate": window.reject_rate},
            },
            summary=f"primary={result.primary.value} ({', '.join(c.value for c in result.secondary) or 'no secondary'})",
        )
