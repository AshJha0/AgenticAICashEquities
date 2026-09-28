"""Deterministic research assessment: signal statistics + backtest + risk -> verdict and flags.

The Stage 2 analogue of ``attribution.py``: a scoring model with explicit,
tunable thresholds whose output is recorded as evidence, shown to the human
approver and reported. The LLM never decides the verdict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ceap.domain.research import (
    BacktestResult,
    PortfolioRiskReport,
    ResearchAssessment,
    ResearchFlag,
    ResearchVerdict,
    SignalStatistics,
)


@dataclass(frozen=True)
class ResearchThresholds:
    ic_t_stat_min: float = 2.0
    sharpe_min: float = 0.5
    oos_is_sharpe_ratio_min: float = 0.5
    oos_ic_t_min: float = 1.0
    cost_share_max: float = 0.5
    pnl_concentration_max: float = 0.5
    min_dates: int = 60


DEFAULT_THRESHOLDS = ResearchThresholds()


def _finite(x: float) -> bool:
    return x is not None and math.isfinite(x)


def _sigmoid(x: float, scale: float) -> float:
    if not _finite(x):
        return 0.0
    return 1.0 / (1.0 + math.exp(-x / scale))


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if not _finite(x):
        return lo
    return max(lo, min(hi, x))


def assess_research(
    stats: SignalStatistics | None,
    backtest: BacktestResult | None,
    risk: PortfolioRiskReport | None,
    thresholds: ResearchThresholds | None = None,
) -> ResearchAssessment:
    th = thresholds or DEFAULT_THRESHOLDS
    missing = [n for n, v in (("signal", stats), ("backtest", backtest), ("risk", risk)) if v is None]
    if stats is None or backtest is None or risk is None:
        return ResearchAssessment(
            ResearchVerdict.REJECT,
            (ResearchFlag.INCOMPLETE_ANALYSIS,),
            {"alpha": 0.0, "robustness": 0.0, "cost": 0.0, "diversification": 0.0, "limits": 0.0},
            (f"analysis incomplete: no {', '.join(missing)} result",),
        )
    is_s, oos_s = stats.period("in_sample"), stats.period("out_of_sample")
    is_b, oos_b, full_b = backtest.period("in_sample"), backtest.period("out_of_sample"), backtest.period("full")
    if is_s is None or oos_s is None or is_b is None or oos_b is None or full_b is None:
        return ResearchAssessment(
            ResearchVerdict.REJECT,
            (ResearchFlag.INCOMPLETE_ANALYSIS,),
            {"alpha": 0.0, "robustness": 0.0, "cost": 0.0, "diversification": 0.0, "limits": 0.0},
            ("analysis incomplete: in-sample / out-of-sample periods missing",),
        )

    gross_ann = full_b.gross_ann_return
    cost_ann = full_b.cost_drag_bps_annual / 10_000.0
    if _finite(gross_ann) and gross_ann > 0 and _finite(cost_ann):
        cost_share = cost_ann / gross_ann
    else:
        cost_share = float("inf") if _finite(cost_ann) and cost_ann > 0 else 0.0
    ratio = backtest.sharpe_ratio_oos_is
    concentration = backtest.pnl_concentration_top3
    breached = [c for c in risk.checks if c.breached]

    flags: list[ResearchFlag] = []
    rationale: list[str] = []

    # ---- alpha chain: no alpha -> overfit -> cost drag
    if not _finite(is_s.ic_t_stat) or is_s.n_dates < th.min_dates or is_s.ic_t_stat < th.ic_t_stat_min:
        flags.append(ResearchFlag.NO_ALPHA)
        rationale.append(
            f"in-sample IC t-stat {is_s.ic_t_stat:.2f} over {is_s.n_dates} dates is below {th.ic_t_stat_min:.1f}"
        )
    else:
        overfit_reasons: list[str] = []
        if _finite(is_b.sharpe) and is_b.sharpe > 0 and _finite(ratio) and ratio < th.oos_is_sharpe_ratio_min:
            overfit_reasons.append(
                f"out-of-sample / in-sample Sharpe ratio {ratio:.2f} is below {th.oos_is_sharpe_ratio_min:.2f}"
            )
        if not _finite(oos_s.ic_t_stat) or oos_s.ic_t_stat < th.oos_ic_t_min:
            overfit_reasons.append(f"out-of-sample IC t-stat {oos_s.ic_t_stat:.2f} is below {th.oos_ic_t_min:.1f}")
        cost_drag = (
            _finite(oos_b.gross_sharpe)
            and oos_b.gross_sharpe >= th.sharpe_min
            and ((not _finite(oos_b.sharpe)) or oos_b.sharpe < th.sharpe_min or cost_share >= th.cost_share_max)
        )
        if overfit_reasons:
            flags.append(ResearchFlag.OVERFIT)
            rationale.extend(overfit_reasons)
        elif cost_drag:
            flags.append(ResearchFlag.COST_DRAG)
            rationale.append(
                f"gross out-of-sample Sharpe {oos_b.gross_sharpe:.2f} but net {oos_b.sharpe:.2f}; "
                f"costs consume {cost_share:.0%} of gross return"
            )
        elif not _finite(oos_b.sharpe) or oos_b.sharpe < th.sharpe_min:
            flags.append(ResearchFlag.OVERFIT)
            rationale.append(f"out-of-sample net Sharpe {oos_b.sharpe:.2f} is below the {th.sharpe_min:.2f} hurdle")

    # ---- independent checks
    if _finite(concentration) and concentration > th.pnl_concentration_max:
        flags.append(ResearchFlag.CONCENTRATION)
        rationale.append(
            f"top three names account for {concentration:.0%} of positive P&L (limit {th.pnl_concentration_max:.0%})"
        )
    if breached:
        flags.append(ResearchFlag.LIMIT_BREACH)
        names = ", ".join(f"{c.limit_name}@{c.symbol}" for c in breached[:4])
        rationale.append(f"{len(breached)} portfolio limit check(s) breached: {names}")

    verdict = ResearchVerdict.PROMOTE if not flags else ResearchVerdict.REJECT
    if not flags:
        rationale.append(
            f"in-sample IC t-stat {is_s.ic_t_stat:.2f}, out-of-sample net Sharpe {oos_b.sharpe:.2f}, "
            f"costs {cost_share:.0%} of gross return, limits respected"
        )
    scores = {
        "alpha": round(_sigmoid(is_s.ic_t_stat - th.ic_t_stat_min, 2.0), 3),
        "robustness": round(_clamp(ratio) if _finite(is_b.sharpe) and is_b.sharpe > 0 else 0.0, 3),
        "cost": round(_clamp(1.0 - cost_share), 3),
        "diversification": round(_clamp(1.0 - concentration), 3),
        "limits": 0.0 if breached else 1.0,
    }
    metrics = {
        "is_ic_t_stat": is_s.ic_t_stat,
        "oos_ic_t_stat": oos_s.ic_t_stat,
        "is_sharpe": is_b.sharpe,
        "oos_sharpe": oos_b.sharpe,
        "oos_gross_sharpe": oos_b.gross_sharpe,
        "sharpe_ratio_oos_is": ratio,
        "cost_share": cost_share if _finite(cost_share) else float("nan"),
        "pnl_concentration_top3": concentration,
        "max_adv_participation": risk.max_adv_participation,
        "breached_checks": float(len(breached)),
    }
    return ResearchAssessment(verdict, tuple(flags), scores, tuple(rationale), metrics)
