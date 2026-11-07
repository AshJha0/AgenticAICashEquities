"""Alpha agent: is the signal predictive, and does the predictive power persist?"""

from __future__ import annotations

from ceap.agents.base import BaseAgent, confidence_from_excess, finite
from ceap.agents.research_base import (
    FAST_DECAY_RATIO,
    MAX_TURNOVER,
    MIN_IC_T_STAT,
    MIN_OOS_IC_T_STAT,
    REGIME_BREAK_T,
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
                f"The {s.signal} signal is predictive in-sample: mean {ins.horizon_days}-day rank IC {ins.mean_ic:.4f} "
                f"with t-statistic {ins.ic_t_stat:.2f} over {ins.n_dates} non-overlapping periods, clearing the "
                f"{MIN_IC_T_STAT:.2f} null-sweep hurdle (Newey-West t on overlapping periods {ins.ic_t_stat_nw:.2f}; "
                f"hit rate {ins.hit_rate * 100:.1f}%).",
                confidence_from_excess(ins.ic_t_stat - MIN_IC_T_STAT, 3.0),
                (calc.id, *ev),
                claim="ALPHA",
                ic_t_stat=ins.ic_t_stat,
            )
            if finite(oos.ic_t_stat) and oos.ic_t_stat <= -REGIME_BREAK_T:
                add(
                    f"The premium reverses out-of-sample: IC t-statistic {oos.ic_t_stat:.2f} over {oos.n_dates} "
                    "non-overlapping periods.",
                    0.85,
                    (calc.id, *ev),
                    flag="REGIME_BREAK",
                    oos_ic_t_stat=oos.ic_t_stat,
                )
            elif finite(oos.ic_t_stat) and oos.ic_t_stat >= MIN_OOS_IC_T_STAT:
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
                    flag="OOS_DECAY",
                    oos_ic_t_stat=oos.ic_t_stat,
                )
        else:
            add(
                f"The {s.signal} signal is not predictive in-sample: mean {ins.horizon_days}-day rank IC "
                f"{ins.mean_ic:.4f} with t-statistic {ins.ic_t_stat:.2f} over {ins.n_dates} non-overlapping periods "
                f"(null-sweep hurdle {MIN_IC_T_STAT:.2f}).",
                0.85,
                (calc.id, *ev),
                claim="NO_ALPHA",
                ic_t_stat=ins.ic_t_stat,
            )
        rat = ins.rank_autocorrelation_turnover
        if finite(rat) and rat > MAX_TURNOVER:
            add(
                f"The signal's ranking changes a lot between rebalances (1 - rank autocorrelation {rat:.2f}); "
                "it will be costly to trade.",
                0.8,
                (calc.id,),
                flag="HIGH_TURNOVER",
                rank_autocorrelation_turnover=rat,
            )
        elif finite(rat):
            add(
                f"The signal's ranking is moderately stable between rebalances (1 - rank autocorrelation {rat:.2f}).",
                0.8,
                (calc.id,),
                rank_autocorrelation_turnover=rat,
            )
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
        combination = None
        if context.task_input.get("combine_signals"):
            combination, cev = await self.ensure(
                context, "alpha.combine_signals", {"dataset": tool_args(context)["evaluate_signal"].get("dataset")}
            )
            oos = combination["out_of_sample"]
            ccalc = self.calc_evidence(
                context,
                f"Ridge combination of the three signals (alpha {combination['alpha']:g}, {combination['n_trials']} ledger trials)",
                {"combination": combination, "label": combination["label"]},
                cev,
            )
            singles = ", ".join(
                f"{k} {v['sharpe_ann']:.2f} (DSR {v['dsr']:.2f})" for k, v in oos.items() if k != "combination"
            )
            add(
                f"Ridge combination out-of-sample: annualised Sharpe {oos['combination']['sharpe_ann']:.2f}, deflated "
                f"Sharpe probability {oos['combination']['dsr']:.2f} over {combination['n_trials']} ledger trials, "
                f"against single signals {singles}; gross of costs, {combination['label']}.",
                0.8,
                (ccalc.id, *cev),
                combination_promote=bool(combination["promote"]),
            )

        return AgentResult(
            agent_id=self.id,
            success=True,
            findings=tuple(findings),
            output={
                "signal_statistics": data,
                "signal_evidence_id": calc.id,
                "predictive": predictive,
                "combination": combination,
            },
            summary=f"IC t-stat in-sample {ins.ic_t_stat:.2f}, out-of-sample {oos.ic_t_stat:.2f}",
        )
