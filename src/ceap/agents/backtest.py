"""Backtest agent: does the strategy survive costs out-of-sample?"""

from __future__ import annotations

from ceap.agents.base import BaseAgent, confidence_from_excess, finite
from ceap.agents.research_base import (
    MAX_COST_SHARE,
    MAX_DRAWDOWN,
    MAX_PNL_CONCENTRATION,
    MIN_OOS_IS_SHARPE_RATIO,
    MIN_SHARPE,
    scope,
    tool_args,
)
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.findings import Finding
from ceap.domain.research import backtest_result_from_dict


class BacktestAgent(BaseAgent):
    agent_id = "backtest"

    async def execute(self, context: AgentContext) -> AgentResult:
        s = scope(context)
        data, ev = await self.ensure(context, "backtest.run_backtest", tool_args(context)["run_backtest"])
        bt = backtest_result_from_dict(data)
        ins, oos, full = bt.period("in_sample"), bt.period("out_of_sample"), bt.period("full")
        if ins is None or oos is None or full is None:
            return AgentResult(agent_id=self.id, success=False, error="backtest lacks the in-sample / out-of-sample periods")
        calc = self.calc_evidence(
            context,
            f"Backtest of {s.signal}: net Sharpe in-sample {ins.sharpe:.2f}, out-of-sample {oos.sharpe:.2f}, "
            f"cost drag {full.cost_drag_bps_annual:.0f} bps per year",
            {"backtest": data},
            ev,
        )
        findings: list[Finding] = []

        def add(statement: str, conf: float, evidence: tuple[str, ...], **attrs: object) -> None:
            findings.append(
                Finding.create(
                    statement, evidence, conf, category="BACKTEST", produced_by=self.id, attributes=dict(attrs)
                )
            )

        if finite(oos.sharpe) and oos.sharpe >= MIN_SHARPE:
            add(
                f"The strategy is profitable out-of-sample: net Sharpe {oos.sharpe:.2f}, CAGR {oos.cagr * 100:.1f}%, "
                f"maximum drawdown {oos.max_drawdown * 100:.1f}% over {oos.n_days} days.",
                confidence_from_excess(oos.sharpe - MIN_SHARPE, 1.5),
                (calc.id, *ev),
                claim="PROFITABLE",
                oos_sharpe=oos.sharpe,
            )
        else:
            add(
                f"The strategy is not profitable out-of-sample: net Sharpe {oos.sharpe:.2f} against a "
                f"{MIN_SHARPE:.2f} hurdle (CAGR {oos.cagr * 100:.1f}%).",
                0.85,
                (calc.id, *ev),
                claim="UNPROFITABLE",
                oos_sharpe=oos.sharpe,
            )
        ratio = bt.sharpe_ratio_oos_is
        if finite(ins.sharpe) and ins.sharpe > 0 and finite(ratio):
            if ratio >= MIN_OOS_IS_SHARPE_RATIO:
                add(
                    f"Out-of-sample performance holds up: the out-of-sample to in-sample Sharpe ratio is {ratio:.2f} "
                    f"(in-sample {ins.sharpe:.2f}, out-of-sample {oos.sharpe:.2f}).",
                    confidence_from_excess(ratio - MIN_OOS_IS_SHARPE_RATIO, 0.5),
                    (calc.id, *ev),
                    claim="ROBUST",
                    sharpe_ratio=ratio,
                )
            else:
                add(
                    f"Out-of-sample performance degrades: the out-of-sample to in-sample Sharpe ratio is {ratio:.2f} "
                    f"(in-sample {ins.sharpe:.2f}, out-of-sample {oos.sharpe:.2f}).",
                    0.85,
                    (calc.id, *ev),
                    flag="OVERFIT",
                    sharpe_ratio=ratio,
                )
        gross = full.gross_ann_return
        cost_share = full.cost_drag_bps_annual / (gross * 10_000.0) if finite(gross) and gross > 0 else float("nan")
        costly = (
            finite(oos.gross_sharpe)
            and oos.gross_sharpe >= MIN_SHARPE
            and ((not finite(oos.sharpe)) or oos.sharpe < MIN_SHARPE or (finite(cost_share) and cost_share >= MAX_COST_SHARE))
        )
        if costly:
            add(
                f"Transaction costs consume the alpha: gross out-of-sample Sharpe {oos.gross_sharpe:.2f} versus net "
                f"{oos.sharpe:.2f}; cost drag {full.cost_drag_bps_annual:.0f} bps per year"
                + (f" ({cost_share * 100:.0f}% of gross return)." if finite(cost_share) else "."),
                0.85,
                (calc.id, *ev),
                flag="COST_DRAG",
                cost_share=cost_share,
            )
        elif finite(cost_share):
            add(
                f"Transaction costs consume {cost_share * 100:.0f}% of gross return ({full.cost_drag_bps_annual:.0f} bps per "
                f"year against gross {gross * 100:.1f}%).",
                0.8,
                (calc.id, *ev),
                cost_share=cost_share,
            )
        if finite(full.max_drawdown) and full.max_drawdown > MAX_DRAWDOWN:
            add(
                f"Maximum drawdown over the full period is {full.max_drawdown * 100:.1f}%, above {MAX_DRAWDOWN * 100:.0f}%.",
                0.8,
                (calc.id,),
                flag="DRAWDOWN",
            )
        conc = bt.pnl_concentration_top3
        if finite(conc) and conc > MAX_PNL_CONCENTRATION:
            add(
                f"P&L is concentrated: the top three names carry {conc * 100:.0f}% of positive P&L "
                f"(limit {MAX_PNL_CONCENTRATION * 100:.0f}%).",
                0.85,
                (calc.id,),
                flag="CONCENTRATION",
                pnl_concentration_top3=conc,
            )
        elif finite(conc):
            add(
                f"P&L is diversified: the top three names carry {conc * 100:.0f}% of positive P&L.",
                0.8,
                (calc.id,),
                pnl_concentration_top3=conc,
            )
        return AgentResult(
            agent_id=self.id,
            success=True,
            findings=tuple(findings),
            output={"backtest": data, "backtest_evidence_id": calc.id},
            summary=f"net Sharpe in-sample {ins.sharpe:.2f}, out-of-sample {oos.sharpe:.2f}",
        )
