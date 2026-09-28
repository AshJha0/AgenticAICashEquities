"""Research agent: what is the hypothesis, and does the data cover it?"""

from __future__ import annotations

from typing import Any

from ceap.agents.base import BaseAgent
from ceap.agents.research_base import MIN_COVERAGE_DAYS, describe_scope, scope, tool_args
from ceap.analytics.signals import SIGNALS
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.findings import Finding


class ResearchAgent(BaseAgent):
    agent_id = "research"

    async def execute(self, context: AgentContext) -> AgentResult:
        s = scope(context)
        summary, ev = await self.ensure(context, "research_data.get_universe_summary", tool_args(context)["universe_summary"])
        spec = SIGNALS.get(s.signal)
        description = spec.description if spec else s.signal
        book = "long-short" if s.long_short else "long-only"
        hypothesis = (
            f"{description} ({s.signal}) predicts cross-sectional forward returns in the {summary.get('symbols')}-name "
            f"universe, so a {book} quantile portfolio rebalanced every {s.rebalance_days} trading days earns a "
            "positive net return out-of-sample."
        )
        coverage: dict[str, Any] = dict(summary.get("coverage") or {})
        days = int(coverage.get("trading_days") or 0)
        complete = bool(coverage.get("complete")) and days >= MIN_COVERAGE_DAYS
        coverage["complete"] = complete
        periods = summary.get("periods") or {}
        ins, oos = periods.get("in_sample") or {}, periods.get("out_of_sample") or {}

        findings: list[Finding] = []

        def add(statement: str, conf: float, evidence: tuple[str, ...], **attrs: object) -> None:
            findings.append(
                Finding.create(
                    statement, evidence, conf, category="RESEARCH", produced_by=self.id, attributes=dict(attrs)
                )
            )

        add(f"Hypothesis: {hypothesis}", 0.8, (*ev,), claim="HYPOTHESIS", scope=describe_scope(s))
        if complete:
            add(
                f"Data coverage is complete: {days} trading days for {summary.get('symbols')} names from "
                f"{coverage.get('start')} to {coverage.get('end')}; the in-sample period ends {coverage.get('in_sample_end')}.",
                0.9,
                (*ev,),
                coverage="COMPLETE",
            )
        else:
            add(
                f"Data coverage is incomplete: {days} trading days available (minimum {MIN_COVERAGE_DAYS}); "
                "conclusions are provisional.",
                0.9,
                (*ev,),
                flag="DATA_GAP",
            )
        if ins and oos:
            add(
                f"Regime: the equal-weight market returned {_pct(ins.get('market_return_pct'))} in-sample and "
                f"{_pct(oos.get('market_return_pct'))} out-of-sample, with annualised volatility "
                f"{_pct(ins.get('market_ann_vol_pct'))} versus {_pct(oos.get('market_ann_vol_pct'))}.",
                0.85,
                (*ev,),
            )
        add(
            f"Liquidity: mean quoted spread {float(summary.get('mean_spread_bps') or 0):.3f} bps and mean daily "
            f"notional {float(summary.get('mean_adv_notional') or 0):,.2f} USD across the universe.",
            0.85,
            (*ev,),
        )
        return AgentResult(
            agent_id=self.id,
            success=True,
            findings=tuple(findings),
            output={"hypothesis": hypothesis, "universe_summary": summary, "coverage": coverage},
            summary=f"hypothesis stated; coverage {'complete' if complete else 'incomplete'} ({days} days)",
        )


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.3f}%"
