"""Research domain objects: scope, signal statistics, backtests, portfolio risk and the assessment.

Every number in these objects is produced by ``ceap.analytics`` - never by an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ceap.domain.findings import HypothesisRow, HypothesisTable
from ceap.domain.risk import LimitCheck

DEFAULT_SIGNAL = "momentum_12_1"
DEFAULT_REBALANCE_DAYS = 21
DEFAULT_GROSS_NOTIONAL = 50_000_000.0
DEFAULT_EXECUTION_LAG_DAYS = 1
DEFAULT_FILL = "close"
FILLS: tuple[str, ...] = ("close", "open")
PERIOD_NAMES: tuple[str, ...] = ("in_sample", "out_of_sample", "full")

# 2.8: the enumerated search space. Every parameter choice the planner or a caller can make comes from
# here, and every distinct choice is one trial in the ledger. The null sweep (2.10) searches all of it.
SEARCH_SIGNALS: tuple[str, ...] = ("momentum_12_1", "reversal_5", "low_vol_60")
SEARCH_REBALANCE_DAYS: tuple[int, ...] = (5, 21)
SEARCH_LONG_SHORT: tuple[bool, ...] = (True, False)
SEARCH_WEIGHTING: tuple[str, ...] = ("equal", "rank")
SEARCH_TOP_FRACTION = 1 / 3


def search_space() -> tuple[dict[str, Any], ...]:
    """All 24 configurations the agent can run (3 signals x 2 rebalance x 2 book x 2 weighting)."""
    return tuple(
        {"signal": s, "rebalance_days": r, "long_short": ls, "weighting": w}
        for s in SEARCH_SIGNALS
        for r in SEARCH_REBALANCE_DAYS
        for ls in SEARCH_LONG_SHORT
        for w in SEARCH_WEIGHTING
    )


def validate_choice(signal: str, rebalance_days: int, long_short: bool, weighting: str) -> None:
    """Refuse a configuration outside the enumerated search space (2.8)."""
    if signal not in SEARCH_SIGNALS:
        raise ValueError(f"signal {signal!r} is not in the enumerated search space {SEARCH_SIGNALS}")
    if int(rebalance_days) not in SEARCH_REBALANCE_DAYS:
        raise ValueError(f"rebalance_days {rebalance_days} is not in the enumerated set {SEARCH_REBALANCE_DAYS}")
    if weighting not in SEARCH_WEIGHTING:
        raise ValueError(f"weighting {weighting!r} is not in the enumerated set {SEARCH_WEIGHTING}")


class ResearchVerdict(str, Enum):
    PROMOTE = "PROMOTE"
    REJECT = "REJECT"


class ResearchFlag(str, Enum):
    NO_ALPHA = "NO_ALPHA"
    # 0.4.0: OVERFIT renamed OOS_DECAY (nothing is fitted; the out-of-sample edge decayed). The alias keeps
    # ``ResearchFlag.OVERFIT`` importable; persisted "OVERFIT" strings are mapped on read.
    OOS_DECAY = "OOS_DECAY"
    OVERFIT = "OOS_DECAY"
    REGIME_BREAK = "REGIME_BREAK"
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
    execution_lag_days: int = DEFAULT_EXECUTION_LAG_DAYS
    fill: str = DEFAULT_FILL

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
            execution_lag_days=int(
                DEFAULT_EXECUTION_LAG_DAYS if i.get("execution_lag_days") is None else i["execution_lag_days"]
            ),
            fill=str(i.get("fill") or DEFAULT_FILL),
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
            "rebalance_days": s.rebalance_days,
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
            "execution_lag_days": s.execution_lag_days,
            "fill": s.fill,
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
    n_dates: int  # non-overlapping horizon-h IC observations (the gate sample)
    mean_ic: float  # mean non-overlapping horizon-h rank IC
    ic_std: float
    ic_t_stat: float  # gate statistic: t of the non-overlapping horizon-h IC (2.7)
    ic_ir: float  # per holding period
    hit_rate: float
    rank_autocorrelation_turnover: float  # 1 - rank autocorrelation of the signal at the rebalance lag (0..2)
    mean_ic_horizon: float  # mean overlapping (daily-sampled) horizon-h IC, descriptive
    quantile_spread_bps_annual: float
    decay: dict[str, float] = field(default_factory=dict)
    ic_t_stat_nw: float = float("nan")  # Newey-West t of the overlapping horizon-h IC, lag h-1
    ic_ir_annualised: float = float("nan")  # ic_ir * sqrt(252 / h)
    mean_ic_1d: float = float("nan")
    ic_t_stat_1d: float = float("nan")
    horizon_days: int = 1
    execution_lag_days: int = 0

    @property
    def turnover(self) -> float:  # 0.4 name, kept read-only for callers not yet migrated
        return self.rank_autocorrelation_turnover


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
    excess_ann_return: float = float("nan")  # mean daily return over the risk-free rate, annualised
    rf_ann: float = float("nan")
    skew: float = float("nan")  # of daily excess returns (for the deflated Sharpe ratio)
    kurtosis: float = float("nan")  # non-excess (normal = 3)


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
    execution_lag_days: int = DEFAULT_EXECUTION_LAG_DAYS
    fill: str = DEFAULT_FILL
    cost_breakdown_bps_annual: dict[str, float] = field(default_factory=dict)  # spread, impact, borrow, financing
    event_counts: dict[str, int] = field(default_factory=dict)  # caps, SSR blocks, delistings, NaN returns
    events: tuple[dict[str, Any], ...] = ()  # first events of each kind, for the audit trail
    delisting_bounds: dict[str, dict[str, float]] = field(default_factory=dict)
    assumptions: dict[str, Any] = field(default_factory=dict)
    profitability: dict[str, Any] = field(default_factory=dict)
    trial: dict[str, Any] = field(default_factory=dict)  # ledger record of this run (2.8)

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
    pnl_factor: float = float("nan")  # generator-factor shock (2.12)
    pnl_short_squeeze: float = float("nan")  # +20 % on every short
    liquidation_cost: float = float("nan")  # unwinding at 10 % ADV: spread plus square-root impact
    liquidation_days: float = float("nan")


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
    history_checks: tuple[dict[str, Any], ...] = ()  # worst out-of-sample date per limit (2.12)

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
    hypotheses: HypothesisTable | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "verdict": self.verdict.value,
            "flags": [f.value for f in self.flags],
            "scores": dict(self.scores),
            "rationale": list(self.rationale),
            "metrics": dict(self.metrics),
        }
        if self.hypotheses is not None:
            out["hypotheses"] = {
                "trials_counted": self.hypotheses.trials_counted,
                "correction": self.hypotheses.correction,
                "rows": [
                    {"name": r.name, "statistic": r.statistic, "p_value": r.p_value, "n_obs": r.n_obs, "verdict": r.verdict}
                    for r in self.hypotheses.rows
                ],
            }
        return out


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
        rank_autocorrelation_turnover=_f(d.get("rank_autocorrelation_turnover", d.get("turnover"))),
        mean_ic_horizon=_f(d.get("mean_ic_horizon")),
        quantile_spread_bps_annual=_f(d.get("quantile_spread_bps_annual")),
        decay={str(k): _f(v) for k, v in (d.get("decay") or {}).items()},
        ic_t_stat_nw=_f(d.get("ic_t_stat_nw")),
        ic_ir_annualised=_f(d.get("ic_ir_annualised")),
        mean_ic_1d=_f(d.get("mean_ic_1d")),
        ic_t_stat_1d=_f(d.get("ic_t_stat_1d")),
        horizon_days=int(d.get("horizon_days", 1) or 1),
        execution_lag_days=int(d.get("execution_lag_days", 0) or 0),
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
        excess_ann_return=_f(d.get("excess_ann_return")),
        rf_ann=_f(d.get("rf_ann")),
        skew=_f(d.get("skew")),
        kurtosis=_f(d.get("kurtosis")),

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
        execution_lag_days=int(d.get("execution_lag_days", 0) or 0),
        fill=str(d.get("fill") or DEFAULT_FILL),
        cost_breakdown_bps_annual={str(k): _f(v) for k, v in (d.get("cost_breakdown_bps_annual") or {}).items()},
        event_counts={str(k): int(v) for k, v in (d.get("event_counts") or {}).items()},
        events=tuple(dict(e) for e in (d.get("events") or [])),
        delisting_bounds={
            str(k): {str(a): _f(b) for a, b in v.items()} for k, v in (d.get("delisting_bounds") or {}).items()
        },
        assumptions=dict(d.get("assumptions") or {}),
        profitability=dict(d.get("profitability") or {}),
        trial=dict(d.get("trial") or {}),
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
        pnl_factor=_f(d.get("pnl_factor")),
        pnl_short_squeeze=_f(d.get("pnl_short_squeeze")),
        liquidation_cost=_f(d.get("liquidation_cost")),
        liquidation_days=_f(d.get("liquidation_days")),
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
        history_checks=tuple(
            dict(c)
            for c in (d.get("history_checks") or exposure.get("history_checks") or checks_src.get("history_checks") or [])
        ),
    )


def research_assessment_from_dict(d: dict[str, Any]) -> ResearchAssessment:
    flags: list[ResearchFlag] = []
    for raw in d.get("flags") or []:
        try:
            flags.append(ResearchFlag("OOS_DECAY" if str(raw) == "OVERFIT" else str(raw)))
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
        hypotheses=_hypotheses_from_dict(d.get("hypotheses")),
    )


def _hypotheses_from_dict(d: Any) -> HypothesisTable | None:
    if not isinstance(d, dict):
        return None
    rows = tuple(
        HypothesisRow(
            name=str(r.get("name", "")),
            statistic=_f(r.get("statistic")),
            p_value=None if r.get("p_value") is None else _f(r.get("p_value")),
            n_obs=int(r.get("n_obs", 0) or 0),
            verdict=str(r.get("verdict", "UNKNOWN")),
        )
        for r in d.get("rows") or []
    )
    return HypothesisTable(
        rows=rows, trials_counted=int(d.get("trials_counted", 0) or 0), correction=str(d.get("correction", "none"))
    )

