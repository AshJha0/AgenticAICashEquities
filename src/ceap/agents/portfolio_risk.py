"""Portfolio risk agent: is the target portfolio within limits, and what does a shock cost?"""

from __future__ import annotations

from ceap.agents.base import BaseAgent, finite
from ceap.agents.research_base import STRESS_LOSS_FRACTION, tool_args
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.findings import Finding
from ceap.domain.research import portfolio_risk_report_from_dict


class PortfolioRiskAgent(BaseAgent):
    agent_id = "portfolio_risk"

    async def execute(self, context: AgentContext) -> AgentResult:
        args = tool_args(context)["portfolio"]
        exposure, e_ev = await self.ensure(context, "risk.get_portfolio_exposure", args)
        limits, l_ev = await self.ensure(context, "risk.check_portfolio_limits", args)
        stress, s_ev = await self.ensure(context, "risk.calculate_portfolio_stress", args)
        report = portfolio_risk_report_from_dict({"exposure": exposure, "limits": limits, "stress": stress})
        findings: list[Finding] = []

        def add(statement: str, conf: float, evidence: tuple[str, ...], **attrs: object) -> None:
            findings.append(
                Finding.create(
                    statement, evidence, conf, category="RISK", produced_by=self.id, attributes=dict(attrs)
                )
            )

        add(
            f"Target portfolio as of {report.as_of}: {len(report.positions)} names, gross {report.gross * 100:.0f}%, "
            f"net {report.net * 100:.0f}%, maximum single-name weight {report.max_abs_weight * 100:.1f}%, "
            f"beta {report.beta:.2f}, effective names {report.effective_names:.1f}.",
            0.85,
            (*e_ev,),
            names=len(report.positions),
        )
        breached = [c for c in report.checks if c.breached]
        hist_breached = [c for c in report.history_checks if c.get("breached")]
        if hist_breached:
            worst = ", ".join(
                f"{c['limit_name']} on {c['date']} ({c['observed_value']:.3f} vs {c['limit_value']:.3f})"
                for c in hist_breached[:4]
            )
            add(
                f"Portfolio limits breached during the out-of-sample history (worst date per limit): {worst}.",
                0.9,
                (*l_ev, *e_ev),
                flag="LIMIT_BREACH",
                breached=[f"{c['limit_name']}@{c['date']}" for c in hist_breached],
                history_breached=[c["limit_name"] for c in hist_breached],

            )
        if breached:
            names = ", ".join(
                f"{c.limit_name}@{c.symbol} ({c.observed_value:.3f} vs {c.limit_value:.3f})" for c in breached[:6]
            )
            add(
                f"Portfolio limits breached: {names}.",
                0.9,
                (*l_ev, *e_ev),
                flag="LIMIT_BREACH",
                breached=[f"{c.limit_name}@{c.symbol}" for c in breached],
            )
        else:
            util = max((c.utilisation for c in report.checks if finite(c.utilisation)), default=0.0)
            add(
                f"Portfolio limits respected (peak utilisation {util * 100:.0f}%; maximum ADV participation "
                f"{report.max_adv_participation * 100:.1f}%).",
                0.85,
                (*l_ev, *e_ev),
                utilisation=util,
            )
        if report.stress is not None:
            st = report.stress
            loss_fraction = abs(st.pnl_market) / report.gross_notional if report.gross_notional else float("nan")
            statement = (
                f"A {st.shock_bps:.0f} bps market shock moves the portfolio by {st.pnl_market:,.2f} USD through beta "
                f"and {st.pnl_flat:,.2f} USD through net exposure; the worst single name loses {st.worst_single_name:,.2f} USD."
            )
            if finite(loss_fraction) and loss_fraction > STRESS_LOSS_FRACTION:
                add(statement, 0.85, (*s_ev,), flag="STRESS_LOSS", loss_fraction=loss_fraction)
            else:
                add(statement, 0.8, (*s_ev,), loss_fraction=loss_fraction)
            if finite(st.pnl_factor) or finite(st.pnl_short_squeeze) or finite(st.liquidation_cost):
                add(
                    f"Tail scenarios: a reversal of the signal's factor moves the book by {st.pnl_factor:,.2f} USD, a "
                    f"+20% short squeeze by {st.pnl_short_squeeze:,.2f} USD; liquidating at 10% of ADV costs "
                    f"{st.liquidation_cost:,.2f} USD over {st.liquidation_days:.1f} day(s).",
                    0.8,
                    (*s_ev,),
                    pnl_factor=st.pnl_factor,
                    pnl_short_squeeze=st.pnl_short_squeeze,
                    liquidation_cost=st.liquidation_cost,
                )
        return AgentResult(
            agent_id=self.id,
            success=True,
            findings=tuple(findings),
            output={
                "risk": {"exposure": exposure, "limits": limits, "stress": stress},
                "exposure": exposure,
                "limits": limits,
                "stress": stress,
                "any_breached": bool(breached or hist_breached),
            },
            summary="limits breached" if breached or hist_breached else "limits respected",

        )
