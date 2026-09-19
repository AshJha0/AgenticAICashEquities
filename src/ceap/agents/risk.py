"""Risk agent: did the behaviour create unusual exposure or limit usage?"""

from __future__ import annotations

from ceap.agents.base import BaseAgent, finite
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.findings import Finding


class RiskAgent(BaseAgent):
    agent_id = "risk"

    async def execute(self, context: AgentContext) -> AgentResult:
        w = self.window(context)
        metrics, m_ev = await self.ensure(context, "execution.get_execution_metrics", w.window_args)
        qty = int(metrics.get("target_quantity") or 0)
        part = metrics.get("participation_rate")
        limits, l_ev = await self.ensure(
            context,
            "risk.check_limit",
            {
                "symbol": w.symbol,
                "order_quantity": qty,
                "participation_rate": part if finite(part) else None,
                "dataset": w.dataset,
            },
        )
        exposure, e_ev = await self.ensure(
            context, "risk.get_exposure", {"symbol": w.symbol, "as_of": w.end, "dataset": w.dataset}
        )

        findings: list[Finding] = []

        def add(statement: str, conf: float, evidence: tuple[str, ...], **attrs: object) -> None:
            findings.append(
                Finding.create(
                    statement, evidence, conf, category="RISK", produced_by=self.id, attributes=dict(attrs)
                )
            )

        breached = [c for c in limits.get("checks", []) if c.get("breached")]
        if breached:
            names = ", ".join(
                f"{c['limit_name']} ({float(c['observed_value'] or 0):.2f} vs {float(c['limit_value'] or 0):.2f})"
                for c in breached
            )
            add(
                f"The parent order breached trading limits: {names}.",
                0.9,
                (*l_ev, *m_ev),
                anomaly="LIMIT_BREACH",
                breached=[c["limit_name"] for c in breached],
            )
        else:
            util = max((c.get("utilisation", 0.0) for c in limits.get("checks", [])), default=0.0)
            add(
                f"Trading limits were respected (peak utilisation {util:.0%}).",
                0.85,
                (*l_ev, *m_ev),
                utilisation=util,
            )
        if exposure.get("gross_exposure") is not None:
            add(
                f"Post-window gross exposure in {w.symbol} was {exposure['gross_exposure']:,.0f} USD "
                f"({(exposure.get('notional_utilisation') or 0):.0%} of the notional limit).",
                0.85,
                (*e_ev,),
                gross_exposure=exposure["gross_exposure"],
            )

        return AgentResult(
            agent_id=self.id,
            success=True,
            findings=tuple(findings),
            output={"limits": limits, "exposure": exposure, "any_breached": bool(breached)},
            summary="limits breached" if breached else "limits respected",
        )
