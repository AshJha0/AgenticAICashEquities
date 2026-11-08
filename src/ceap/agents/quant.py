"""Quant agent: what does the data statistically show, and what caused it?

Runs the deterministic attribution model over the window/baseline metrics
gathered by the plan and turns the ranked causes into findings, each
backed by a CALCULATION evidence record that stores the full score table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ceap.agents.base import BaseAgent, finite
from ceap.analytics.attribution import MATERIAL_SCORE, Cause, PeerWindow, attribute_causes
from ceap.data.session import matched_windows
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
        win_d, win_ev = await self.fetched(context, "execution.get_execution_metrics", w.window_args)
        base_d, base_ev = await self.fetched(context, "execution.get_execution_metrics", w.baseline_args)
        mkt_w, mw_ev = await self.fetched(context, "market_data.get_market_statistics", w.window_args)
        mkt_b, mb_ev = await self.fetched(context, "market_data.get_market_statistics", w.baseline_args)
        book_w, bw_ev = await self.fetched(context, "market_data.get_order_book_statistics", w.window_args)
        book_b, bb_ev = await self.fetched(context, "market_data.get_order_book_statistics", w.baseline_args)

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

        peers, peer_ev = await self._matched_baseline(context, w.symbol, w.start, w.end, w.dataset)
        result = attribute_causes(window, baseline, market_w, market_b, engineering, peers=peers)
        source_ev = (*win_ev, *base_ev, *mw_ev, *mb_ev, *bw_ev, *bb_ev, *peer_ev)
        calc = self.calc_evidence(
            context,
            f"Cause attribution: primary={result.primary.value}, IS residual {result.residual_bps:+.1f} bps "
            f"(z {result.residual_z:+.2f} vs pre-trade estimate), IS delta {result.is_delta_bps:+.1f} bps, "
            f"VWAP-slippage delta {result.vwap_slippage_delta_bps:+.1f} bps, baseline {result.baseline_kind}",
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
        if result.basis == "pretrade_residual":
            add(
                f"Execution quality {change}: arrival shortfall {window.arrival_shortfall_bps:+.1f} bps against a pre-trade "
                f"estimate of {window.pretrade_estimate_bps:+.1f} bps, a residual of {result.residual_bps:+.1f} bps "
                f"({result.residual_z:+.2f} standard errors; SE {result.residual_se_bps:.1f} bps).",
                0.9,
                (*win_ev, calc.id),
                deteriorated=result.deteriorated,
                residual_z=result.residual_z,
            )
        else:
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
                f"{role.capitalize()} {noun}: {CAUSE_TEXT[cs.cause]} (score {cs.score:.2f}; {cs.rationale}{_effect_text(cs.effect)}).",
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
                "matched_baseline_windows": len(peers),
                "metrics": {"window": win_d, "baseline": base_d},
                "market": {"window": mkt_w, "baseline": mkt_b, "ratios": _market_ratios(mkt_w, mkt_b)},
                "engineering": engineering
                if finite(engineering.get("latency_ratio"))
                else {"reject_rate": window.reject_rate},
            },
            summary=f"primary={result.primary.value} ({', '.join(c.value for c in result.secondary) or 'no secondary'})",
        )


    async def _matched_baseline(
        self, context: AgentContext, symbol: str, start: str, end: str, dataset: str | None
    ) -> tuple[list[PeerWindow], tuple[str, ...]]:
        """The same clock window on previous sessions (2T.2): metrics and market statistics."""
        try:
            s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
        except ValueError:
            return [], ()
        peers: list[PeerWindow] = []
        evidence: list[str] = []
        for ps, pe in matched_windows(s, e):
            args: dict[str, Any] = {"symbol": symbol, "start": ps.isoformat(), "end": pe.isoformat(), "dataset": dataset}
            m_d, m_ev = await self.ensure(context, "execution.get_execution_metrics", args)
            if not m_d or not m_d.get("execution_count"):
                continue
            k_d, k_ev = await self.ensure(context, "market_data.get_market_statistics", args)
            b_d, b_ev = await self.ensure(context, "market_data.get_order_book_statistics", args)
            market = market_statistics_from_dict(
                {**k_d, "average_displayed_depth": b_d.get("average_displayed_depth", k_d.get("average_displayed_depth"))}
            )
            peers.append(PeerWindow(execution_metrics_from_dict(m_d), market))
            evidence.extend((*m_ev, *k_ev, *b_ev))
        return peers, tuple(evidence)


def _effect_text(effect: dict[str, Any]) -> str:
    if not effect or not finite(effect.get("effect")) or not effect.get("n_peers"):
        return ""
    lo, hi = effect.get("ci") or (None, None)
    if not (finite(lo) and finite(hi)):
        return ""
    return (
        f"; versus {effect['n_peers']} matched peer windows the defining metric differs by {effect['effect']:+.2f} "
        f"(90% CI {lo:+.2f} to {hi:+.2f})"
    )


def _market_ratios(window: dict[str, Any], baseline: dict[str, Any]) -> dict[str, float | None]:
    """Window/baseline ratios against the baseline this agent actually used (matched windows)."""

    def r(key: str) -> float | None:
        a, b = window.get(key), baseline.get(key)
        try:
            return float(a) / float(b) if a is not None and b else None
        except (TypeError, ValueError):
            return None

    return {
        "volatility": r("realised_volatility_bps"),
        "displayed_depth": r("average_displayed_depth"),
        "spread": r("average_spread_bps"),
    }
