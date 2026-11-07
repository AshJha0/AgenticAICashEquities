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
                    flag="OOS_DECAY",
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
        borrow = bt.cost_breakdown_bps_annual.get("borrow")
        if bt.cost_breakdown_bps_annual:
            lines = ", ".join(f"{k} {v:.0f}" for k, v in bt.cost_breakdown_bps_annual.items())
            add(
                f"Execution: decision at the close, fill at the {bt.fill} {bt.execution_lag_days} day(s) later; "
                f"cost lines in bps per year: {lines}"
                + (f" (borrow alone {borrow:.0f})." if finite(borrow) else "."),
                0.85,
                (calc.id,),
                cost_breakdown=dict(bt.cost_breakdown_bps_annual),
            )
        if bt.event_counts:
            counts = ", ".join(f"{k} {v}" for k, v in sorted(bt.event_counts.items()))
            add(
                f"Trades clipped, blocked or adjusted inside the backtest (logged, never silently traded): {counts}.",
                0.85,
                (calc.id,),
                event_counts=dict(bt.event_counts),
            )
        if bt.delisting_bounds:
            b = bt.delisting_bounds
            add(
                "Delisting bounds: full-period net Sharpe "
                + ", ".join(f"{k} {v.get('full_sharpe', float('nan')):.2f}" for k, v in sorted(b.items()))
                + " (merger: last price; performance: -30% NYSE, -55% Nasdaq).",
                0.8,
                (calc.id,),
                delisting_bounds=b,
            )
        prof = bt.profitability or {}
        cap = prof.get("capacity") or {}
        if prof:
            be = prof.get("breakeven_cost_bps_per_unit_turnover")
            fm = prof.get("factor_model") or {}
            aum = cap.get("breakeven_aum")
            half = cap.get("half_sharpe_aum")
            add(
                "Profitability: breakeven trading cost "
                + (f"{be:.1f} bps per unit of turnover" if isinstance(be, float) else "n/a")
                + f"; maximum drawdown {float(prof.get('max_drawdown') or float('nan')) * 100:.1f}% lasting up to "

                + (f"{float(fm['alpha_ann']) * 100:.2f}% a year (t {float(fm['alpha_t']):.2f})" if isinstance(fm.get('alpha_ann'), float) and isinstance(fm.get('alpha_t'), float) else 'n/a')
                + (f"{fm.get('alpha_ann') * 100:.2f}% a year (t {fm.get('alpha_t'):.2f})" if isinstance(fm.get("alpha_ann"), float) and isinstance(fm.get("alpha_t"), float) else "n/a")
                + "; capacity: breakeven AUM "
                + (f"{aum / 1e6:,.0f}M" if isinstance(aum, float) else str(cap.get("breakeven_note") or "n/a"))
                + (f", net Sharpe halves by {half / 1e6:,.0f}M" if isinstance(half, float) else "")
                + ".",
                0.8,
                (calc.id,),
                breakeven_cost_bps=be,
                breakeven_aum=aum,
            )
        if bt.trial:
            add(
                f"Trial ledger: {bt.trial.get('n_trials', 0)} distinct configuration(s) tested on this dataset; "
                "the deflated Sharpe uses this count, which the caller cannot set.",
                0.9,
                (calc.id,),
                n_trials=bt.trial.get("n_trials", 0),
            )
        return AgentResult(
            agent_id=self.id,
            success=True,

            findings=tuple(findings),
            output={"backtest": data, "backtest_evidence_id": calc.id},
            summary=f"net Sharpe in-sample {ins.sharpe:.2f}, out-of-sample {oos.sharpe:.2f}",
        )
