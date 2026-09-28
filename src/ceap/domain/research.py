"""Research domain objects: scope, signal statistics, backtests, portfolio risk and the assessment.

Every number in these objects is produced by ``ceap.analytics`` - never by an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ceap.domain.risk import LimitCheck

DEFAULT_SIGNAL = "momentum_12_1"
DEFAULT_REBALANCE_DAYS = 21
DEFAULT_GROSS_NOTIONAL = 50_000_000.0
PERIOD_NAMES: tuple[str, ...] = ("in_sample", "out_of_sample", "full")


class ResearchVerdict(str, Enum):
    PROMOTE = "PROMOTE"
    REJECT = "REJECT"


class ResearchFlag(str, Enum):
    NO_ALPHA = "NO_ALPHA"
    OVERFIT = "OVERFIT"
    COST_DRAG = "COST_DRAG"
    CONCENTRATION = "CONCENTRATION"
    LIMIT_BREACH = "LIMIT_BREACH"
    INCOMPLETE_ANALYSIS = "INCOMPLETE_ANALYSIS"


def _f(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------- scope
@dataclass(frozen=True)
class ResearchScope:
    """The parameters that scope a research task (all written into ``Task.input``)."""

    dataset: str
    signal: str
    start: str
    end: str
    in_sample_end: str
    rebalance_days: int = DEFAULT_REBALANCE_DAYS
    long_short: bool = True
    gross_notional: float = DEFAULT_GROSS_NOTIONAL
    stage_orders: bool = False

    @staticmethod
    def from_task_input(task_input: dict[str, Any]) -> ResearchScope:
        i = task_input
        return ResearchScope(
            dataset=str(i["dataset"]),
            signal=str(i.get("signal") or DEFAULT_SIGNAL),
            start=str(i["start"]),
            end=str(i["end"]),
            in_sample_end=str(i["in_sample_end"]),
            rebalance_days=int(i.get("rebalance_days") or DEFAULT_REBALANCE_DAYS),
            long_short=bool(i.get("long_short", True)),
            gross_notional=float(i.get("gross_notional") or DEFAULT_GROSS_NOTIONAL),
            stage_orders=bool(i.get("stage_orders", False)),
        )


def research_tool_arguments(task_input: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The single source of tool arguments for a research task.

    The canonical plan, every research agent and the harness governance tail
    build their tool requests from this map, so ``BaseAgent.ensure`` always
    finds the plan's cached output and no tool is invoked twice.
    """
    s = ResearchScope.from_task_input(task_input)
    portfolio = {
        "signal": s.signal,
        "dataset": s.dataset,
        "as_of": s.end,
        "long_short": s.long_short,
        "gross_notional": s.gross_notional,
    }
    return {
        "universe_summary": {"dataset": s.dataset, "start": s.start, "end": s.end},
        "evaluate_signal": {
            "signal": s.signal,
            "dataset": s.dataset,
            "start": s.start,
            "end": s.end,
            "in_sample_end": s.in_sample_end,
        },
        "run_backtest": {
            "signal": s.signal,
            "dataset": s.dataset,
            "start": s.start,
            "end": s.end,
            "in_sample_end": s.in_sample_end,
            "rebalance_days": s.rebalance_days,
            "long_short": s.long_short,
            "gross_notional": s.gross_notional,
        },
        "portfolio": portfolio,
        "stage_orders": dict(portfolio),
    }


# ------------------------------------------------------------ signal stats
@dataclass(frozen=True)
class PeriodSignalStats:
    name: str
    start: str
    end: str
    n_dates: int
    mean_ic: float
    ic_std: float
    ic_t_stat: float
    ic_ir: float
    hit_rate: float
    turnover: float
    mean_ic_horizon: float
    quantile_spread_bps_annual: float
    decay: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalStatistics:
    signal: str
    dataset: str
    horizon_days: int
    universe_size: int
    periods: dict[str, PeriodSignalStats]

    def period(self, name: str) -> PeriodSignalStats | None:
        return self.periods.get(name)


# ---------------------------------------------------------------- backtest
@dataclass(frozen=True)
class BacktestPeriodStats:
    name: str
    start: str
    end: str
    n_days: int
    cagr: float
    ann_return: float
    ann_vol: float
    sharpe: float
    sortino: float
    max_drawdown: float
    turnover_annual: float
    hit_rate: float
    gross_sharpe: float
    gross_ann_return: float
    cost_drag_bps_annual: float
    n_rebalances: int


