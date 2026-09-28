"""Alpha agent: is the signal predictive, and does the predictive power persist?"""

from __future__ import annotations

from ceap.agents.base import BaseAgent, confidence_from_excess, finite
from ceap.agents.research_base import (
    FAST_DECAY_RATIO,
    MAX_TURNOVER,
    MIN_IC_T_STAT,
    MIN_OOS_IC_T_STAT,
    scope,
    tool_args,
)
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.findings import Finding
from ceap.domain.research import signal_statistics_from_dict


class AlphaAgent(BaseAgent):
    agent_id = "alpha"

    async def execute(self, context: AgentContext) -> AgentResult:
        s = scope(context)
        data, ev = await self.ensure(context, "alpha.evaluate_signal", tool_args(context)["evaluate_signal"])
        stats = signal_statistics_from_dict(data)
        ins, oos = stats.period("in_sample"), stats.period("out_of_sample")
        if ins is None or oos is None:
            return AgentResult(agent_id=self.id, success=False, error="signal statistics lack the in-sample / out-of-sample periods")
        calc = self.calc_evidence(
            context,
            f"Signal statistics for {s.signal}: in-sample IC t-stat {ins.ic_t_stat:.2f}, out-of-sample IC t-stat {oos.ic_t_stat:.2f}",
            {"signal_statistics": data},
            ev,
        )
        findings: list[Finding] = []

        def add(statement: str, conf: float, evidence: tuple[str, ...], **attrs: object) -> None:
            findings.append(
                Finding.create(
                    statement, evidence, conf, category="SIGNAL", produced_by=self.id, attributes=dict(attrs)
                )
            )

        predictive = finite(ins.ic_t_stat) and ins.ic_t_stat >= MIN_IC_T_STAT
        if predictive:
            add(
                f"The {s.signal} signal is predictive in-sample: mean rank IC {ins.mean_ic:.4f} with t-statistic "
                f"{ins.ic_t_stat:.2f} over {ins.n_dates} dates (hit rate {ins.hit_rate * 100:.1f}%).",
                confidence_from_excess(ins.ic_t_stat - MIN_IC_T_STAT, 3.0),
                (calc.id, *ev),
                claim="ALPHA",
                ic_t_stat=ins.ic_t_stat,
            )
            if finite(oos.ic_t_stat) and oos.ic_t_stat >= MIN_OOS_IC_T_STAT:
                add(
                    f"Predictive power persists out-of-sample: IC t-statistic {oos.ic_t_stat:.2f} over {oos.n_dates} dates.",
                    confidence_from_excess(oos.ic_t_stat - MIN_OOS_IC_T_STAT, 3.0),
                    (calc.id, *ev),
                    claim="ROBUST",
                    oos_ic_t_stat=oos.ic_t_stat,
                )
            else:
                add(
                    f"Predictive power fades out-of-sample: IC t-statistic {oos.ic_t_stat:.2f} over {oos.n_dates} dates "
                    f"(below {MIN_OOS_IC_T_STAT:.1f}); the in-sample fit may not persist.",
                    0.85,
                    (calc.id, *ev),
                    flag="OVERFIT",
                    oos_ic_t_stat=oos.ic_t_stat,
                )
        else:
            add(
                f"The {s.signal} signal is not predictive in-sample: mean rank IC {ins.mean_ic:.4f} with t-statistic "
                f"{ins.ic_t_stat:.2f} over {ins.n_dates} dates (threshold {MIN_IC_T_STAT:.1f}).",
                0.85,
                (calc.id, *ev),
                claim="NO_ALPHA",
                ic_t_stat=ins.ic_t_stat,
            )
        if finite(ins.turnover) and ins.turnover > MAX_TURNOVER:
            add(
                f"Rank turnover per rebalance is high ({ins.turnover:.2f}); the signal will be costly to trade.",
                0.8,
                (calc.id,),
                flag="HIGH_TURNOVER",
                turnover=ins.turnover,
            )
        elif finite(ins.turnover):
            add(f"Rank turnover per rebalance is moderate ({ins.turnover:.2f}).", 0.8, (calc.id,), turnover=ins.turnover)
        d1, d21 = ins.decay.get("1", float("nan")), ins.decay.get("21", float("nan"))
        if finite(d1) and finite(d21) and d1 > 0:
            if d21 < FAST_DECAY_RATIO * d1:
                add(
                    f"The IC decays quickly: one-day IC {d1:.4f} versus twenty-one-day IC {d21:.4f}.",
                    0.8,
                    (calc.id,),
                    flag="FAST_DECAY",
                )
            else:
                add(
                    f"The IC persists across horizons: one-day IC {d1:.4f}, twenty-one-day IC {d21:.4f}.",
                    0.8,
                    (calc.id,),
                )
        return AgentResult(
            agent_id=self.id,
            success=True,
            findings=tuple(findings),
            output={"signal_statistics": data, "signal_evidence_id": calc.id, "predictive": predictive},
            summary=f"IC t-stat in-sample {ins.ic_t_stat:.2f}, out-of-sample {oos.ic_t_stat:.2f}",
        )
