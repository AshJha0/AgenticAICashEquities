"""Research assessment: each flag in isolation, the promote path and tunable thresholds."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ceap.analytics.research_assessment import ResearchThresholds, assess_research
from ceap.domain.research import (
    BacktestPeriodStats,
    BacktestResult,
    PeriodSignalStats,
    PortfolioRiskReport,
    ResearchFlag,
    ResearchVerdict,
    SignalStatistics,
    research_assessment_from_dict,
)
from ceap.domain.risk import LimitCheck


def _signal_period(name: str, t: float = 5.0, n: int = 500) -> PeriodSignalStats:
    return PeriodSignalStats(name, "2023-01-02", "2024-12-31", n, 0.05, 0.2, t, 0.25, 0.6, 0.3, 0.04, 800.0, {})


def _stats(is_t: float = 5.0, oos_t: float = 4.0, n: int = 500) -> SignalStatistics:
    return SignalStatistics(
        "momentum_12_1",
        "R01",
        21,
        30,
        {
            "in_sample": _signal_period("in_sample", is_t, n),
            "out_of_sample": _signal_period("out_of_sample", oos_t, 250),
            "full": _signal_period("full", is_t, n + 250),
        },
    )


def _bt_period(name: str, sharpe: float, gross_sharpe: float, gross_ann: float = 0.2, cost: float = 200.0):
    return BacktestPeriodStats(
        name, "2023-01-02", "2024-12-31", 500, 0.18, 0.18, 0.06, sharpe, sharpe * 1.3, 0.08, 3.0, 0.55, gross_sharpe,
        gross_ann, cost, 24,
    )


def _backtest(
    is_sharpe: float = 3.0,
    oos_sharpe: float = 2.5,
    oos_gross: float = 2.7,
    gross_ann: float = 0.2,
    cost: float = 200.0,
    concentration: float = 0.3,
) -> BacktestResult:
    ratio = oos_sharpe / is_sharpe if is_sharpe > 0 else float("nan")
    return BacktestResult(
        "momentum_12_1",
        "R01",
        21,
        True,
        5e7,
        {
            "in_sample": _bt_period("in_sample", is_sharpe, is_sharpe + 0.2, gross_ann, cost),
            "out_of_sample": _bt_period("out_of_sample", oos_sharpe, oos_gross, gross_ann, cost),
            "full": _bt_period("full", (is_sharpe + oos_sharpe) / 2, oos_gross, gross_ann, cost),
        },
        ratio,
        (),
        (),
        concentration,
    )


def _risk(breached: bool = False) -> PortfolioRiskReport:
    checks = (LimitCheck("PORTFOLIO", "max_gross", 1.05, 1.5 if breached else 1.0, breached, 1.4 if breached else 0.95),)
    return PortfolioRiskReport("momentum_12_1", "R01", "2024-12-31", True, 5e7, (), 1.0, 0.0, 0.05, 0.05, 20.0, 0.1, 0.03, checks)


def test_promote_when_every_hurdle_is_cleared():
    a = assess_research(_stats(), _backtest(), _risk())
    assert a.verdict is ResearchVerdict.PROMOTE and a.flags == ()
    assert all(0.0 <= s <= 1.0 for s in a.scores.values()) and a.scores["limits"] == 1.0
    assert a.metrics["cost_share"] == pytest.approx(0.02 / 0.2)
    assert "limits respected" in a.rationale[-1]


def test_no_alpha_flag():
    assert ResearchFlag.NO_ALPHA in assess_research(_stats(is_t=1.0), _backtest(), _risk()).flags
    assert ResearchFlag.NO_ALPHA in assess_research(_stats(n=10), _backtest(), _risk()).flags
    a = assess_research(_stats(is_t=1.0), _backtest(oos_sharpe=-1.0), _risk())
    assert a.flags == (ResearchFlag.NO_ALPHA,)  # the alpha chain stops at the first failure


def test_overfit_flag():
    a = assess_research(_stats(), _backtest(is_sharpe=3.0, oos_sharpe=0.3, oos_gross=0.4), _risk())
    assert a.flags == (ResearchFlag.OVERFIT,) and a.verdict is ResearchVerdict.REJECT
    assert ResearchFlag.OVERFIT in assess_research(_stats(oos_t=0.2), _backtest(), _risk()).flags
    weak = assess_research(_stats(), _backtest(is_sharpe=0.4, oos_sharpe=0.3, oos_gross=0.35), _risk())
    assert weak.flags == (ResearchFlag.OVERFIT,)


def test_cost_drag_flag():
    a = assess_research(_stats(), _backtest(is_sharpe=0.3, oos_sharpe=0.2, oos_gross=2.5, cost=1500.0), _risk())
    assert a.flags == (ResearchFlag.COST_DRAG,)
    assert a.metrics["cost_share"] == pytest.approx(0.75) and a.scores["cost"] == pytest.approx(0.25)


def test_independent_flags_and_missing_inputs():
    conc = assess_research(_stats(), _backtest(concentration=0.7), _risk())
    assert conc.flags == (ResearchFlag.CONCENTRATION,)
    lim = assess_research(_stats(), _backtest(), _risk(breached=True))
    assert lim.flags == (ResearchFlag.LIMIT_BREACH,) and lim.scores["limits"] == 0.0
    both = assess_research(_stats(is_t=1.0), _backtest(concentration=0.7), _risk(breached=True))
    assert set(both.flags) == {ResearchFlag.NO_ALPHA, ResearchFlag.CONCENTRATION, ResearchFlag.LIMIT_BREACH}
    missing = assess_research(None, _backtest(), _risk())
    assert missing.flags == (ResearchFlag.INCOMPLETE_ANALYSIS,) and "signal" in missing.rationale[0]
    partial = replace(_stats(), periods={"full": _signal_period("full")})
    assert assess_research(partial, _backtest(), _risk()).flags == (ResearchFlag.INCOMPLETE_ANALYSIS,)


def test_thresholds_are_tunable_and_dict_round_trip():
    strict = ResearchThresholds(ic_t_stat_min=10.0)
    assert ResearchFlag.NO_ALPHA in assess_research(_stats(), _backtest(), _risk(), strict).flags
    a = assess_research(_stats(), _backtest(concentration=0.7), _risk(breached=True))
    d = a.to_dict()
    assert d["verdict"] == "REJECT" and set(d["flags"]) == {"CONCENTRATION", "LIMIT_BREACH"}
    back = research_assessment_from_dict(d)
    assert back.verdict is a.verdict and back.flags == a.flags and back.scores == a.scores