@dataclass(frozen=True)
class BacktestResult:
    signal: str
    dataset: str
    rebalance_days: int
    long_short: bool
    gross_notional: float
    periods: dict[str, BacktestPeriodStats]
    sharpe_ratio_oos_is: float
    equity_curve: tuple[tuple[str, float], ...] = ()
    top_contributors: tuple[tuple[str, float], ...] = ()
    pnl_concentration_top3: float = float("nan")

    def period(self, name: str) -> BacktestPeriodStats | None:
        return self.periods.get(name)


# ---------------------------------------------------------- portfolio risk
@dataclass(frozen=True)
class PortfolioPosition:
    symbol: str
    weight: float
    notional: float
    quantity: int
    price: float
    adv_participation: float


@dataclass(frozen=True)
class PortfolioStressResult:
    shock_bps: float
    pnl_market: float
    pnl_flat: float
    worst_single_name: float


@dataclass(frozen=True)
class PortfolioRiskReport:
    signal: str
    dataset: str
    as_of: str
    long_short: bool
    gross_notional: float
    positions: tuple[PortfolioPosition, ...]
    gross: float
    net: float
    max_abs_weight: float
    hhi: float
    effective_names: float
    beta: float
    max_adv_participation: float
    checks: tuple[LimitCheck, ...] = ()
    stress: PortfolioStressResult | None = None

    @property
    def any_breached(self) -> bool:
        return any(c.breached for c in self.checks)

    @property
    def breached_symbols(self) -> tuple[str, ...]:
        return tuple(sorted({c.symbol for c in self.checks if c.breached}))


# -------------------------------------------------------------- assessment
@dataclass(frozen=True)
class ResearchAssessment:
    verdict: ResearchVerdict
    flags: tuple[ResearchFlag, ...]
    scores: dict[str, float]
    rationale: tuple[str, ...]
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "flags": [f.value for f in self.flags],
            "scores": dict(self.scores),
            "rationale": list(self.rationale),
            "metrics": dict(self.metrics),
        }


# ------------------------------------------------------------ from_dict
def period_signal_stats_from_dict(d: dict[str, Any]) -> PeriodSignalStats:
    return PeriodSignalStats(
        name=str(d.get("name", "")),
        start=str(d.get("start", "")),
        end=str(d.get("end", "")),
        n_dates=int(d.get("n_dates", 0) or 0),
        mean_ic=_f(d.get("mean_ic")),
        ic_std=_f(d.get("ic_std")),
        ic_t_stat=_f(d.get("ic_t_stat")),
        ic_ir=_f(d.get("ic_ir")),
        hit_rate=_f(d.get("hit_rate")),
        turnover=_f(d.get("turnover")),
        mean_ic_horizon=_f(d.get("mean_ic_horizon")),
        quantile_spread_bps_annual=_f(d.get("quantile_spread_bps_annual")),
        decay={str(k): _f(v) for k, v in (d.get("decay") or {}).items()},
    )


def signal_statistics_from_dict(d: dict[str, Any]) -> SignalStatistics:
    return SignalStatistics(
        signal=str(d.get("signal", "")),
        dataset=str(d.get("dataset", "")),
        horizon_days=int(d.get("horizon_days", 0) or 0),
        universe_size=int(d.get("universe_size", 0) or 0),
        periods={str(k): period_signal_stats_from_dict(v) for k, v in (d.get("periods") or {}).items()},
    )


def backtest_period_stats_from_dict(d: dict[str, Any]) -> BacktestPeriodStats:
    return BacktestPeriodStats(
        name=str(d.get("name", "")),
        start=str(d.get("start", "")),
        end=str(d.get("end", "")),
        n_days=int(d.get("n_days", 0) or 0),
        cagr=_f(d.get("cagr")),
        ann_return=_f(d.get("ann_return")),
        ann_vol=_f(d.get("ann_vol")),
        sharpe=_f(d.get("sharpe")),
        sortino=_f(d.get("sortino")),
        max_drawdown=_f(d.get("max_drawdown")),
        turnover_annual=_f(d.get("turnover_annual")),
        hit_rate=_f(d.get("hit_rate")),
        gross_sharpe=_f(d.get("gross_sharpe")),
        gross_ann_return=_f(d.get("gross_ann_return")),
        cost_drag_bps_annual=_f(d.get("cost_drag_bps_annual")),
        n_rebalances=int(d.get("n_rebalances", 0) or 0),
    )


def backtest_result_from_dict(d: dict[str, Any]) -> BacktestResult:
    return BacktestResult(
        signal=str(d.get("signal", "")),
        dataset=str(d.get("dataset", "")),
        rebalance_days=int(d.get("rebalance_days", DEFAULT_REBALANCE_DAYS) or DEFAULT_REBALANCE_DAYS),
        long_short=bool(d.get("long_short", True)),
        gross_notional=_f(d.get("gross_notional"), DEFAULT_GROSS_NOTIONAL),
        periods={str(k): backtest_period_stats_from_dict(v) for k, v in (d.get("periods") or {}).items()},
        sharpe_ratio_oos_is=_f(d.get("sharpe_ratio_oos_is")),
        equity_curve=tuple((str(p[0]), _f(p[1])) for p in (d.get("equity_curve") or []) if len(p) == 2),
        top_contributors=tuple(
            (str(p[0]), _f(p[1])) for p in (d.get("top_contributors") or []) if len(p) == 2
        ),
        pnl_concentration_top3=_f(d.get("pnl_concentration_top3")),
    )


def limit_check_from_dict(d: dict[str, Any]) -> LimitCheck:
    return LimitCheck(
        symbol=str(d.get("symbol", "PORTFOLIO")),
        limit_name=str(d.get("limit_name", "")),
        limit_value=_f(d.get("limit_value")),
        observed_value=_f(d.get("observed_value")),
        breached=bool(d.get("breached", False)),
        utilisation=_f(d.get("utilisation")),
    )


def portfolio_stress_from_dict(d: dict[str, Any] | None) -> PortfolioStressResult | None:
    if not d:
        return None
    return PortfolioStressResult(
        shock_bps=_f(d.get("shock_bps")),
        pnl_market=_f(d.get("pnl_market")),
        pnl_flat=_f(d.get("pnl_flat")),
        worst_single_name=_f(d.get("worst_single_name")),
    )


def portfolio_risk_report_from_dict(d: dict[str, Any]) -> PortfolioRiskReport:
    """Accept either a flat report or the agent's composite ``{exposure, limits, stress}``."""
    exposure: dict[str, Any] = d["exposure"] if isinstance(d.get("exposure"), dict) else d
    checks_src: dict[str, Any] = d["limits"] if isinstance(d.get("limits"), dict) else exposure
    stress_raw = d.get("stress") if isinstance(d.get("stress"), dict) else exposure.get("stress")
    stress_src: dict[str, Any] | None = stress_raw if isinstance(stress_raw, dict) else None
    return PortfolioRiskReport(
        signal=str(exposure.get("signal", "")),
        dataset=str(exposure.get("dataset", "")),
        as_of=str(exposure.get("as_of", "")),
        long_short=bool(exposure.get("long_short", True)),
        gross_notional=_f(exposure.get("gross_notional"), DEFAULT_GROSS_NOTIONAL),
        positions=tuple(
            PortfolioPosition(
                symbol=str(p.get("symbol", "")),
                weight=_f(p.get("weight")),
                notional=_f(p.get("notional")),
                quantity=int(p.get("quantity", 0) or 0),
                price=_f(p.get("price")),
                adv_participation=_f(p.get("adv_participation")),
            )
            for p in (exposure.get("positions") or [])
        ),
        gross=_f(exposure.get("gross")),
        net=_f(exposure.get("net")),
        max_abs_weight=_f(exposure.get("max_abs_weight")),
        hhi=_f(exposure.get("hhi")),
        effective_names=_f(exposure.get("effective_names")),
        beta=_f(exposure.get("beta")),
        max_adv_participation=_f(exposure.get("max_adv_participation")),
        checks=tuple(limit_check_from_dict(c) for c in (checks_src.get("checks") or [])),
        stress=portfolio_stress_from_dict(stress_src),
    )


def research_assessment_from_dict(d: dict[str, Any]) -> ResearchAssessment:
    flags: list[ResearchFlag] = []
    for raw in d.get("flags") or []:
        try:
            flags.append(ResearchFlag(str(raw)))
        except ValueError:
            continue
    try:
        verdict = ResearchVerdict(str(d.get("verdict", "REJECT")))
    except ValueError:
        verdict = ResearchVerdict.REJECT
    return ResearchAssessment(
        verdict=verdict,
        flags=tuple(flags),
        scores={str(k): _f(v) for k, v in (d.get("scores") or {}).items()},
        rationale=tuple(str(r) for r in (d.get("rationale") or [])),
        metrics={str(k): _f(v) for k, v in (d.get("metrics") or {}).items()},
    )
